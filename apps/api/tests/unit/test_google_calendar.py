"""P-S3-JF-02: GoogleCalendarService blocking isolation, retries and invalid_grant.

These are the deterministic contract cases of P-S3-JF-02. The protected cloud leg (a real
Google account) is not executed here: J3.1 scope verification and its authorized test account
are still pending, so no request leaves this process.
"""

import asyncio
import json
import logging
import threading
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import Any, cast

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GoogleCredential
from app.services.google_calendar import (
    GoogleCalendarConflict,
    GoogleCalendarForbidden,
    GoogleCalendarInvalidGrant,
    GoogleCalendarNotFound,
    GoogleCalendarPreconditionFailed,
    GoogleCalendarRateLimited,
    GoogleCalendarResponseError,
    GoogleCalendarService,
    GoogleCalendarTransientError,
    GoogleCalendarUnauthorized,
    disconnect_google_credential,
)

Handler = Callable[[httpx.Request], httpx.Response]


class _Sleeper:
    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, delay: float) -> None:
        self.delays.append(delay)


class _FakeDb:
    def __init__(self, credential: GoogleCredential | None) -> None:
        self.credential = credential
        self.flushed = 0

    async def get(self, model: type[Any], key: Any) -> GoogleCredential | None:
        return self.credential

    async def flush(self) -> None:
        self.flushed += 1


def _service(
    handler: Handler,
    *,
    max_retries: int = 3,
    sleeper: _Sleeper | None = None,
    jitter: Callable[[], float] = lambda: 0.5,
    on_invalid_grant: Callable[[], Awaitable[None]] | None = None,
    max_retry_after_seconds: float = 60.0,
    access_token: str = "test-access-token",  # noqa: S107 - fixture, not a credential
) -> GoogleCalendarService:
    return GoogleCalendarService(
        access_token=access_token,
        base_url="https://calendar.example/v3",
        max_retries=max_retries,
        max_retry_after_seconds=max_retry_after_seconds,
        transport=httpx.MockTransport(handler),
        sleeper=sleeper or _Sleeper(),
        jitter=jitter,
        on_invalid_grant=on_invalid_grant,
    )


def _ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"kind": "calendar#calendar", "id": "primary"})


async def test_blocking_client_runs_outside_the_event_loop() -> None:
    """The sync HTTP client runs in a worker thread and the loop keeps running meanwhile."""
    main_thread = threading.get_ident()
    handler_threads: list[int] = []
    started = threading.Event()
    release = threading.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        handler_threads.append(threading.get_ident())
        started.set()
        assert release.wait(5)
        return _ok(request)

    task = asyncio.create_task(
        _service(handler).request("GET", "/users/me/calendarList")
    )
    while not started.is_set():
        await asyncio.sleep(0.01)
    ticks = 0
    for _ in range(10):
        await asyncio.sleep(0)
        ticks += 1
    assert ticks == 10
    release.set()
    assert await task == {"kind": "calendar#calendar", "id": "primary"}
    assert handler_threads and handler_threads[0] != main_thread


async def test_rate_limit_honours_retry_after() -> None:
    calls: list[int] = []
    sleeper = _Sleeper()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "2"}, json={"error": {}})
        return _ok(request)

    result = await _service(handler, sleeper=sleeper).request(
        "GET", "/calendars/primary/events"
    )

    assert result["id"] == "primary"
    assert len(calls) == 2
    assert sleeper.delays == [2.0]


async def test_rate_limit_honours_http_date_retry_after() -> None:
    when = datetime.now(UTC) + timedelta(seconds=5)
    sleeper = _Sleeper()
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(
                429,
                headers={"Retry-After": format_datetime(when, usegmt=True)},
                json={"error": {}},
            )
        return _ok(request)

    await _service(handler, sleeper=sleeper).request("GET", "/calendars/primary/events")

    assert 3.0 <= sleeper.delays[0] <= 5.0


async def test_retry_after_is_capped() -> None:
    sleeper = _Sleeper()
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "9999"}, json={"error": {}})
        return _ok(request)

    await _service(handler, sleeper=sleeper, max_retry_after_seconds=5).request(
        "GET", "/calendars/primary/events"
    )

    assert sleeper.delays == [5.0]


async def test_exhausted_rate_limit_is_typed() -> None:
    sleeper = _Sleeper()
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(429, headers={"Retry-After": "3"}, json={"error": {}})

    with pytest.raises(GoogleCalendarRateLimited) as failure:
        await _service(handler, max_retries=1, sleeper=sleeper).request(
            "GET", "/calendars/primary/events"
        )

    assert len(calls) == 2
    assert sleeper.delays == [3.0]
    assert failure.value.code == "google_calendar_rate_limited"
    assert failure.value.transient is True
    assert failure.value.details["retry_after"] == 3.0
    assert failure.value.details["attempts"] == 2


async def test_transient_5xx_retries_then_succeeds() -> None:
    calls: list[int] = []
    sleeper = _Sleeper()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(503, json={"error": {"code": 503}})
        return _ok(request)

    result = await _service(handler, sleeper=sleeper).request(
        "GET", "/calendars/primary/events"
    )

    assert result["id"] == "primary"
    assert len(calls) == 3
    assert sleeper.delays == [0.75, 1.5]


async def test_exhausted_5xx_is_a_transient_error() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(500, json={"error": {"code": 500}})

    with pytest.raises(GoogleCalendarTransientError) as failure:
        await _service(handler, max_retries=2).request(
            "GET", "/calendars/primary/events"
        )

    assert len(calls) == 3
    assert failure.value.code == "google_calendar_transient_error"
    assert failure.value.transient is True
    assert failure.value.details["status"] == 500
    assert failure.value.details["attempts"] == 3


async def test_network_errors_retry_and_end_typed() -> None:
    calls: list[int] = []
    sleeper = _Sleeper()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("boom", request=request)
        return _ok(request)

    result = await _service(handler, sleeper=sleeper).request(
        "GET", "/calendars/primary/events"
    )
    assert result["id"] == "primary"
    assert len(calls) == 2

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(GoogleCalendarTransientError) as failure:
        await _service(timeout, max_retries=1).request("GET", "/calendars/primary/events")

    assert failure.value.transient is True
    assert failure.value.details["error"] == "ReadTimeout"


async def test_forbidden_quota_reason_is_retried() -> None:
    calls: list[int] = []
    sleeper = _Sleeper()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(
                403,
                headers={"Retry-After": "1"},
                json={"error": {"code": 403, "errors": [{"reason": "rateLimitExceeded"}]}},
            )
        return _ok(request)

    result = await _service(handler, sleeper=sleeper).request(
        "GET", "/calendars/primary/events"
    )

    assert result["id"] == "primary"
    assert len(calls) == 2
    assert sleeper.delays == [1.0]


async def test_forbidden_without_quota_is_not_retried_or_disconnected() -> None:
    calls: list[int] = []
    sleeper = _Sleeper()
    disconnected: list[bool] = []

    async def on_invalid_grant() -> None:
        disconnected.append(True)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(
            403,
            json={
                "error": {
                    "code": 403,
                    "status": "PERMISSION_DENIED",
                    "errors": [{"reason": "forbidden"}],
                }
            },
        )

    with pytest.raises(GoogleCalendarForbidden) as failure:
        await _service(handler, sleeper=sleeper, on_invalid_grant=on_invalid_grant).request(
            "GET", "/calendars/primary/events"
        )

    assert len(calls) == 1
    assert sleeper.delays == []
    assert disconnected == []
    assert failure.value.transient is False
    assert failure.value.disconnects_integration is False


@pytest.mark.parametrize("status", [400, 401, 403])
async def test_invalid_grant_disconnects_without_retry(status: int) -> None:
    user_id = uuid.uuid4()
    credential = GoogleCredential(
        user_id=user_id, access_token_enc=b"blob", key_version=1, status="connected"
    )
    fake_db = _FakeDb(credential)
    calls: list[int] = []
    sleeper = _Sleeper()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(
            status,
            json={
                "error": "invalid_grant",
                "error_description": "Token has been expired or revoked.",
            },
        )

    async def on_invalid_grant() -> None:
        await disconnect_google_credential(cast(AsyncSession, fake_db), user_id)

    with pytest.raises(GoogleCalendarInvalidGrant) as failure:
        await _service(handler, sleeper=sleeper, on_invalid_grant=on_invalid_grant).request(
            "GET", "/calendars/primary/events"
        )

    assert len(calls) == 1
    assert sleeper.delays == []
    assert credential.status == "disconnected"
    assert fake_db.flushed == 1
    assert failure.value.code == "google_calendar_invalid_grant"
    assert failure.value.transient is False
    assert failure.value.disconnects_integration is True


async def test_invalid_grant_inside_structured_error_disconnects() -> None:
    user_id = uuid.uuid4()
    credential = GoogleCredential(
        user_id=user_id, access_token_enc=b"blob", key_version=1, status="connected"
    )
    fake_db = _FakeDb(credential)
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(
            401,
            json={
                "error": {
                    "code": 401,
                    "status": "UNAUTHENTICATED",
                    "errors": [{"reason": "invalid_grant"}],
                }
            },
        )

    async def on_invalid_grant() -> None:
        await disconnect_google_credential(cast(AsyncSession, fake_db), user_id)

    with pytest.raises(GoogleCalendarInvalidGrant):
        await _service(handler, on_invalid_grant=on_invalid_grant).request(
            "GET", "/calendars/primary/events"
        )

    assert len(calls) == 1
    assert credential.status == "disconnected"


async def test_unauthorized_is_not_retried_and_does_not_disconnect() -> None:
    calls: list[int] = []
    disconnected: list[bool] = []

    async def on_invalid_grant() -> None:
        disconnected.append(True)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(
            401,
            json={
                "error": {
                    "code": 401,
                    "status": "UNAUTHENTICATED",
                    "errors": [{"reason": "authError"}],
                }
            },
        )

    with pytest.raises(GoogleCalendarUnauthorized) as failure:
        await _service(handler, on_invalid_grant=on_invalid_grant).request(
            "GET", "/calendars/primary/events"
        )

    assert len(calls) == 1
    assert disconnected == []
    assert failure.value.transient is False


async def test_conflict_is_not_retried() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(409, json={"error": {"code": 409}})

    with pytest.raises(GoogleCalendarConflict) as failure:
        await _service(handler).request("POST", "/calendars/primary/events")

    assert len(calls) == 1
    assert failure.value.transient is False
    assert failure.value.details["status"] == 409


async def test_not_found_is_not_retried() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(404, json={"error": {"code": 404}})

    with pytest.raises(GoogleCalendarNotFound) as failure:
        await _service(handler).request("GET", "/calendars/missing/events")

    assert len(calls) == 1
    assert failure.value.transient is False


async def test_precondition_failed_is_not_retried() -> None:
    """J3.6: a stale `If-Match` is a terminal conflict, never a retry."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(412, json={"error": {"code": 412}})

    with pytest.raises(GoogleCalendarPreconditionFailed) as failure:
        await _service(handler).request(
            "PATCH",
            "/calendars/primary/events/event-1",
            headers={"If-Match": '"etag-1"'},
        )

    assert len(calls) == 1
    assert failure.value.transient is False
    assert failure.value.details["status"] == 412


async def test_request_forwards_caller_headers_without_exposing_the_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    await _service(handler).request(
        "PATCH", "/calendars/primary/events/event-1", headers={"If-Match": '"etag-1"'}
    )

    assert seen[0].headers["if-match"] == '"etag-1"'
    assert seen[0].headers["authorization"] == "Bearer test-access-token"


@pytest.mark.parametrize("name", ["authorization", "AUTHORIZATION", "Accept"])
async def test_request_rejects_reserved_headers_in_any_case(name: str) -> None:
    """J3.6 fix2: a caller header can never coexist with the bearer token or `Accept`."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    with pytest.raises(ValueError):
        await _service(handler).request(
            "GET",
            "/calendars/primary/events/event-1",
            headers={name: "Bearer attacker"},
        )

    assert seen == []  # rejected before any HTTP call


async def test_request_forwards_method_path_params_and_body() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    result = await _service(handler).request(
        "POST",
        "/calendars/primary/events",
        params={"sendUpdates": "none"},
        json={"summary": "x"},
    )

    assert result == {"ok": True}
    request = seen[0]
    assert request.method == "POST"
    assert (
        str(request.url)
        == "https://calendar.example/v3/calendars/primary/events?sendUpdates=none"
    )
    assert request.headers["authorization"] == "Bearer test-access-token"
    assert json.loads(request.content) == {"summary": "x"}


async def test_invalid_json_response_is_typed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    with pytest.raises(GoogleCalendarResponseError) as failure:
        await _service(handler).request("GET", "/calendars/primary/events")

    assert failure.value.transient is False
    assert failure.value.details["status"] == 200


async def test_no_content_returns_an_empty_mapping() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    assert await _service(handler).request("DELETE", "/calendars/primary/events/1") == {}


async def test_logs_do_not_leak_the_access_token(
    caplog: pytest.LogCaptureFixture,
) -> None:
    token = "super-secret-access-token"
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(503, json={"error": {"code": 503}})
        return _ok(request)

    with caplog.at_level(logging.DEBUG):
        await _service(handler, access_token=token).request(
            "GET", "/calendars/primary/events"
        )

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert caplog.records
    assert token not in logged


async def test_calendar_list_entry_encodes_the_calendar_id_as_one_path_segment() -> None:
    """Calendar IDs are usually e-mail addresses; the `@` must not split the path."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.raw_path.decode())
        return httpx.Response(200, json={"id": "mw@group.calendar.google.com"})

    entry = await _service(handler).get_calendar_list_entry(
        "mw@group.calendar.google.com"
    )

    assert entry == {"id": "mw@group.calendar.google.com"}
    assert seen == ["/v3/users/me/calendarList/mw%40group.calendar.google.com"]

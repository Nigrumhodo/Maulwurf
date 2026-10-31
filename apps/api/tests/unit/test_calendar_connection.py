"""I-S3-JF-06 (J3.3): incremental OAuth scopes and the single "Maulwurf" calendar.

Deterministic cases: no request leaves the process (the Calendar API is an
`httpx.MockTransport` double). The protected cloud leg (P-S3-JF-01/J3.1) is still pending,
so a mock here proves the contract logic, never Google's real behavior.

Covered:
- `connect` asks only for Calendar capabilities the recorded grant lacks and `reconnect`
  asks for the whole set; the login scopes are never re-requested.
- A token response without the Calendar scopes is a clean rejection.
- The calendar is created once with a stable identity (summary + description marker).
- An ambiguous create (transient/5xx/unparseable/409) is reconciled by identity BEFORE
  retrying, so a lost response never produces a second calendar.
- Multiple matches from an earlier ambiguous creation pin one deterministic ID and never
  delete a remote calendar.
- The stored ID wins while it still exists; a deleted stored calendar is reconciled again.
"""

import json
from collections.abc import Callable

import httpx
import pytest

from app.services import calendar_connection
from app.services.calendar_connection import (
    CalendarBinding,
    binding_from_google,
    grant_covers_calendar,
    is_maulwurf_calendar,
    requested_scopes,
)
from app.services.google_calendar import (
    GoogleCalendarNotFound,
    GoogleCalendarResponseError,
    GoogleCalendarService,
    GoogleCalendarTransientError,
)
from app.services.oauth_google import CALENDAR_SCOPES, missing_calendar_scopes, scope_granted

Handler = Callable[[httpx.Request], httpx.Response]

CREATE_URL = "https://calendar.example/v3/calendars"
LIST_URL = "https://calendar.example/v3/users/me/calendarList"


def _service(handler: Handler) -> GoogleCalendarService:
    return GoogleCalendarService(
        access_token="test-access-token",  # noqa: S106 - fixture, not a credential
        base_url="https://calendar.example/v3",
        max_retries=2,
        transport=httpx.MockTransport(handler),
        sleeper=lambda _delay: None,
        jitter=lambda: 0.0,
    )


def _marked(calendar_id: str = "mw@group.calendar.google.com") -> dict[str, object]:
    return {
        "id": calendar_id,
        "summary": calendar_connection.MAULWURF_CALENDAR_SUMMARY,
        "description": calendar_connection.MAULWURF_CALENDAR_DESCRIPTION,
    }


def _calendar_list(*items: dict[str, object], next_page: str | None = None) -> httpx.Response:
    body: dict[str, object] = {"items": list(items)}
    if next_page is not None:
        body["nextPageToken"] = next_page
    return httpx.Response(200, json=body)


# --- Scopes ---------------------------------------------------------------------------


def test_scope_granted_accepts_the_long_and_the_short_form() -> None:
    full = CALENDAR_SCOPES[0]
    short = full.rsplit("/", 1)[-1]

    assert scope_granted([full], full)
    assert scope_granted([short], full)
    assert not scope_granted(["openid"], full)


def test_missing_scopes_lists_only_the_calendar_capabilities_not_granted() -> None:
    assert missing_calendar_scopes(["openid", CALENDAR_SCOPES[0]]) == list(CALENDAR_SCOPES[1:])
    assert missing_calendar_scopes(list(CALENDAR_SCOPES)) == []


def test_connect_asks_only_for_missing_scopes_and_reconnect_asks_for_all() -> None:
    assert requested_scopes([], reconnect=False) == list(CALENDAR_SCOPES)
    assert requested_scopes([CALENDAR_SCOPES[0]], reconnect=False) == list(CALENDAR_SCOPES[1:])
    # Everything already granted: ask again to renew the offline token, which Google treats
    # as an already-granted permission.
    assert requested_scopes(list(CALENDAR_SCOPES), reconnect=False) == list(CALENDAR_SCOPES)
    # After invalid_grant the previous grant cannot be trusted.
    assert requested_scopes(list(CALENDAR_SCOPES), reconnect=True) == list(CALENDAR_SCOPES)


def test_grant_covers_calendar_requires_every_scope_in_the_token_response() -> None:
    assert grant_covers_calendar({"scope": " ".join(CALENDAR_SCOPES)})
    assert grant_covers_calendar({"scope": "openid email profile " + " ".join(CALENDAR_SCOPES)})
    assert not grant_covers_calendar({"scope": "openid email profile"})
    assert not grant_covers_calendar({"scope": " ".join(CALENDAR_SCOPES[:2])})
    assert not grant_covers_calendar({})
    assert not grant_covers_calendar({"scope": None})


def test_identity_requires_summary_description_and_a_secondary_calendar() -> None:
    assert is_maulwurf_calendar(_marked())
    assert not is_maulwurf_calendar({**_marked(), "summary": "Maulwurf (personal)"})
    assert not is_maulwurf_calendar({**_marked(), "description": None})
    assert not is_maulwurf_calendar({**_marked(), "primary": True})


# --- Reconciliation -------------------------------------------------------------------


async def test_stored_calendar_is_reused_while_it_still_exists() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.url.path.endswith("/calendars/mw@group.calendar.google.com")
        return httpx.Response(200, json=_marked())

    binding = await binding_from_google(
        _service(handler), stored_calendar_id="mw@group.calendar.google.com", timezone="UTC"
    )

    assert binding == CalendarBinding(
        calendar_id="mw@group.calendar.google.com", reused=True, reconciled=False
    )
    assert [request.method for request in calls] == ["GET"]


async def test_deleted_stored_calendar_is_reconciled_from_the_calendar_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and "/calendarList" in request.url.path:
            return _calendar_list(_marked("replacement@group.calendar.google.com"))
        return httpx.Response(404, json={"error": {"errors": [{"reason": "notFound"}]}})

    binding = await binding_from_google(
        _service(handler), stored_calendar_id="gone@group.calendar.google.com", timezone="UTC"
    )

    assert binding.calendar_id == "replacement@group.calendar.google.com"
    assert binding.reused is True


async def test_existing_calendar_is_reused_without_creating_another() -> None:
    posts: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts.append(request)
            raise AssertionError("a second calendar must never be created")
        return _calendar_list(
            {"id": "other@group.calendar.google.com", "summary": "Personal"}, _marked()
        )

    binding = await binding_from_google(_service(handler), stored_calendar_id=None, timezone="UTC")

    assert binding.calendar_id == "mw@group.calendar.google.com"
    assert binding.reused is True and binding.reconciled is False
    assert posts == []


async def test_first_connect_creates_one_marked_calendar() -> None:
    created: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            if str(request.url) != CREATE_URL:
                raise AssertionError(f"unexpected create URL: {request.url}")
            created.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={**_marked("created@group.calendar.google.com"), "timeZone": "America/Bogota"},
            )
        return _calendar_list()

    binding = await binding_from_google(
        _service(handler), stored_calendar_id=None, timezone="America/Bogota"
    )

    assert binding == CalendarBinding(
        calendar_id="created@group.calendar.google.com", reused=False, reconciled=False
    )
    assert created == [
        {
            "summary": calendar_connection.MAULWURF_CALENDAR_SUMMARY,
            "description": calendar_connection.MAULWURF_CALENDAR_DESCRIPTION,
            "timeZone": "America/Bogota",
        }
    ]


async def test_ambiguous_create_is_reconciled_by_identity_before_failing() -> None:
    """A lost create response: Google created it, the client saw a 503 and retried."""
    calendar_exists = False
    posts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calendar_exists, posts
        if request.method == "POST":
            posts += 1
            calendar_exists = True  # the calendar was created before the response was lost
            return httpx.Response(503, json={"error": {"reason": "backendError"}})
        if "/calendarList" in request.url.path:
            return _calendar_list(*([_marked()] if calendar_exists else []))
        return httpx.Response(404)

    binding = await binding_from_google(_service(handler), stored_calendar_id=None, timezone="UTC")

    assert binding.calendar_id == "mw@group.calendar.google.com"
    assert binding.reconciled is True
    # Exactly one POST: a transport retry after a lost response could create a duplicate.
    assert posts == 1


async def test_lost_create_response_does_not_post_twice() -> None:
    """Why: a second POST after a lost response would create a duplicate calendar."""
    calendar_exists = False
    posts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calendar_exists, posts
        if request.method == "POST":
            posts += 1
            calendar_exists = True  # created server-side, response lost
            return httpx.Response(503, json={"error": {"reason": "backendError"}})
        if "/calendarList" in request.url.path:
            return _calendar_list(*([_marked()] if calendar_exists else []))
        return httpx.Response(404)

    binding = await binding_from_google(_service(handler), stored_calendar_id=None, timezone="UTC")

    assert posts == 1
    assert binding.reconciled is True


async def test_ambiguous_create_without_a_matching_calendar_surfaces_the_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(503, json={"error": {"reason": "backendError"}})
        return _calendar_list()

    with pytest.raises(GoogleCalendarTransientError):
        await binding_from_google(_service(handler), stored_calendar_id=None, timezone="UTC")


async def test_unparseable_create_response_is_treated_as_ambiguous() -> None:
    calendar_exists = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calendar_exists
        if request.method == "POST":
            calendar_exists = True
            return httpx.Response(200, content=b"<html>proxy error</html>")
        if "/calendarList" in request.url.path:
            return _calendar_list(*([_marked()] if calendar_exists else []))
        return httpx.Response(404)

    binding = await binding_from_google(_service(handler), stored_calendar_id=None, timezone="UTC")

    assert binding.reconciled is True
    assert binding.calendar_id == "mw@group.calendar.google.com"


async def test_duplicate_calendars_pin_one_deterministic_id_and_never_delete() -> None:
    deleted: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            deleted.append(request)
            return httpx.Response(204)
        return _calendar_list(
            _marked("zz@group.calendar.google.com"), _marked("aa@group.calendar.google.com")
        )

    binding = await binding_from_google(_service(handler), stored_calendar_id=None, timezone="UTC")

    assert binding.calendar_id == "aa@group.calendar.google.com"
    assert binding.reused is True
    assert deleted == []


async def test_calendar_list_pagination_is_followed() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.params.get("pageToken") is None:
            return _calendar_list(next_page="page-2")
        return _calendar_list(_marked())

    binding = await binding_from_google(_service(handler), stored_calendar_id=None, timezone="UTC")

    assert binding.calendar_id == "mw@group.calendar.google.com"
    assert len(requests) == 2  # no create after the second page
    assert requests[1].url.params["pageToken"] == "page-2"


async def test_truncated_calendar_list_fails_closed_instead_of_creating_a_duplicate() -> None:
    """A partial list could hide the existing calendar: never create from one."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            raise AssertionError("no calendar may be created after a truncated list")
        return _calendar_list(next_page="never-ends")

    with pytest.raises(GoogleCalendarTransientError):
        await binding_from_google(_service(handler), stored_calendar_id=None, timezone="UTC")

    assert requests
    assert all(request.method == "GET" for request in requests)


async def test_a_calendar_without_id_is_a_response_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"summary": "Maulwurf"})
        return _calendar_list()

    with pytest.raises(GoogleCalendarResponseError):
        await binding_from_google(_service(handler), stored_calendar_id=None, timezone="UTC")


async def test_a_missing_calendar_raises_the_typed_not_found() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"errors": [{"reason": "notFound"}]}})

    with pytest.raises(GoogleCalendarNotFound):
        await _service(handler).get_calendar("missing@group.calendar.google.com")

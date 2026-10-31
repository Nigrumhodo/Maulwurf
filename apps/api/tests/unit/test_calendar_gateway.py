"""J3.4 fix: the Google gateway patches stale bodies and refreshes a rejected token once.

`httpx.MockTransport` plays Google Calendar, so the cases stay deterministic and no real
credential or network call is involved.
"""

import json
from collections.abc import Callable, Iterator

import httpx
import pytest

from app.services.calendar_gateway import GoogleCalendarGateway
from app.services.google_calendar import (
    GoogleCalendarService,
    GoogleCalendarUnauthorized,
)

CALENDAR = "maulwurf@group.calendar.example"
EVENT_ID = "maulwurfabc"
BODY = {
    "summary": "Entregar informe",
    "description": "Informe revisado",
    "start": {"dateTime": "2026-11-02T14:00:00Z", "timeZone": "America/Bogota"},
    "end": {"dateTime": "2026-11-02T15:00:00Z", "timeZone": "America/Bogota"},
}

Handler = Callable[[httpx.Request], httpx.Response]


class Calendar:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json={"id": EVENT_ID})


def _gateway(handler: Handler, *, refresh: list[str] | None = None) -> GoogleCalendarGateway:
    async def refresh_access_token() -> str:
        assert refresh is not None
        refresh.append("called")
        return "refreshed-token"

    return GoogleCalendarGateway(
        GoogleCalendarService(
            access_token="expired-token",  # noqa: S106 - fixture, not a credential
            base_url="https://calendar.example/v3",
            transport=httpx.MockTransport(handler),
            max_retries=0,
            sleeper=lambda _delay: None,
        ),
        refresh_access_token=refresh_access_token if refresh is not None else None,
    )


@pytest.fixture
def calendar() -> Calendar:
    return Calendar()


async def test_update_event_patches_without_notifications(calendar: Calendar) -> None:
    await _gateway(calendar).update_event(CALENDAR, EVENT_ID, BODY)

    request = calendar.requests[0]
    assert request.method == "PATCH"
    assert request.url.params["sendUpdates"] == "none"
    assert json.loads(request.content) == BODY


async def test_update_event_clears_a_detail_the_new_version_removed(
    calendar: Calendar,
) -> None:
    body = {k: v for k, v in BODY.items() if k != "description"}

    await _gateway(calendar).update_event(CALENDAR, EVENT_ID, body)

    # PATCH keeps the fields it does not receive, so the adapter must send an empty string.
    assert json.loads(calendar.requests[0].content)["description"] == ""


async def test_a_401_refreshes_the_token_once_and_retries(calendar: Calendar) -> None:
    statuses: Iterator[int] = iter([401, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        calendar.requests.append(request)
        status = next(statuses)
        return httpx.Response(status, json={"error": {"code": status}})

    refreshed: list[str] = []
    gateway = _gateway(handler, refresh=refreshed)

    await gateway.insert_event(CALENDAR, EVENT_ID, BODY)

    assert refreshed == ["called"]
    assert len(calendar.requests) == 2
    assert calendar.requests[0].headers["authorization"] == "Bearer expired-token"
    assert calendar.requests[1].headers["authorization"] == "Bearer refreshed-token"


async def test_a_401_without_a_refresh_callback_is_raised(calendar: Calendar) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        calendar.requests.append(request)
        return httpx.Response(401, json={"error": {"code": 401}})

    with pytest.raises(GoogleCalendarUnauthorized):
        await _gateway(handler).insert_event(CALENDAR, EVENT_ID, BODY)

    assert len(calendar.requests) == 1

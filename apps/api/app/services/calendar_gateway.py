"""Google Calendar adapter for the idempotent task-event sync (J3.4).

Wraps `GoogleCalendarService` so the orchestrator sees "event or None" instead of the
service's typed `NotFound` error, and never lists or deletes events. A 401 triggers one
token refresh through the injected callback before the operation is retried.
"""

from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from urllib.parse import quote

from app.services.google_calendar import (
    GoogleCalendarNotFound,
    GoogleCalendarService,
    GoogleCalendarUnauthorized,
)


def _calendar(calendar_id: str) -> str:
    # Calendar IDs are usually e-mail addresses: encode the "@" as a path segment.
    return quote(calendar_id, safe="")


class GoogleCalendarGateway:
    def __init__(
        self,
        service: GoogleCalendarService,
        *,
        refresh_access_token: Callable[[], Awaitable[str]] | None = None,
    ) -> None:
        self._service = service
        self._refresh = refresh_access_token

    async def insert_event(
        self, calendar_id: str, event_id: str, body: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        payload = dict(body)
        payload["id"] = event_id
        return await self._request(
            "POST",
            f"/calendars/{_calendar(calendar_id)}/events",
            params={"sendUpdates": "none"},  # never e-mail attendees
            json=payload,
        )

    async def get_event(
        self, calendar_id: str, event_id: str
    ) -> Mapping[str, Any] | None:
        try:
            return await self._request(
                "GET",
                f"/calendars/{_calendar(calendar_id)}/events/{quote(event_id, safe='')}",
            )
        except GoogleCalendarNotFound:
            return None

    async def update_event(
        self, calendar_id: str, event_id: str, body: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        payload = dict(body)
        if "description" not in payload:
            # PATCH keeps what is not sent; an empty string clears a removed detail.
            payload["description"] = ""
        return await self._request(
            "PATCH",
            f"/calendars/{_calendar(calendar_id)}/events/{quote(event_id, safe='')}",
            params={"sendUpdates": "none"},
            json=payload,
        )

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            return await self._service.request(method, path, **kwargs)
        except GoogleCalendarUnauthorized:
            # The token may have expired between the freshness check and this call.
            if self._refresh is None:
                raise
            self._service.update_access_token(await self._refresh())
            return await self._service.request(method, path, **kwargs)

"""Google Calendar adapter for the idempotent task-event sync (J3.4).

Wraps `GoogleCalendarService` so the orchestrator sees "event or None" instead of the
service's typed `NotFound` error, and never lists or deletes events.
"""

from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

from app.services.google_calendar import GoogleCalendarNotFound, GoogleCalendarService


def _calendar(calendar_id: str) -> str:
    # Calendar IDs are usually e-mail addresses: encode the "@" as a path segment.
    return quote(calendar_id, safe="")


class GoogleCalendarGateway:
    def __init__(self, service: GoogleCalendarService) -> None:
        self._service = service

    async def insert_event(
        self, calendar_id: str, event_id: str, body: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        payload = dict(body)
        payload["id"] = event_id
        return await self._service.request(
            "POST",
            f"/calendars/{_calendar(calendar_id)}/events",
            params={"sendUpdates": "none"},  # never e-mail attendees
            json=payload,
        )

    async def get_event(
        self, calendar_id: str, event_id: str
    ) -> Mapping[str, Any] | None:
        try:
            return await self._service.request(
                "GET",
                f"/calendars/{_calendar(calendar_id)}/events/{quote(event_id, safe='')}",
            )
        except GoogleCalendarNotFound:
            return None

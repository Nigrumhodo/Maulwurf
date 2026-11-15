"""Google Calendar page parsing (J3.7).

Every page is followed. A truncated read is an error, never a complete window.
`syncToken` cannot be mixed with the explicit time bounds.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Literal, Protocol
from urllib.parse import quote

from app.services.calendar_window import (
    CANCELLED,
    CalendarEventInvalid,
    CalendarPaginationExhausted,
    _valid_timezone,
)
from app.services.google_calendar import GoogleCalendarService

MAX_PAGES = 100
MAX_PAGE_SIZE = 2500

@dataclass(frozen=True)
class RemoteCalendarEvent:
    """Normalized Google event: exactly one of the two pairs is set."""

    calendar_id: str
    event_id: str
    title: str
    start_at: datetime | None = None
    end_at: datetime | None = None
    start_date: date | None = None
    end_date_exclusive: date | None = None
    timezone: str | None = None
    etag: str | None = None
    remote_status: str = "confirmed"
    source: str = "google"


@dataclass(frozen=True)
class RemoteCancelledEvent:
    """Key of a remote event Google reported as cancelled; never itself a mirrored row."""

    calendar_id: str
    event_id: str


@dataclass(frozen=True)
class CalendarRead:
    """One calendar's complete read: active events plus the keys Google cancelled."""

    events: tuple[RemoteCalendarEvent, ...] = ()
    cancelled: tuple[RemoteCancelledEvent, ...] = ()


def iso_utc(value: datetime) -> str:
    """RFC 3339 in UTC, the format Google requires for `timeMin`/`timeMax`."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def build_list_params(
    *,
    time_min: datetime,
    time_max: datetime,
    page_token: str | None = None,
    sync_token: str | None = None,
) -> dict[str, str]:
    """Build one `events.list` query, refusing the incompatible incremental mix."""
    if sync_token is not None:
        # Google rejects this combination; dropping either side silently would hide a
        # coverage bug, so the contract fails loudly instead.
        raise ValueError("syncToken cannot be mixed with timeMin/timeMax and singleEvents")
    if time_min >= time_max:
        raise ValueError("timeMin must be before timeMax")
    params = {
        "timeMin": iso_utc(time_min),
        "timeMax": iso_utc(time_max),
        "singleEvents": "true",
        "showDeleted": "true",
        "orderBy": "startTime",
        "maxResults": str(MAX_PAGE_SIZE),
    }
    if page_token:
        params["pageToken"] = page_token
    return params


def _parse_instant(value: object, *, field: Literal["start", "end"]) -> datetime:
    code = f"event_{field}_invalid"
    if not isinstance(value, str) or not value:
        raise CalendarEventInvalid(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CalendarEventInvalid(code) from exc
    if parsed.tzinfo is None:
        # RFC 3339 always carries an offset; guessing UTC would misplace the event.
        raise CalendarEventInvalid(f"event_{field}_naive")
    return parsed.astimezone(UTC)


def _parse_date(value: object, *, field: Literal["start", "end"]) -> date:
    code = f"event_{field}_date_invalid"
    if not isinstance(value, str) or not value:
        raise CalendarEventInvalid(code)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CalendarEventInvalid(code) from exc


def parse_remote_event(
    calendar_id: str, item: Mapping[str, Any]
) -> RemoteCalendarEvent | RemoteCancelledEvent:
    """Normalize one Google event.

    A cancelled instance comes back as a `RemoteCancelledEvent` key: Google may omit its
    times, and the publication must only mark an existing linked row, never insert a new
    one. Every other item must carry a valid block or the whole read fails.
    """
    raw_id = item.get("id")
    if not isinstance(raw_id, str) or not raw_id.strip():
        raise CalendarEventInvalid("event_id_invalid")
    # Validate and persist the same token: keeping the padding would create a second mirror
    # row when Google later returns the canonical id, and the id is opaque besides whitespace.
    event_id = raw_id.strip()
    raw_status = item.get("status")
    # Blank and whitespace-only both mean "Google did not report a status": a returned
    # event is `confirmed`. The mirror CHECK rejects any blank value.
    status = raw_status.strip() if isinstance(raw_status, str) else ""
    remote_status = status or "confirmed"
    if remote_status == CANCELLED:
        return RemoteCancelledEvent(calendar_id=calendar_id, event_id=event_id)
    summary = item.get("summary")
    title = summary if isinstance(summary, str) else ""
    raw_etag = item.get("etag")
    etag = raw_etag.strip() if isinstance(raw_etag, str) else ""
    start = item.get("start")
    end = item.get("end")
    if not isinstance(start, Mapping) or not isinstance(end, Mapping):
        raise CalendarEventInvalid("event_block_missing")

    start_at = start_date = end_at = end_date = None
    timezone: str | None = None
    if start.get("dateTime") is not None or end.get("dateTime") is not None:
        start_at = _parse_instant(start.get("dateTime"), field="start")
        end_at = _parse_instant(end.get("dateTime"), field="end")
        if start_at >= end_at:
            raise CalendarEventInvalid("event_time_inverted")
        raw_zone = start.get("timeZone")
        timezone = _valid_timezone(raw_zone)
    elif start.get("date") is not None or end.get("date") is not None:
        start_date = _parse_date(start.get("date"), field="start")
        end_date = _parse_date(end.get("date"), field="end")
        if start_date >= end_date:
            raise CalendarEventInvalid("event_date_inverted")
    else:
        raise CalendarEventInvalid("event_block_missing")

    return RemoteCalendarEvent(
        calendar_id=calendar_id,
        event_id=event_id,
        title=title,
        start_at=start_at,
        end_at=end_at,
        start_date=start_date,
        end_date_exclusive=end_date,
        timezone=timezone,
        # An ETag is an opaque token: strip HTTP field whitespace, and treat a blank token
        # as unknown (NULL) instead of storing a whitespace-only conditional-write baseline.
        # `str.strip()` covers the SQL helper's ASCII set, vertical tab included.
        etag=etag or None,
        remote_status=remote_status,
    )


class CalendarEventSource(Protocol):
    async def list_events(
        self, calendar_id: str, *, time_min: datetime, time_max: datetime
    ) -> CalendarRead: ...


class GoogleCalendarEventSource:
    """Google adapter: full pagination over one calendar inside a fixed window."""

    def __init__(
        self,
        service: GoogleCalendarService,
        *,
        max_pages: int = MAX_PAGES,
    ) -> None:
        if max_pages < 1:
            raise ValueError("max_pages must be positive")
        self._service = service
        self._max_pages = max_pages

    async def list_events(
        self, calendar_id: str, *, time_min: datetime, time_max: datetime
    ) -> CalendarRead:
        if not calendar_id:
            raise CalendarEventInvalid("calendar_id_invalid")
        path = f"/calendars/{quote(calendar_id, safe='')}/events"
        base = build_list_params(time_min=time_min, time_max=time_max)
        events: list[RemoteCalendarEvent] = []
        cancelled: list[RemoteCancelledEvent] = []
        token: str | None = None
        pages = 0
        while True:
            params = dict(base)
            if token is not None:
                params["pageToken"] = token
            payload = await self._service.request("GET", path, params=params)
            pages += 1
            if not isinstance(payload, Mapping):
                # A non-object body (list, null, scalar) has no `.get`: fail with the typed
                # error instead of leaking an AttributeError as a 500.
                raise CalendarEventInvalid("events_payload_invalid")
            items = payload.get("items", [])
            if items is None:
                items = []
            if not isinstance(items, list):
                raise CalendarEventInvalid("events_payload_invalid")
            for item in items:
                if not isinstance(item, Mapping):
                    raise CalendarEventInvalid("event_payload_invalid")
                parsed = parse_remote_event(calendar_id, item)
                if isinstance(parsed, RemoteCancelledEvent):
                    cancelled.append(parsed)
                else:
                    events.append(parsed)
            next_token = payload.get("nextPageToken")
            if next_token is None or next_token == "":
                return CalendarRead(events=tuple(events), cancelled=tuple(cancelled))
            if not isinstance(next_token, str):
                raise CalendarEventInvalid("next_page_token_invalid")
            if pages >= self._max_pages:
                # Never return a partial first page as if it were the whole window.
                raise CalendarPaginationExhausted("pagination_exhausted")
            token = next_token



def google_event_source(access_token: str) -> CalendarEventSource:
    """Build the Google reader; tests replace this factory to inject a deterministic read."""
    return GoogleCalendarEventSource(GoogleCalendarService(access_token=access_token))

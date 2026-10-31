"""Calendar reading and atomic publication into the local mirror (J3.7).

The calendar is read as a **full window** every time:

- The window defaults to seven local days back and sixty local days forward. Day boundaries
  are computed in the user's IANA timezone, so a DST change never shifts the covered days.
- Every page is followed (`nextPageToken`) before anything is written. A truncated or
  failed read publishes nothing: partial results would be reported as complete coverage.
- `singleEvents=true` makes Google expand recurrences into instances and `showDeleted=true`
  surfaces deleted instances; a cancelled event is removed from the mirror, never stored.
- `syncToken` is never sent. It is incompatible with `timeMin`/`timeMax`/`singleEvents`/
  `orderBy`, and the MVP contract is an explicit full-window refresh. `build_list_params`
  rejects the mix instead of silently dropping a filter.

`SqlCalendarMirror` replaces the whole window in one transaction (savepoint) after taking a
per-user transaction advisory lock. Rows of the republished calendars that ended before the
window are pruned (Google is queried from `window.start`, so it will never return them), the
window rows the fresh read no longer carries are deleted, and the fetched events are upserted
on `(user_id, calendar_id, event_id)`: an event rescheduled into the window from an earlier
publication updates in place instead of colliding with the unique key. It then records the
covered window and `last_synced_at` in `settings`. Any failure rolls the savepoint back,
leaving both the previous mirror and the previous coverage untouched.
"""

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Final, Literal, Protocol, cast
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import CursorResult, Table, and_, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CalendarEvent, CalendarSettings, User
from app.services.google_calendar import GoogleCalendarService

DAYS_BACK = 7
DAYS_FORWARD = 60
# A query can never ask for an unbounded mirror scan; the default window is 68 local days.
MAX_QUERY_DAYS = 366
MAX_PAGES = 100
MAX_PAGE_SIZE = 2500
# The spec refreshes the mirror on demand or hourly; two hours is a conservative staleness
# signal for the dashboard, not a scheduled job of its own.
STALE_AFTER = timedelta(hours=2)

# Advisory-lock convention (A2.14, `services/audios.py`): the two-int form namespaces our
# locks instead of a bare bigint. The namespace is the same 0x4D57 ("MW") value that module
# uses and the per-user key uses the same derivation; a key collision only serializes two
# unrelated transactions for a moment and can never admit more work than the limits allow.
_ADVISORY_LOCK_NAMESPACE = 0x4D57

# Closed coverage vocabulary consumed by D3.6 and the `get_calendar_events` tool (J3.8).
CoverageNotice = Literal[
    "never_synced",
    "no_read_calendars",
    "calendar_set_changed",
    "outside_coverage",
    "stale",
]
NEVER_SYNCED: Final[CoverageNotice] = "never_synced"
NO_READ_CALENDARS: Final[CoverageNotice] = "no_read_calendars"
CALENDAR_SET_CHANGED: Final[CoverageNotice] = "calendar_set_changed"
OUTSIDE_COVERAGE: Final[CoverageNotice] = "outside_coverage"
STALE: Final[CoverageNotice] = "stale"

# Google `orderBy=startTime` requires `singleEvents=true`; both are part of the read contract.


class CalendarSyncError(Exception):
    """Typed Calendar read/publication error. `code` is stable and safe to log."""

    code = "calendar_sync_error"

    def __init__(self, code: str, *, field: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.field = field


class CalendarWindowInvalid(CalendarSyncError):
    code = "calendar_window_invalid"


class CalendarEventInvalid(CalendarSyncError):
    code = "calendar_event_invalid"


class CalendarPaginationExhausted(CalendarSyncError):
    code = "calendar_pagination_exhausted"


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        # A stored value can predate validation or come from an old client. UTC keeps the
        # window deterministic instead of turning a read into a 500.
        return ZoneInfo("UTC")


def _valid_timezone(value: object) -> str | None:
    """Keep only an IANA zone `zoneinfo` can resolve; a bogus one is stored as unknown."""
    if not isinstance(value, str) or not value:
        return None
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return value


def _user_lock_key(user_id: uuid.UUID) -> int:
    """Stable 31-bit advisory key for one user (fits the signed `int4` of the lock)."""
    return user_id.int & 0x7FFF_FFFF


@dataclass(frozen=True)
class CalendarWindow:
    """Half-open absolute window plus the IANA zone used to derive its local day bounds."""

    start: datetime
    end: datetime
    timezone: str = "UTC"

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise CalendarWindowInvalid("window_naive")
        if self.start >= self.end:
            raise CalendarWindowInvalid("window_inverted")
        object.__setattr__(self, "start", self.start.astimezone(UTC))
        object.__setattr__(self, "end", self.end.astimezone(UTC))

    @property
    def zone(self) -> ZoneInfo:
        return _zone(self.timezone)

    @property
    def start_date(self) -> date:
        return self.start.astimezone(self.zone).date()

    @property
    def end_date(self) -> date:
        """Last local date the window covers (inclusive)."""
        return (self.end - timedelta(microseconds=1)).astimezone(self.zone).date()

    @classmethod
    def default(cls, *, now: datetime, timezone: str) -> "CalendarWindow":
        zone = _zone(timezone)
        today = now.astimezone(zone).date()
        return cls.from_dates(
            today - timedelta(days=DAYS_BACK),
            today + timedelta(days=DAYS_FORWARD),
            timezone=timezone,
        )

    @classmethod
    def from_dates(
        cls, date_from: date, date_to: date, *, timezone: str
    ) -> "CalendarWindow":
        if date_to < date_from:
            raise CalendarWindowInvalid("window_inverted", field="to")
        if (date_to - date_from).days + 1 > MAX_QUERY_DAYS:
            raise CalendarWindowInvalid("window_too_wide", field="to")
        zone = _zone(timezone)
        start = datetime.combine(date_from, time.min, tzinfo=zone)
        # Inclusive `date_to`: the exclusive instant is the next local midnight.
        end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=zone)
        return cls(start=start, end=end, timezone=timezone)


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
class PublicationResult:
    """Rows written (`inserted` counts inserts and in-place updates) and rows deleted.

    `removed` includes both stale window rows and pruned pre-window rows.
    """

    inserted: int
    removed: int


@dataclass(frozen=True)
class CalendarSyncResult:
    window: CalendarWindow
    calendars: tuple[str, ...]
    inserted: int
    removed: int
    last_synced_at: datetime


@dataclass(frozen=True)
class CalendarEventView:
    id: uuid.UUID
    calendar_id: str
    event_id: str
    title: str
    source: str
    start_at: datetime | None
    end_at: datetime | None
    start_date: date | None
    end_date_exclusive: date | None
    timezone: str | None
    remote_status: str
    last_synced_at: datetime


@dataclass(frozen=True)
class CalendarPage:
    events: list[CalendarEventView]
    date_from: date
    date_to: date
    last_synced_at: datetime | None
    coverage_ok: bool
    coverage_notice: CoverageNotice | None
    stale: bool


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


def _parse_instant(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise CalendarEventInvalid("event_start_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CalendarEventInvalid("event_start_invalid") from exc
    if parsed.tzinfo is None:
        # RFC 3339 always carries an offset; guessing UTC would misplace the event.
        raise CalendarEventInvalid("event_start_naive")
    return parsed.astimezone(UTC)


def _parse_date(value: object) -> date:
    if not isinstance(value, str) or not value:
        raise CalendarEventInvalid("event_date_invalid")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CalendarEventInvalid("event_date_invalid") from exc


def parse_remote_event(calendar_id: str, item: Mapping[str, Any]) -> RemoteCalendarEvent | None:
    """Normalize one Google event; `None` means it is cancelled and must leave the mirror."""
    raw_id = item.get("id")
    if not isinstance(raw_id, str) or not raw_id:
        raise CalendarEventInvalid("event_id_invalid")
    status = item.get("status")
    remote_status = status if isinstance(status, str) and status else "confirmed"
    if remote_status == "cancelled":
        return None
    summary = item.get("summary")
    title = summary if isinstance(summary, str) else ""
    etag = item.get("etag")
    start = item.get("start")
    end = item.get("end")
    if not isinstance(start, Mapping) or not isinstance(end, Mapping):
        raise CalendarEventInvalid("event_block_missing")

    start_at = start_date = end_at = end_date = None
    timezone: str | None = None
    if start.get("dateTime") is not None or end.get("dateTime") is not None:
        start_at = _parse_instant(start.get("dateTime"))
        end_at = _parse_instant(end.get("dateTime"))
        if start_at >= end_at:
            raise CalendarEventInvalid("event_time_inverted")
        raw_zone = start.get("timeZone")
        timezone = _valid_timezone(raw_zone)
    elif start.get("date") is not None or end.get("date") is not None:
        start_date = _parse_date(start.get("date"))
        end_date = _parse_date(end.get("date"))
        if start_date >= end_date:
            raise CalendarEventInvalid("event_date_inverted")
    else:
        raise CalendarEventInvalid("event_block_missing")

    return RemoteCalendarEvent(
        calendar_id=calendar_id,
        event_id=raw_id,
        title=title,
        start_at=start_at,
        end_at=end_at,
        start_date=start_date,
        end_date_exclusive=end_date,
        timezone=timezone,
        etag=etag if isinstance(etag, str) else None,
        remote_status=remote_status,
    )


class CalendarEventSource(Protocol):
    async def list_events(
        self, calendar_id: str, *, time_min: datetime, time_max: datetime
    ) -> list[RemoteCalendarEvent]: ...


class CalendarMirror(Protocol):
    async def replace_window(
        self,
        *,
        user_id: uuid.UUID,
        window: CalendarWindow,
        calendars: tuple[str, ...],
        events: Iterable[RemoteCalendarEvent],
        synced_at: datetime,
    ) -> PublicationResult: ...


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
    ) -> list[RemoteCalendarEvent]:
        if not calendar_id:
            raise CalendarEventInvalid("calendar_id_invalid")
        path = f"/calendars/{quote(calendar_id, safe='')}/events"
        base = build_list_params(time_min=time_min, time_max=time_max)
        events: list[RemoteCalendarEvent] = []
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
                if parsed is not None:
                    events.append(parsed)
            next_token = payload.get("nextPageToken")
            if next_token is None or next_token == "":
                return events
            if not isinstance(next_token, str):
                raise CalendarEventInvalid("next_page_token_invalid")
            if pages >= self._max_pages:
                # Never return a partial first page as if it were the whole window.
                raise CalendarPaginationExhausted("pagination_exhausted")
            token = next_token


def _overlap_filter(window: CalendarWindow) -> Any:
    next_date = window.end_date + timedelta(days=1)
    timed = and_(
        CalendarEvent.start_at.is_not(None),
        CalendarEvent.start_at < window.end,
        CalendarEvent.end_at > window.start,
    )
    all_day = and_(
        CalendarEvent.start_date.is_not(None),
        CalendarEvent.start_date < next_date,
        CalendarEvent.end_date_exclusive > window.start_date,
    )
    return or_(timed, all_day)


def _before_window_filter(window: CalendarWindow) -> Any:
    """Rows that ended before the window and can no longer be observed by the read."""
    timed = and_(
        CalendarEvent.start_at.is_not(None),
        CalendarEvent.end_at <= window.start,
    )
    all_day = and_(
        CalendarEvent.start_date.is_not(None),
        CalendarEvent.end_date_exclusive <= window.start_date,
    )
    return or_(timed, all_day)


class SqlCalendarMirror:
    """PostgreSQL publication of one window; the caller owns the outer transaction."""

    # `Base.__table__` is typed as `FromClause` by mypy; DML needs the concrete `Table`.
    _EVENTS = cast("Table", CalendarEvent.__table__)
    _SETTINGS = cast("Table", CalendarSettings.__table__)

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def replace_window(
        self,
        *,
        user_id: uuid.UUID,
        window: CalendarWindow,
        calendars: tuple[str, ...],
        events: Iterable[RemoteCalendarEvent],
        synced_at: datetime,
    ) -> PublicationResult:
        rows = [
            {
                "user_id": user_id,
                "calendar_id": event.calendar_id,
                "event_id": event.event_id,
                "source": event.source,
                "title": event.title,
                "start_at": event.start_at,
                "end_at": event.end_at,
                "start_date": event.start_date,
                "end_date_exclusive": event.end_date_exclusive,
                "timezone": event.timezone,
                "etag": event.etag,
                "remote_status": event.remote_status,
                "last_synced_at": synced_at,
            }
            for event in events
        ]
        async with self._db.begin_nested():
            # Two concurrent refreshes for one user must not interleave delete/upsert.
            await self._db.execute(
                select(
                    func.pg_advisory_xact_lock(
                        _ADVISORY_LOCK_NAMESPACE, _user_lock_key(user_id)
                    )
                )
            )
            # Google is queried from `window.start`, so a row that ended before the window
            # will never come back: keeping it would grow the mirror without bound. Rows
            # linked to a task belong to the write path (J3.4/A3.8) and are never pruned.
            pruned = await self._db.execute(
                delete(CalendarEvent).where(
                    CalendarEvent.user_id == user_id,
                    CalendarEvent.calendar_id.in_(list(calendars)),
                    CalendarEvent.task_id.is_(None),
                    _before_window_filter(window),
                )
            )
            removed = await self._db.execute(
                delete(CalendarEvent).where(
                    CalendarEvent.user_id == user_id,
                    CalendarEvent.calendar_id.in_(list(calendars)),
                    _overlap_filter(window),
                )
            )
            if rows:
                # Upsert on the natural key: an event already mirrored by an earlier
                # publication (for example one rescheduled into this window) updates in
                # place instead of violating the unique key and failing every sync.
                # `source` and `task_id` are deliberately kept: they record who created the
                # row and its task link, not where the current remote values came from.
                statement = pg_insert(self._EVENTS).values(rows)
                await self._db.execute(
                    statement.on_conflict_do_update(
                        index_elements=["user_id", "calendar_id", "event_id"],
                        set_={
                            "title": statement.excluded.title,
                            "start_at": statement.excluded.start_at,
                            "end_at": statement.excluded.end_at,
                            "start_date": statement.excluded.start_date,
                            "end_date_exclusive": statement.excluded.end_date_exclusive,
                            "timezone": statement.excluded.timezone,
                            "etag": statement.excluded.etag,
                            "remote_status": statement.excluded.remote_status,
                            "last_synced_at": statement.excluded.last_synced_at,
                            "updated_at": func.now(),
                        },
                    )
                )
            await self._db.execute(
                pg_insert(self._SETTINGS)
                .values(
                    user_id=user_id,
                    calendar_last_synced_at=synced_at,
                    calendar_coverage_from=window.start,
                    calendar_coverage_to=window.end,
                    calendar_coverage_calendar_ids=list(calendars),
                )
                .on_conflict_do_update(
                    index_elements=["user_id"],
                    set_={
                        "calendar_last_synced_at": synced_at,
                        "calendar_coverage_from": window.start,
                        "calendar_coverage_to": window.end,
                        "calendar_coverage_calendar_ids": list(calendars),
                        "updated_at": func.now(),
                    },
                )
            )
            await self._db.flush()
        removed_count = (
            (cast("CursorResult[Any]", pruned).rowcount or 0)
            + (cast("CursorResult[Any]", removed).rowcount or 0)
        )
        return PublicationResult(inserted=len(rows), removed=removed_count)


def _dedupe(events: Iterable[RemoteCalendarEvent]) -> list[RemoteCalendarEvent]:
    """Keep one row per `(calendar, event)`; the last occurrence in read order wins."""
    unique: dict[tuple[str, str], RemoteCalendarEvent] = {}
    for event in events:
        unique[(event.calendar_id, event.event_id)] = event
    return list(unique.values())


def _normalize_calendars(calendars: Iterable[str]) -> tuple[str, ...]:
    """Drop blanks and duplicates; the selection is a set of remote calendar IDs."""
    return tuple(sorted({calendar.strip() for calendar in calendars if calendar.strip()}))


async def sync_calendar_window(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    window: CalendarWindow,
    calendars: Iterable[str],
    source: CalendarEventSource,
    mirror: CalendarMirror | None = None,
    now: datetime | None = None,
) -> CalendarSyncResult:
    """Read every selected calendar, then publish the complete window atomically."""
    selected = _normalize_calendars(calendars)
    synced_at = (now or datetime.now(UTC)).astimezone(UTC)
    if not selected:
        return CalendarSyncResult(
            window=window,
            calendars=(),
            inserted=0,
            removed=0,
            last_synced_at=synced_at,
        )
    # No database work happens until every page of every calendar was read: a failure
    # leaves the previous mirror and its coverage untouched.
    fetched: list[RemoteCalendarEvent] = []
    for calendar_id in selected:
        fetched.extend(
            await source.list_events(
                calendar_id, time_min=window.start, time_max=window.end
            )
        )
    publication = await (mirror or SqlCalendarMirror(db)).replace_window(
        user_id=user_id,
        window=window,
        calendars=selected,
        events=_dedupe(fetched),
        synced_at=synced_at,
    )
    return CalendarSyncResult(
        window=window,
        calendars=selected,
        inserted=publication.inserted,
        removed=publication.removed,
        last_synced_at=synced_at,
    )


def coverage_status(
    *,
    read_calendars: Iterable[str],
    coverage_from: datetime | None,
    coverage_to: datetime | None,
    coverage_calendars: Iterable[str],
    last_synced_at: datetime | None,
    window: CalendarWindow,
    now: datetime,
) -> tuple[bool, CoverageNotice | None]:
    """Closed-set freshness/coverage decision for the mirror read (D3.6/J3.8)."""
    current = _normalize_calendars(read_calendars)
    if last_synced_at is None or coverage_from is None or coverage_to is None:
        return False, NEVER_SYNCED
    if not current:
        return False, NO_READ_CALENDARS
    if current != _normalize_calendars(coverage_calendars):
        return False, CALENDAR_SET_CHANGED

    if coverage_from > window.start or coverage_to < window.end:
        return False, OUTSIDE_COVERAGE
    if now.astimezone(UTC) - last_synced_at.astimezone(UTC) > STALE_AFTER:
        return False, STALE
    return True, None


def _event_sort_key(event: CalendarEventView, zone: ZoneInfo) -> tuple[datetime, str, str]:
    if event.start_at is not None:
        start = event.start_at.astimezone(UTC)
    elif event.start_date is not None:
        start = datetime.combine(event.start_date, time.min, tzinfo=zone).astimezone(UTC)
    else:  # pragma: no cover - the CHECK constraint keeps this unreachable
        start = datetime.max.replace(tzinfo=UTC)
    return start, event.calendar_id, event.event_id


class CalendarService:
    """Reads only the local mirror; it never calls Google (J3.7 contract)."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def _timezone(self, user_id: uuid.UUID) -> str:
        user = await self._db.get(User, user_id)
        return user.timezone if user is not None and user.timezone else "UTC"

    async def page(
        self,
        *,
        user_id: uuid.UUID,
        date_from: date | None = None,
        date_to: date | None = None,
        now: datetime | None = None,
    ) -> CalendarPage:
        """Return the whole requested window in one response.

        The J3.7 contract is not paginated: the widest allowed window (366 local days) and
        the per-user mirror bound the result, and a silent `LIMIT` would report incomplete
        data as covered. Paging would have to be added to the API contract first.
        """
        reference = (now or datetime.now(UTC)).astimezone(UTC)
        timezone = await self._timezone(user_id)
        window = _resolve_window(
            date_from=date_from, date_to=date_to, timezone=timezone, now=reference
        )
        settings = await self._db.get(CalendarSettings, user_id)
        result = await self._db.execute(
            select(CalendarEvent)
            .where(CalendarEvent.user_id == user_id, _overlap_filter(window))
            .order_by(CalendarEvent.calendar_id, CalendarEvent.event_id)
        )
        rows = list(result.scalars())
        read_calendars = settings.calendar_read_calendar_ids if settings is not None else []
        coverage_ok, notice = coverage_status(
            read_calendars=read_calendars,
            coverage_from=settings.calendar_coverage_from if settings else None,
            coverage_to=settings.calendar_coverage_to if settings else None,
            coverage_calendars=(
                settings.calendar_coverage_calendar_ids if settings else []
            ),
            last_synced_at=settings.calendar_last_synced_at if settings else None,
            window=window,
            now=reference,
        )
        last_synced_at = settings.calendar_last_synced_at if settings else None
        stale = last_synced_at is None or (
            reference - last_synced_at.astimezone(UTC) > STALE_AFTER
        )
        views = [
            CalendarEventView(
                id=row.id,
                calendar_id=row.calendar_id,
                event_id=row.event_id,
                title=row.title,
                source=row.source,
                start_at=row.start_at,
                end_at=row.end_at,
                start_date=row.start_date,
                end_date_exclusive=row.end_date_exclusive,
                timezone=row.timezone,
                remote_status=row.remote_status,
                last_synced_at=row.last_synced_at,
            )
            for row in rows
        ]
        zone = window.zone
        views.sort(key=lambda event: _event_sort_key(event, zone))
        return CalendarPage(
            events=views,
            date_from=window.start_date,
            date_to=window.end_date,
            last_synced_at=last_synced_at,
            coverage_ok=coverage_ok,
            coverage_notice=notice,
            stale=stale,
        )


def _resolve_window(
    *,
    date_from: date | None,
    date_to: date | None,
    timezone: str,
    now: datetime,
) -> CalendarWindow:
    """Complete a half-given range from the bound the client actually sent.

    A lone bound anchors the window (`from` → `from + 60`, `to` → `to - 7`). Filling the
    missing side from today's default would invert a future `from` and answer a misleading
    "`to` cannot be before `from`" for a parameter the client never sent.
    """
    if date_to is None:
        if date_from is None:
            return CalendarWindow.default(now=now, timezone=timezone)
        return CalendarWindow.from_dates(
            date_from, date_from + timedelta(days=DAYS_FORWARD), timezone=timezone
        )
    if date_from is None:
        return CalendarWindow.from_dates(
            date_to - timedelta(days=DAYS_BACK), date_to, timezone=timezone
        )
    return CalendarWindow.from_dates(date_from, date_to, timezone=timezone)

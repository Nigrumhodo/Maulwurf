"""Local mirror reads: coverage, ordering and `CalendarService.page` (J3.7/J3.8).

This module never calls Google.
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CalendarEvent, CalendarSettings, User
from app.services.calendar_mirror import _overlap_filter
from app.services.calendar_window import (
    CALENDAR_SET_CHANGED,
    NEVER_SYNCED,
    NO_READ_CALENDARS,
    OUTSIDE_COVERAGE,
    STALE,
    STALE_AFTER,
    CalendarWindow,
    CoverageNotice,
    _resolve_window,
)


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
    timezone: str
    last_synced_at: datetime | None
    coverage_ok: bool
    coverage_notice: CoverageNotice | None
    stale: bool

def _normalize_calendars(calendars: Iterable[str]) -> tuple[str, ...]:
    """Drop blanks and duplicates; the selection is a set of remote calendar IDs."""
    return tuple(sorted({calendar.strip() for calendar in calendars if calendar.strip()}))


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

        Only rows of the calendars currently selected for reading are returned: a calendar
        removed from the selection must stop being presented immediately, even before the
        next refresh prunes its rows from the mirror. An empty selection returns no rows.

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
        read_calendars = _normalize_calendars(
            settings.calendar_read_calendar_ids if settings is not None else []
        )
        rows: list[CalendarEvent] = []
        if read_calendars:
            result = await self._db.execute(
                select(CalendarEvent)
                .where(
                    CalendarEvent.user_id == user_id,
                    CalendarEvent.calendar_id.in_(list(read_calendars)),
                    _overlap_filter(window),
                )
                .order_by(CalendarEvent.calendar_id, CalendarEvent.event_id)
            )
            rows = list(result.scalars())
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
            timezone=timezone,
            last_synced_at=last_synced_at,
            coverage_ok=coverage_ok,
            coverage_notice=notice,
            stale=stale,
        )

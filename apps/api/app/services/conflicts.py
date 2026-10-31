"""Calendar conflicts: timed overlap is not per-day clustering (J3.8, M5).

Two different questions, deliberately answered by different lists:

- **`time_conflicts`**: two events that actually share time. Only rows with an instant
  pair (`start_at`/`end_at`) qualify; the interval is half-open, so `end == start` is not
  an overlap. All-day rows, cancelled rows and rows the last read marked `missing` never
  produce a time conflict.
- **`deadline_clashes`**: two or more active task deadlines or exams falling on the same
  **user-local** day. A deadline is a day, not a block: it is never a time conflict, even
  when its instant falls inside another event.

The window and the mirror read are the same as `GET /calendar/events`: the service reuses
`CalendarService.page`, so deselected calendars, coverage and the -7/+60 default stay in
one place. Deadline rows come from the frozen `tasks` shape (DESIGN §2.8); A3.1 owns the
real table, so until that migration lands the source returns no rows instead of failing the
read (the table-existence probe is a catalog lookup, not a caught SQL error).
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Final, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    MetaData,
    Table,
    Text,
    and_,
    or_,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.calendar_sync import (
    CalendarEventView,
    CalendarPage,
    CalendarService,
    CoverageNotice,
)

# A deadline without a date cannot cluster; these are the frozen task types that represent
# a real obligation. Reminders and recommendations are not deadlines (DESIGN §2.8).
DEADLINE_TIPOS: Final = frozenset({"examen", "tarea", "entrega", "lectura", "proyecto"})
ACTIVE_TASK_STATUSES: Final = frozenset({"pending", "needs_review", "confirmed"})
# Two obligations on one day are already a concentration the student should see; a single
# deadline is normal workload, not a clash.
MIN_DEADLINES_PER_DAY: Final = 2
# A row in either state is not a current commitment. `tentative` still blocks time.
INACTIVE_REMOTE_STATUSES: Final = frozenset({"cancelled", "missing"})

# Read-only projection of the frozen `tasks` shape (DESIGN §2.8). A3.1 owns the migration;
# declaring only the columns this read needs keeps the service independent of the rest of
# the task model.
TASKS = Table(
    "tasks",
    MetaData(),
    Column("id", PgUUID(as_uuid=True), primary_key=True),
    Column("user_id", PgUUID(as_uuid=True), nullable=False),
    Column("tipo", Text, nullable=False),
    Column("due_date", Date),
    Column("due_at", DateTime(timezone=True)),
    Column("status", Text, nullable=False),
)


@dataclass(frozen=True)
class ConflictEventRef:
    event_id: str
    calendar_id: str
    title: str
    start_at: datetime
    end_at: datetime


@dataclass(frozen=True)
class TimeConflict:
    a: ConflictEventRef
    b: ConflictEventRef


@dataclass(frozen=True)
class Deadline:
    task_id: uuid.UUID
    tipo: str
    due_date: date | None = None
    due_at: datetime | None = None


@dataclass(frozen=True)
class DeadlineItem:
    task_id: uuid.UUID
    tipo: str


@dataclass(frozen=True)
class DeadlineClash:
    date: date
    items: tuple[DeadlineItem, ...]


@dataclass(frozen=True)
class ConflictReport:
    time_conflicts: tuple[TimeConflict, ...]
    deadline_clashes: tuple[DeadlineClash, ...]


@dataclass(frozen=True)
class CalendarConflicts:
    report: ConflictReport
    date_from: date
    date_to: date
    last_synced_at: datetime | None
    coverage_ok: bool
    coverage_notice: CoverageNotice | None
    stale: bool


class DeadlineSource(Protocol):
    async def list_deadlines(
        self,
        *,
        user_id: uuid.UUID,
        date_from: date,
        date_to: date,
        timezone: str,
    ) -> list[Deadline]: ...


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        # A stored zone that no longer resolves keeps the grouping deterministic instead
        # of turning the endpoint into a 500; the same rule as the calendar window.
        return ZoneInfo("UTC")


def _local_day(deadline: Deadline, zone: ZoneInfo) -> date | None:
    # `due_date` is already a full-day value. `due_at` is an instant, so the local day
    # depends on the user's zone: 02:00Z is still the previous day in Bogota.
    if deadline.due_date is not None:
        return deadline.due_date
    if deadline.due_at is None:
        return None
    return deadline.due_at.astimezone(zone).date()


def detect_conflicts(
    events: Iterable[CalendarEventView],
    deadlines: Iterable[Deadline],
    *,
    timezone: str,
) -> ConflictReport:
    """Compute both lists from already-read rows. Pure and deterministic."""
    timed = sorted(
        (
            ConflictEventRef(
                event_id=event.event_id,
                calendar_id=event.calendar_id,
                title=event.title,
                start_at=event.start_at,
                end_at=event.end_at,
            )
            for event in events
            if event.start_at is not None
            and event.end_at is not None
            and event.remote_status not in INACTIVE_REMOTE_STATUSES
        ),
        key=lambda event: (
            event.start_at,
            event.end_at,
            event.calendar_id,
            event.event_id,
        ),
    )
    time_conflicts: list[TimeConflict] = []
    for index, first in enumerate(timed):
        # Sorted by start: once a later event starts at or after `first.end`, every event
        # after it does too, so the half-open interval comparison can stop there.
        for second in timed[index + 1 :]:
            if second.start_at >= first.end_at:
                break
            time_conflicts.append(TimeConflict(a=first, b=second))

    zone = _zone(timezone)
    by_day: dict[date, list[DeadlineItem]] = {}
    for deadline in deadlines:
        if deadline.tipo not in DEADLINE_TIPOS:
            continue
        day = _local_day(deadline, zone)
        if day is None:
            continue
        by_day.setdefault(day, []).append(
            DeadlineItem(task_id=deadline.task_id, tipo=deadline.tipo)
        )

    clashes = [
        DeadlineClash(
            date=day,
            items=tuple(sorted(items, key=lambda item: (item.task_id, item.tipo))),
        )
        for day, items in sorted(by_day.items())
        if len(items) >= MIN_DEADLINES_PER_DAY
    ]
    return ConflictReport(
        time_conflicts=tuple(time_conflicts), deadline_clashes=tuple(clashes)
    )


class SqlTaskDeadlineSource:
    """Reads active deadlines from the frozen `tasks` table, when it exists.

    A3.1 owns the table and it is not in `develop` yet. A catalog probe keeps this read
    working (empty `deadline_clashes`) instead of failing every conflicts request on a
    missing relation; once the migration lands, the same SQL starts returning rows.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def _table_exists(self) -> bool:
        return bool(
            await self._db.scalar(text("SELECT to_regclass('public.tasks') IS NOT NULL"))
        )

    async def list_deadlines(
        self,
        *,
        user_id: uuid.UUID,
        date_from: date,
        date_to: date,
        timezone: str,
    ) -> list[Deadline]:
        if not await self._table_exists():
            return []
        zone = _zone(timezone)
        start = datetime.combine(date_from, time.min, tzinfo=zone)
        end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=zone)
        result = await self._db.execute(
            select(TASKS.c.id, TASKS.c.tipo, TASKS.c.due_date, TASKS.c.due_at).where(
                TASKS.c.user_id == user_id,
                TASKS.c.status.in_(sorted(ACTIVE_TASK_STATUSES)),
                TASKS.c.tipo.in_(sorted(DEADLINE_TIPOS)),
                or_(
                    and_(
                        TASKS.c.due_date.is_not(None),
                        TASKS.c.due_date >= date_from,
                        TASKS.c.due_date <= date_to,
                    ),
                    and_(
                        TASKS.c.due_at.is_not(None),
                        TASKS.c.due_at >= start,
                        TASKS.c.due_at < end,
                    ),
                ),
            )
        )
        return [
            Deadline(
                task_id=row.id,
                tipo=row.tipo,
                due_date=row.due_date,
                due_at=row.due_at,
            )
            for row in result
        ]


class ConflictService:
    """Reads the mirror and the task deadlines for one tenant; never calls Google."""

    def __init__(
        self, db: AsyncSession, *, deadlines: DeadlineSource | None = None
    ) -> None:
        self._calendar = CalendarService(db)
        self._deadlines = deadlines or SqlTaskDeadlineSource(db)

    async def conflicts(
        self,
        *,
        user_id: uuid.UUID,
        date_from: date | None = None,
        date_to: date | None = None,
        now: datetime | None = None,
    ) -> CalendarConflicts:
        page: CalendarPage = await self._calendar.page(
            user_id=user_id, date_from=date_from, date_to=date_to, now=now
        )
        deadlines = await self._deadlines.list_deadlines(
            user_id=user_id,
            date_from=page.date_from,
            date_to=page.date_to,
            timezone=page.timezone,
        )
        report = detect_conflicts(page.events, deadlines, timezone=page.timezone)
        return CalendarConflicts(
            report=report,
            date_from=page.date_from,
            date_to=page.date_to,
            last_synced_at=page.last_synced_at,
            coverage_ok=page.coverage_ok,
            coverage_notice=page.coverage_notice,
            stale=page.stale,
        )

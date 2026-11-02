"""J4.6: daily and weekly digests by the user's local date (M8 §1.4).

A digest is a scheduled ``notifications`` occurrence, exactly like a reminder: the J4.2
scheduler claims it and J4.1 delivers it through Gmail. This module owns the two pieces the
scheduler needs before that hand-off:

- **When**: the next daily occurrence at ``settings.digest_daily_time`` (07:00 by default)
  and the next weekly occurrence on ``settings.digest_weekday`` (0 = Sunday, the frozen
  default). Both are resolved in ``users.timezone`` with the J4.4 DST rule, so a nonexistent
  local wall clock moves to the first valid instant and a repeated one is armed once. The
  daily occurrence's ``occurrence`` is its local date; the weekly occurrence is the Sunday
  that starts the local week. The canonical A4.1 ``dedupe_key`` therefore contains the
  local period and the database uniqueness makes a second send per user and period
  impossible by construction.
- **What**: ``build_digest_content`` reads the tenant's active tasks and the M5 conflicts
  (``time_conflicts`` + ``deadline_clashes``) for the period. Daily content covers today and
  tomorrow (M8: "today's pending items + what is due tomorrow"); weekly content covers the
  seven local days that start on the weekly occurrence. Gmail templates are J4.1's job: this
  seam returns structured, redacted rows (no transcript text, no recipient, no provider
  payload).

Opt-in is a scheduling gate: a user without ``gmail_opt_in`` gets no new digest occurrence.
Like A4.3 records, opting out pauses delivery instead of irreversibly cancelling an already
scheduled occurrence; J4.1 refuses to send it because the preference is no longer set.

The materializer runs as the cross-tenant jobs role from the ARQ cron. It keeps the next
planned occurrence of each enabled type and cancels the pending rows that plan leaves
behind: a digest of an older period is cancelled when its ``scheduled_at`` is still ahead
(the preferred hour or weekday moved to one that already passed today/this week, so the
leftover row would be delivered with the previous schedule) or when its period is already
over (a scheduler outage cannot flush a retrospective burst). A current-period row whose
instant already arrived stays claimable by J4.2, and a pre-armed row of a later period is
left for the plan to adopt. A pending occurrence whose key is still planned and whose
``scheduled_at`` moved is recomputed (the dedupe identity stays the same), a row whose
instant already matches is not rewritten at all, and a ``cancelled`` occurrence is reopened
only when the preference is enabled again. A ``sending``/``sent``/``failed``/
``delivery_unknown`` row is never rewritten, so delivery history is preserved.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Final, Self, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import CursorResult, and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CalendarSettings, Notification, Task, User
from app.services.conflicts import (
    ACTIVE_TASK_STATUSES,
    ConflictReport,
    ConflictService,
)
from app.services.notifications import dedupe_key
from app.services.reminders import resolve_local_wall

# A4.1 freezes 07:00 as the daily default; the weekly digest reuses the same configured
# wall clock because the settings row has no second time preference.
DEFAULT_DIGEST_LOCAL_TIME: Final = time(7, 0)
# 0 = Sunday. This is the PATCH schema's 0–6 range and the frozen default, so the weekly
# digest lands on Sunday unless the user changes the preference.
DEFAULT_DIGEST_WEEKDAY: Final = 0
SUNDAY: Final = 0
DAYS_PER_WEEK: Final = 7
# M8 daily content: today's pending items plus what is due tomorrow.
DAILY_WINDOW_DAYS: Final = 2
WEEKLY_WINDOW_DAYS: Final = 7
# `gmail_opt_in` is part of the schedule gate; `Notification` remains the delivery audit.
OPT_IN_REQUIRED = "gmail_not_opted_in"


class DigestScheduleError(ValueError):
    """The digest preferences cannot produce a truthful schedule; nothing is invented."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class DigestPreferences:
    """The A4.1 digest columns; validation happens when a plan is built."""

    gmail_opt_in: bool = False
    digest_daily_enabled: bool = False
    digest_daily_time: time = DEFAULT_DIGEST_LOCAL_TIME
    digest_weekly_enabled: bool = False
    digest_weekday: int = DEFAULT_DIGEST_WEEKDAY

    @classmethod
    def from_settings(cls, row: CalendarSettings | None) -> Self:
        if row is None:
            return cls()
        return cls(
            gmail_opt_in=row.gmail_opt_in,
            digest_daily_enabled=row.digest_daily_enabled,
            digest_daily_time=row.digest_daily_time,
            digest_weekly_enabled=row.digest_weekly_enabled,
            digest_weekday=row.digest_weekday,
        )


@dataclass(frozen=True, slots=True)
class DigestOccurrence:
    """One scheduled digest ready to be persisted under its canonical identity."""

    type: str
    scheduled_at: datetime
    occurrence: date
    dedupe_key: str


@dataclass(frozen=True, slots=True)
class DigestTaskItem:
    """A redacted upcoming-task row; no transcript text ever enters the digest content."""

    task_id: uuid.UUID
    titulo: str
    tipo: str
    status: str
    all_day: bool
    local_day: date
    due_date: date | None
    due_at: datetime | None


@dataclass(frozen=True, slots=True)
class DigestContent:
    """What J4.1 renders for one digest notification."""

    type: str
    local_date: date
    window_start: date
    window_end: date
    upcoming_tasks: tuple[DigestTaskItem, ...]
    conflicts: ConflictReport


@dataclass(frozen=True, slots=True)
class DigestMaterialization:
    """Counts of one materializer tick; no row payload leaves the adapter.

    ``written`` counts inserted or changed rows only, so a tick with no preference change
    reports zero even when occurrences were planned.
    """

    users: int
    planned: int
    written: int
    cancelled: int
    skipped: int


def _error(code: str, message: str) -> DigestScheduleError:
    return DigestScheduleError(code, message)


def _require_aware(value: datetime, *, field: str, code: str) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise _error(code, f"{field} must be a timezone-aware instant")
    return value


def _zone(name: str) -> ZoneInfo:
    cleaned = name.strip()
    if not cleaned:
        raise _error("timezone_invalid", "the user timezone must be a non-blank IANA name")
    try:
        return ZoneInfo(cleaned)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise _error(
            "timezone_invalid", "the user timezone is not a valid IANA zone"
        ) from exc


def _local_clock(value: time) -> time:
    if value.tzinfo is not None:
        raise _error(
            "local_time_invalid",
            "the digest time is a local wall clock, not an instant",
        )
    if value.second or value.microsecond:
        raise _error(
            "local_time_invalid", "the digest time uses minute precision (HH:MM)"
        )
    return value


def _weekday(value: int) -> int:
    # `bool` is an `int` subclass; it is never a valid weekday preference.
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 6:
        raise _error(
            "weekday_invalid", "the weekly digest weekday must be between 0 and 6"
        )
    return value


def _local_instant(day: date, local_time: time, zone: ZoneInfo) -> datetime:
    """Resolve one local wall clock to UTC under the J4.4 DST rules."""
    wall = datetime.combine(day, local_time)
    return resolve_local_wall(wall, zone).astimezone(UTC)


def _sunday(day: date) -> date:
    """Sunday that starts the week containing ``day`` (0 = Sunday, as in the preference)."""
    return day - timedelta(days=(day.weekday() + 1) % DAYS_PER_WEEK)


def _current_period(type_: str, local_day: date) -> date:
    """Period identity of ``local_day`` for one digest type."""
    return local_day if type_ == "digest_daily" else _sunday(local_day)


def _occurrence(
    *,
    user_id: uuid.UUID,
    type_: str,
    scheduled_at: datetime,
    occurrence: date,
) -> DigestOccurrence:
    return DigestOccurrence(
        type=type_,
        scheduled_at=scheduled_at,
        occurrence=occurrence,
        dedupe_key=dedupe_key(
            user_id=user_id,
            type=type_,
            task_id=None,
            due_version=None,
            occurrence=occurrence,
        ),
    )


def _next_daily(
    *,
    user_id: uuid.UUID,
    local_day: date,
    clock: time,
    zone: ZoneInfo,
    now: datetime,
) -> DigestOccurrence:
    """The next daily occurrence strictly after ``now`` (today or tomorrow)."""
    occurrence_day = local_day
    scheduled_at = _local_instant(occurrence_day, clock, zone)
    if scheduled_at <= now:
        occurrence_day += timedelta(days=1)
        scheduled_at = _local_instant(occurrence_day, clock, zone)
    return _occurrence(
        user_id=user_id,
        type_="digest_daily",
        scheduled_at=scheduled_at,
        occurrence=occurrence_day,
    )


def _next_weekly(
    *,
    user_id: uuid.UUID,
    local_day: date,
    clock: time,
    weekday: int,
    zone: ZoneInfo,
    now: datetime,
) -> DigestOccurrence:
    """The next weekly occurrence strictly after ``now``.

    ``occurrence`` is the Sunday of the target week, not the configured delivery weekday:
    changing ``digest_weekday`` inside the same week keeps the weekly dedupe identity, so
    J4.6 updates ``scheduled_at`` instead of creating a second row for the same week.
    """
    week_start = _sunday(local_day)
    target_day = week_start + timedelta(days=weekday)
    scheduled_at = _local_instant(target_day, clock, zone)
    if scheduled_at <= now:
        week_start += timedelta(days=DAYS_PER_WEEK)
        target_day += timedelta(days=DAYS_PER_WEEK)
        scheduled_at = _local_instant(target_day, clock, zone)
    return _occurrence(
        user_id=user_id,
        type_="digest_weekly",
        scheduled_at=scheduled_at,
        occurrence=week_start,
    )


def plan_digest_occurrences(
    preferences: DigestPreferences,
    *,
    user_id: uuid.UUID,
    user_timezone: str,
    now: datetime,
) -> tuple[DigestOccurrence, ...]:
    """Build the next daily and weekly occurrences, or nothing when opted out/disabled.

    The plan is pure: the caller owns persistence and the transaction. ``now`` is an
    instant, never a local wall clock, so two hosts in different zones agree. With the
    opt-in off, no new occurrence is armed; an occurrence already scheduled is not
    cancelled here (A4.3 owns preference cancellation and J4.1 the delivery gate).
    """
    instant = _require_aware(now, field="now", code="clock_invalid")
    clock = _local_clock(preferences.digest_daily_time)
    weekday = _weekday(preferences.digest_weekday)
    zone = _zone(user_timezone)
    if not preferences.gmail_opt_in:
        return ()
    local_day = instant.astimezone(zone).date()
    occurrences: list[DigestOccurrence] = []
    if preferences.digest_daily_enabled:
        occurrences.append(
            _next_daily(
                user_id=user_id,
                local_day=local_day,
                clock=clock,
                zone=zone,
                now=instant,
            )
        )
    if preferences.digest_weekly_enabled:
        occurrences.append(
            _next_weekly(
                user_id=user_id,
                local_day=local_day,
                clock=clock,
                weekday=weekday,
                zone=zone,
                now=instant,
            )
        )
    return tuple(occurrences)


def digest_window(type_: str, local_date: date) -> tuple[date, date]:
    """Inclusive local-day window covered by one digest."""
    if type_ == "digest_daily":
        return local_date, local_date + timedelta(days=DAILY_WINDOW_DAYS - 1)
    if type_ == "digest_weekly":
        return local_date, local_date + timedelta(days=WEEKLY_WINDOW_DAYS - 1)
    raise _error("digest_type_invalid", f"unknown digest type: {type_!r}")


async def _database_clock(db: AsyncSession) -> datetime:
    """PostgreSQL's statement instant, so a skewed API host cannot choose the wrong day."""
    value = await db.scalar(select(func.clock_timestamp()))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise _error("clock_invalid", "the database did not return a timezone-aware instant")
    return value


def _rearmed_values(statement: Any) -> dict[str, Any]:
    """Columns written when a pending digest is recomputed or a cancelled one is reopened.

    Only ``pending``/``cancelled`` rows reach this branch (the conflict clause's WHERE).
    Clearing the delivery fields keeps an unusual historical row from coming back as
    ``pending`` with a lease, fence or sent proof that violates the M8 CHECKs. ``attempts``
    is deliberately absent: a pending row that carries a retry keeps its counter, and a
    cancelled row was never claimed.
    """
    return {
        "status": "pending",
        "scheduled_at": statement.excluded.scheduled_at,
        "lease_expires_at": None,
        "claimed_by_fence": None,
        "sent_at": None,
        "provider_message_id": None,
        "last_error_code": None,
        "updated_at": func.now(),
    }


async def _cancel_unscheduled_digests(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    planned: tuple[DigestOccurrence, ...],
    local_day: date,
    now: datetime,
) -> int:
    """Cancel pending digests of an enabled type that the current plan left behind.

    A pending row whose canonical identity is the planned next occurrence is kept and
    recomputed by the upsert. A row of the same type from an **older** period is obsolete:
    it is cancelled when its ``scheduled_at`` is still ahead (a preference change to an hour
    or weekday that already passed moves the plan to the next period, and the leftover row
    would otherwise be delivered with the old schedule) or when its period is already over
    (a scheduler outage must not flush a retrospective burst).

    Two cases are deliberately preserved: a current-period row whose instant already
    arrived stays claimable by J4.2 (a missed claim at the exact minute is retried later the
    same day, as before), and a pre-armed row of a **later** period is left for the plan to
    adopt when it reaches that period, so it is never cancelled and recreated needlessly.
    ``sending`` and terminal rows are delivery history and are never rewritten, a manual
    resend (``occurrence_seq > 0``) is an audited one-shot, and a disabled type is left to
    A4.3's preference cancellation.
    """
    if not planned:
        return 0
    conditions = [
        and_(
            Notification.type == occurrence.type,
            Notification.occurrence < occurrence.occurrence,
            or_(
                Notification.scheduled_at > now,
                Notification.occurrence < _current_period(occurrence.type, local_day),
            ),
        )
        for occurrence in planned
    ]
    result = await db.execute(
        update(Notification)
        .where(
            Notification.user_id == user_id,
            Notification.status == "pending",
            Notification.occurrence_seq == 0,
            Notification.dedupe_key.not_in(
                frozenset(occurrence.dedupe_key for occurrence in planned)
            ),
            or_(*conditions),
        )
        .values(status="cancelled", updated_at=func.now())
    )
    return max(int(cast(CursorResult[Any], result).rowcount or 0), 0)


async def _write_occurrences(db: AsyncSession, rows: list[dict[str, Any]]) -> int:
    """Upsert this user's digest occurrences; the canonical key is the conflict target.

    A pending row is rewritten only when the plan moves its ``scheduled_at``, so a tick with
    no preference change touches nothing and ``written`` counts only inserted or changed
    rows; ``attempts`` is never reset by a rewrite. A ``cancelled`` row is reopened when the
    preference returns. ``sending``/``sent``/``failed``/``delivery_unknown`` never match the
    WHERE and keep their delivery history.
    """
    statement = pg_insert(Notification).values(rows)
    upsert = statement.on_conflict_do_update(
        constraint="uq_notifications_dedupe_key",
        set_=_rearmed_values(statement),
        where=or_(
            Notification.status == "cancelled",
            and_(
                Notification.status == "pending",
                Notification.scheduled_at.is_distinct_from(
                    statement.excluded.scheduled_at
                ),
            ),
        ),
    )
    result = await db.execute(upsert)
    return max(int(cast(CursorResult[Any], result).rowcount or 0), 0)


async def materialize_due_digests(
    db: AsyncSession, *, now: datetime | None = None
) -> DigestMaterialization:
    """Ensure every opted-in user has its next daily/weekly occurrence in ``notifications``.

    The cron runs this as the cross-tenant ``maulwurf_jobs`` role. Rows are created ahead
    of their ``scheduled_at`` (so a preference change can recompute the pending row), and
    the J4.2 claim remains the only place that moves them to ``sending``. Nothing is
    delivered here: Gmail is J4.1.

    One unreadable preference (a legacy invalid timezone) is skipped instead of aborting
    the whole tick; the report counts it so the operator can notice it without log PII.
    """
    instant = now if now is not None else await _database_clock(db)
    _require_aware(instant, field="now", code="clock_invalid")
    query = (
        select(User.id, User.timezone, CalendarSettings)
        .join(CalendarSettings, CalendarSettings.user_id == User.id)
        .where(
            User.status == "active",
            User.deleted_at.is_(None),
            CalendarSettings.gmail_opt_in.is_(True),
            or_(
                CalendarSettings.digest_daily_enabled.is_(True),
                CalendarSettings.digest_weekly_enabled.is_(True),
            ),
        )
        .order_by(User.id)
    )
    users = 0
    planned = 0
    written = 0
    cancelled = 0
    skipped = 0
    for user_id, timezone, settings_row in (await db.execute(query)).all():
        users += 1
        preferences = DigestPreferences.from_settings(settings_row)
        try:
            occurrences = plan_digest_occurrences(
                preferences,
                user_id=user_id,
                user_timezone=str(timezone),
                now=instant,
            )
            local_day = instant.astimezone(_zone(str(timezone))).date()
        except DigestScheduleError:
            skipped += 1
            continue
        cancelled += await _cancel_unscheduled_digests(
            db,
            user_id=user_id,
            planned=occurrences,
            local_day=local_day,
            now=instant,
        )
        if not occurrences:
            continue
        planned += len(occurrences)
        rows = [
            {
                "user_id": user_id,
                "task_id": None,
                "type": occurrence.type,
                "occurrence": occurrence.occurrence,
                "due_version": None,
                "scheduled_at": occurrence.scheduled_at,
                "dedupe_key": occurrence.dedupe_key,
                "occurrence_seq": 0,
                "status": "pending",
            }
            for occurrence in occurrences
        ]
        written += await _write_occurrences(db, rows)
    return DigestMaterialization(
        users=users,
        planned=planned,
        written=written,
        cancelled=cancelled,
        skipped=skipped,
    )


def _task_item(
    *,
    task_id: uuid.UUID,
    titulo: str,
    tipo: str,
    status: str,
    all_day: bool,
    due_date: date | None,
    due_at: datetime | None,
    zone: ZoneInfo,
) -> DigestTaskItem | None:
    """Project one task onto its user-local day, failing closed on an incoherent row.

    The database CHECK guarantees exactly one due representation for a resolved task. An
    incoherent in-memory row is a programming error, so it is omitted instead of inventing
    a midnight or an instant. A naive instant is likewise not trusted.
    """
    if due_date is not None and due_at is None:
        local_day = due_date
        stored_date: date | None = due_date
        stored_instant: datetime | None = None
    elif due_at is not None and due_date is None:
        if due_at.tzinfo is None:
            return None
        stored_date = None
        stored_instant = due_at.astimezone(UTC)
        local_day = due_at.astimezone(zone).date()
    else:
        return None
    return DigestTaskItem(
        task_id=task_id,
        titulo=titulo,
        tipo=tipo,
        status=status,
        all_day=all_day,
        local_day=local_day,
        due_date=stored_date,
        due_at=stored_instant,
    )


def _task_sort_key(item: DigestTaskItem) -> tuple[date, datetime, str, str]:
    instant = item.due_at or datetime.combine(item.local_day, time.min, tzinfo=UTC)
    return (item.local_day, instant, item.titulo, str(item.task_id))


async def _upcoming_tasks(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    timezone: str,
    window_start: date,
    window_end: date,
) -> tuple[DigestTaskItem, ...]:
    """Active resolved tasks due inside the local-day window, ordered for the email."""
    zone = _zone(timezone)
    start = _local_instant(window_start, time.min, zone)
    end = _local_instant(window_end + timedelta(days=1), time.min, zone)
    rows = (
        await db.execute(
            select(
                Task.id,
                Task.titulo,
                Task.tipo,
                Task.status,
                Task.all_day,
                Task.due_date,
                Task.due_at,
            ).where(
                Task.user_id == user_id,
                Task.status.in_(sorted(ACTIVE_TASK_STATUSES)),
                Task.date_status == "resolved",
                or_(
                    and_(
                        Task.due_date.is_not(None),
                        Task.due_date >= window_start,
                        Task.due_date <= window_end,
                    ),
                    and_(
                        Task.due_at.is_not(None),
                        Task.due_at >= start,
                        Task.due_at < end,
                    ),
                ),
            )
        )
    ).all()
    items = [
        item
        for task_id, titulo, tipo, status, all_day, due_date, due_at in rows
        if (
            item := _task_item(
                task_id=task_id,
                titulo=titulo,
                tipo=tipo,
                status=status,
                all_day=all_day,
                due_date=due_date,
                due_at=due_at,
                zone=zone,
            )
        )
        is not None
        and window_start <= item.local_day <= window_end
    ]
    return tuple(sorted(items, key=_task_sort_key))


async def build_digest_content(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    user_timezone: str,
    type_: str,
    local_date: date,
    now: datetime | None = None,
) -> DigestContent:
    """Read the tasks and M5 conflicts one digest should present.

    The content is computed when J4.1 is about to deliver the claimed occurrence, never at
    insertion time: the week's tasks and conflicts must reflect the data at send time. Only
    task titles, types, statuses and due representations are returned; transcript text,
    segment spans, addresses and provider payloads stay out of the digest contract.
    """
    window_start, window_end = digest_window(type_, local_date)
    tasks = await _upcoming_tasks(
        db,
        user_id=user_id,
        timezone=user_timezone,
        window_start=window_start,
        window_end=window_end,
    )
    conflicts = await ConflictService(db).conflicts(
        user_id=user_id,
        date_from=window_start,
        date_to=window_end,
        now=now,
    )
    return DigestContent(
        type=type_,
        local_date=local_date,
        window_start=window_start,
        window_end=window_end,
        upcoming_tasks=tasks,
        conflicts=conflicts.report,
    )

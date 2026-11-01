"""J4.3: escalating reminders for a confirmed task (M8 §1.2).

A task with a resolved date gets one `notifications` occurrence per enabled threshold:

- **timed instant** (`due_at`): T-48 h, T-24 h and T-2 h before the instant;
- **all-day** (`due_date`): T-3 d and T-1 d at the user's local wall-clock time.

The all-day reminder uses the user's current IANA zone (`users.timezone`), not the task's
class-timezone snapshot: the snapshot resolves the extracted date, while the delivery has to
follow the person's local clock. A timed reminder already carries its instant, so only the
local day recorded in `occurrence` needs the user's zone.

An undated task (`date_status=missing|ambiguous`) gets no reminders, and a task that is not
`confirmed` is not schedulable: completing or dismissing it must never arm a message. The
schedule is **never retrospective**: if the user confirms late, thresholds that already
passed are simply absent, so Gmail cannot send a burst of old reminders. A threshold due at
the exact confirmation instant is kept; it is on time and the J4.2 minute tick delivers it
inside the M8 15-minute target.

`occurrence` is the **user-local target date** of each occurrence (the day the reminder is
delivered), matching the persistent dedupe identity of A4.1. `dedupe_key` is built by
`app.services.notifications.dedupe_key`, the only formatter, so a repeated confirmation or a
retried job cannot produce two spellings of the same identity. The database UNIQUE
constraints remain the final barrier.

The all-day wall clock has no dedicated preference column in A4.1 (`settings` only freezes
the digest time and the reminder offsets), so J4.3 uses `DEFAULT_REMINDER_LOCAL_TIME` and
accepts an explicit override. A future preference can pass its own `local_time` without
changing the scheduling contract; reusing `digest_daily_time` would couple two unrelated
preferences.

DST is J4.4's ticket: this module resolves the local wall clock with `zoneinfo` and does not
yet move a nonexistent hour to the next valid instant. A repeated hour resolves to its first
occurrence (one local occurrence, as M8 requires).

The persistence adapter writes the plan and the A4.3 obsolete-generation cancellation in the
caller's transaction. The upsert only conflicts on `uq_notifications_dedupe_key`: a repeated
confirmation of the same task version updates nothing while the occurrence is still pending,
and it re-arms a scheduled occurrence that A4.3 cancelled (for example after a preference was
disabled and enabled again). A row that is `sending`, `sent`, `failed` or `delivery_unknown`
is never rewritten, so the idempotent re-confirmation cannot resend a delivery. The previous
generation is retired before the new rows are inserted.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Final, Protocol, Self, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import CursorResult, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CalendarSettings, Notification
from app.services.notification_cancellation import SqlNotificationCancellationStore
from app.services.notifications import (
    ALLDAY_REMINDER_OFFSETS,
    TIMED_REMINDER_OFFSETS,
    dedupe_key,
    scheduled_reminder_types,
)

# The frozen A4.1 defaults: [48, 24, 2] hours and [3, 1] days.
DEFAULT_REMINDER_OFFSETS_HOURS: Final = (48, 24, 2)
DEFAULT_REMINDER_OFFSETS_DAYS: Final = (3, 1)

# M8 asks for the all-day thresholds "at the configured local time" but A4.1 has no reminder
# wall-clock preference. 09:00 local is the documented J4.3 default; callers can pass another
# `local_time` without a schema change, and the digest time stays independent.
DEFAULT_REMINDER_LOCAL_TIME: Final = time(9, 0)

_RESOLVED = "resolved"
_CONFIRMED = "confirmed"


class ReminderScheduleError(ValueError):
    """The inputs cannot produce a truthful schedule; nothing is invented."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class TaskLike(Protocol):
    """The `tasks` fields the planner reads; an ORM row or a test double satisfies it.

    The members are read-only properties so a SQLAlchemy mapped attribute (which has no
    public setter type) can satisfy the protocol without a cast.
    """

    @property
    def id(self) -> uuid.UUID: ...

    @property
    def user_id(self) -> uuid.UUID: ...

    @property
    def status(self) -> str: ...

    @property
    def all_day(self) -> bool: ...

    @property
    def date_status(self) -> str: ...

    @property
    def due_date(self) -> date | None: ...

    @property
    def due_at(self) -> datetime | None: ...

    @property
    def due_version(self) -> int: ...


@dataclass(frozen=True, slots=True)
class ReminderTask:
    """Schedulable view of one `tasks` row; no ORM session is retained."""

    id: uuid.UUID
    user_id: uuid.UUID
    status: str
    all_day: bool
    date_status: str
    due_date: date | None
    due_at: datetime | None
    due_version: int

    @classmethod
    def from_task(cls, task: TaskLike) -> Self:
        return cls(
            id=task.id,
            user_id=task.user_id,
            status=task.status,
            all_day=task.all_day,
            date_status=task.date_status,
            due_date=task.due_date,
            due_at=task.due_at,
            due_version=task.due_version,
        )


@dataclass(frozen=True, slots=True)
class ReminderPreferences:
    """The two threshold arrays of A4.1; validation happens when a plan is built."""

    reminder_offsets_hours: tuple[int, ...] = DEFAULT_REMINDER_OFFSETS_HOURS
    reminder_allday_offsets_days: tuple[int, ...] = DEFAULT_REMINDER_OFFSETS_DAYS


DEFAULT_REMINDER_PREFERENCES: Final = ReminderPreferences()


@dataclass(frozen=True, slots=True)
class ReminderOccurrence:
    """One occurrence of the M8 dedupe identity, ready to be persisted."""

    type: str
    scheduled_at: datetime
    occurrence: date
    due_version: int
    dedupe_key: str


@dataclass(frozen=True, slots=True)
class ReminderSchedule:
    """Counts of one scheduling attempt; no row payload leaves the adapter."""

    planned: int
    # Occurrences armed by the attempt: newly inserted plus `cancelled` rows reopened by the
    # upsert. A pending duplicate is not counted because it was already armed.
    inserted: int
    cancelled: int


def _error(code: str, message: str) -> ReminderScheduleError:
    return ReminderScheduleError(code, message)


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


def _local_time(value: time) -> time:
    if value.tzinfo is not None:
        raise _error(
            "local_time_invalid",
            "the all-day reminder time is a local wall clock, not an instant",
        )
    if value.second or value.microsecond:
        raise _error(
            "local_time_invalid",
            "the all-day reminder time uses minute precision (HH:MM)",
        )
    return value


def _preferences(preferences: ReminderPreferences) -> tuple[str, ...]:
    """Validate the thresholds and return the ordered types they enable."""
    try:
        return scheduled_reminder_types(
            reminder_offsets_hours=preferences.reminder_offsets_hours,
            reminder_allday_offsets_days=preferences.reminder_allday_offsets_days,
        )
    except ValueError as exc:
        raise _error(
            "preference_invalid", "the reminder thresholds are not valid offsets"
        ) from exc


def _due_kind(task: ReminderTask) -> str | None:
    """Return `"all_day"`/`"instant"`, or `None` when nothing may be scheduled.

    The database CHECK guarantees a coherent resolved date. An incoherent in-memory row is a
    programming error, so the planner fails closed with no reminders instead of inventing a
    midnight or an instant. Unresolved tasks are a normal, contract-defined state.
    """
    if task.status != _CONFIRMED or task.date_status != _RESOLVED:
        return None
    if task.all_day:
        if task.due_date is not None and task.due_at is None:
            return "all_day"
        return None
    if task.due_at is not None and task.due_date is None:
        return "instant"
    return None


def _local_target(
    day: date, *, local_time: time, zone: ZoneInfo
) -> datetime:
    """Resolve one local target day at the configured wall clock.

    J4.4 owns the DST correction of a nonexistent hour; `zoneinfo` already resolves a
    repeated hour to a single local occurrence (fold=0), which is the M8 rule.
    """
    return datetime.combine(day, local_time, tzinfo=zone)


def plan_task_reminders(
    task: ReminderTask,
    *,
    user_timezone: str,
    now: datetime,
    preferences: ReminderPreferences = DEFAULT_REMINDER_PREFERENCES,
    local_time: time = DEFAULT_REMINDER_LOCAL_TIME,
) -> tuple[ReminderOccurrence, ...]:
    """Build the occurrences a confirmed task version arms, earliest first.

    The plan is pure: the caller owns persistence and the transaction. No date means no
    reminders; a non-confirmed task means no reminders; a threshold already past at `now` is
    omitted so a late confirmation never triggers a retrospective burst.
    """
    instant = _require_aware(now, field="now", code="clock_invalid")
    clock = _local_time(local_time)
    zone = _zone(user_timezone)
    if task.due_version < 1:
        raise _error("due_version_invalid", "the task due_version must be positive")
    kind = _due_kind(task)
    if kind is None:
        return ()

    enabled = _preferences(preferences)
    occurrences: list[ReminderOccurrence] = []
    if kind == "all_day":
        # `_due_kind` proved the all-day pair before returning; the cast only carries that
        # narrowing into the branch, it does not trust an inconsistent row.
        due_date = cast(date, task.due_date)
        for type_ in enabled:
            offset = ALLDAY_REMINDER_OFFSETS.get(type_)
            if offset is None:
                continue
            target_day = due_date - timedelta(days=offset)
            local_instant = _local_target(target_day, local_time=clock, zone=zone)
            scheduled_at = local_instant.astimezone(UTC)
            if scheduled_at < instant:
                continue
            occurrences.append(
                _occurrence(
                    task,
                    type_=type_,
                    scheduled_at=scheduled_at,
                    occurrence=target_day,
                )
            )
    else:
        due_at = cast(datetime, task.due_at)
        due_instant = _require_aware(due_at, field="due_at", code="due_invalid")
        for type_ in enabled:
            hours = TIMED_REMINDER_OFFSETS.get(type_)
            if hours is None:
                continue
            scheduled_at = due_instant.astimezone(UTC) - timedelta(hours=hours)
            if scheduled_at < instant:
                continue
            occurrences.append(
                _occurrence(
                    task,
                    type_=type_,
                    scheduled_at=scheduled_at,
                    occurrence=scheduled_at.astimezone(zone).date(),
                )
            )
    return tuple(occurrences)


def _occurrence(
    task: ReminderTask,
    *,
    type_: str,
    scheduled_at: datetime,
    occurrence: date,
) -> ReminderOccurrence:
    return ReminderOccurrence(
        type=type_,
        scheduled_at=scheduled_at,
        occurrence=occurrence,
        due_version=task.due_version,
        dedupe_key=dedupe_key(
            user_id=task.user_id,
            type=type_,
            task_id=task.id,
            due_version=task.due_version,
            occurrence=occurrence,
        ),
    )


async def load_reminder_preferences(
    db: AsyncSession, user_id: uuid.UUID
) -> ReminderPreferences:
    """Read the user's threshold arrays, or the frozen defaults when no row exists."""
    row = await db.get(CalendarSettings, user_id)
    if row is None:
        return DEFAULT_REMINDER_PREFERENCES
    return ReminderPreferences(
        reminder_offsets_hours=tuple(row.reminder_offsets_hours),
        reminder_allday_offsets_days=tuple(row.reminder_allday_offsets_days),
    )


async def _database_clock(db: AsyncSession) -> datetime:
    """PostgreSQL's current instant, so a skewed API host cannot arm or skip a threshold.

    `now()` is the start of the transaction, not the moment of the comparison: a long
    confirmation transaction would measure "already past" against a stale instant and could
    arm a retrospective reminder. `clock_timestamp()` is the statement instant, which is
    what "now" means for this gate. J4.2's scheduler keeps `now()` on purpose: its tick is a
    single bound transaction where the election, the expiry sweep and the claim must share
    one instant, and both expressions come from the same database server clock.
    """
    value = await db.scalar(select(func.clock_timestamp()))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise _error("clock_invalid", "the database did not return a timezone-aware instant")
    return value


def _rearmed_values(statement: Any) -> dict[str, Any]:
    """Columns reopened when a cancelled scheduled occurrence is armed again.

    Only `cancelled` rows reach this branch (the conflict clause's WHERE), and A4.3 only
    cancels rows that are still `pending`, so the delivery fields must already be empty. They
    are reset anyway so an unusual historical row can never come back as `pending` with a
    lease or delivery proof that violates the M8 CHECKs, and a re-armed occurrence starts
    with a clean attempt count.
    """
    return {
        "status": "pending",
        "scheduled_at": statement.excluded.scheduled_at,
        "lease_expires_at": None,
        "attempts": 0,
        "claimed_by_fence": None,
        "sent_at": None,
        "provider_message_id": None,
        "last_error_code": None,
        "updated_at": func.now(),
    }


async def schedule_task_reminders(
    db: AsyncSession,
    task: ReminderTask,
    *,
    user_timezone: str,
    preferences: ReminderPreferences = DEFAULT_REMINDER_PREFERENCES,
    now: datetime | None = None,
    local_time: time = DEFAULT_REMINDER_LOCAL_TIME,
) -> ReminderSchedule:
    """Arm the task's occurrences in the caller's transaction, idempotently.

    The plan is built (and validated) before anything is written, so an invalid timezone or
    preference never retires the previous generation. A4.3 owns *which* occurrences are
    obsolete, so the adapter reuses its store: pending reminders of another `due_version`
    of this task are cancelled before the new generation is inserted. The upsert targets
    `uq_notifications_dedupe_key` explicitly: a repeated confirmation of the same version
    leaves a still-pending occurrence untouched, and reopens a row A4.3 cancelled (the user
    disabled and re-enabled a threshold) so the reminder is not silently lost. `sending`,
    `sent`, `failed` and `delivery_unknown` never match the reopen condition and keep their
    delivery history. The caller commits; a crash cannot leave the old generation armed next
    to the new one.

    With no explicit `now`, the comparison uses PostgreSQL's current instant (the same
    database clock the J4.2 scheduler uses), not the API host's. No insert happens for an
    undated or non-confirmed task; the cancellation still runs so an edit that removed the
    date retires the generation that was armed before it.
    """
    instant = now if now is not None else await _database_clock(db)
    occurrences = plan_task_reminders(
        task,
        user_timezone=user_timezone,
        now=instant,
        preferences=preferences,
        local_time=local_time,
    )
    cancelled = await SqlNotificationCancellationStore(db).cancel_obsolete(
        task.user_id, task_id=task.id, due_version=task.due_version
    )
    if not occurrences:
        return ReminderSchedule(planned=0, inserted=0, cancelled=cancelled)

    rows = [
        {
            "user_id": task.user_id,
            "task_id": task.id,
            "type": occurrence.type,
            "occurrence": occurrence.occurrence,
            "due_version": occurrence.due_version,
            "scheduled_at": occurrence.scheduled_at,
            "dedupe_key": occurrence.dedupe_key,
            # A4.2 reserves 0 for the scheduled occurrence; a manual resend takes >= 1.
            "occurrence_seq": 0,
            "status": "pending",
        }
        for occurrence in occurrences
    ]
    statement = pg_insert(Notification).values(rows)
    upsert = statement.on_conflict_do_update(
        constraint="uq_notifications_dedupe_key",
        set_=_rearmed_values(statement),
        where=Notification.status == "cancelled",
    )
    result = await db.execute(upsert)
    inserted = max(int(cast(CursorResult[Any], result).rowcount or 0), 0)
    return ReminderSchedule(
        planned=len(occurrences), inserted=inserted, cancelled=cancelled
    )

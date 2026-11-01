"""J4.3 against PostgreSQL: the planned occurrences are persisted with their dedupe key.

U-S4-JF-01 proves the pure plan; this module proves the A/J adapter on the real schema:

- a confirmed timed task inserts exactly T-48/T-24/T-2 h as `pending` occurrences with the
  canonical `dedupe_key` and `occurrence_seq=0`;
- repeating the confirmation is idempotent (the database uniqueness absorbs it);
- a new `due_version` retires the previous generation (A4.3) and arms the new one;
- an all-day task inserts T-3d/T-1d at the user's local 09:00;
- an undated task inserts nothing, and removing the date retires the armed generation.

No Gmail/Google call, credential or secret is involved.
"""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CalendarSettings, Notification, Task
from app.services.reminders import (
    DEFAULT_REMINDER_PREFERENCES,
    ReminderPreferences,
    ReminderSchedule,
    ReminderTask,
    load_reminder_preferences,
    schedule_task_reminders,
)
from tests.integration.actors import Actor, make_actor, make_task

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
TIMED_DUE = datetime(2026, 11, 10, 18, 0, tzinfo=UTC)
ALL_DAY_DUE = date(2026, 11, 10)
BOGOTA = "America/Bogota"


async def _confirmed_timed(db: AsyncSession, actor: Actor) -> Task:
    task = await make_task(db, actor)
    task.status = "confirmed"
    task.all_day = False
    task.date_status = "resolved"
    task.due_at = TIMED_DUE
    task.due_date = None
    await db.flush()
    return task


async def _confirmed_allday(db: AsyncSession, actor: Actor) -> Task:
    task = await make_task(db, actor)
    task.status = "confirmed"
    task.all_day = True
    task.date_status = "resolved"
    task.due_date = ALL_DAY_DUE
    task.due_at = None
    await db.flush()
    return task


async def _stored(db: AsyncSession, task_id: uuid.UUID) -> list[Notification]:
    return list(
        (
            await db.scalars(
                select(Notification)
                .where(Notification.task_id == task_id)
                .order_by(Notification.scheduled_at)
            )
        ).all()
    )


async def _schedule(
    db: AsyncSession,
    task: Task,
    *,
    preferences: ReminderPreferences = DEFAULT_REMINDER_PREFERENCES,
    now: datetime = NOW,
) -> ReminderSchedule:
    return await schedule_task_reminders(
        db,
        ReminderTask.from_task(task),
        user_timezone=BOGOTA,
        preferences=preferences,
        now=now,
    )


async def test_a_confirmed_timed_task_inserts_the_three_expiring_occurrences(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    task = await _confirmed_timed(db_session, actor)

    result = await _schedule(db_session, task)

    assert result == ReminderSchedule(planned=3, inserted=3, cancelled=0)
    rows = await _stored(db_session, task.id)
    assert [row.type for row in rows] == ["reminder_t48", "reminder_t24", "reminder_t2"]
    assert [row.scheduled_at for row in rows] == [
        datetime(2026, 11, 8, 18, 0, tzinfo=UTC),
        datetime(2026, 11, 9, 18, 0, tzinfo=UTC),
        datetime(2026, 11, 10, 16, 0, tzinfo=UTC),
    ]
    assert [row.occurrence for row in rows] == [
        date(2026, 11, 8),
        date(2026, 11, 9),
        date(2026, 11, 10),
    ]
    assert {row.status for row in rows} == {"pending"}
    assert {row.occurrence_seq for row in rows} == {0}
    assert {row.due_version for row in rows} == {task.due_version}
    assert [row.dedupe_key for row in rows] == [
        f"{actor.user_id}|reminder_t48|{task.id}|1|2026-11-08",
        f"{actor.user_id}|reminder_t24|{task.id}|1|2026-11-09",
        f"{actor.user_id}|reminder_t2|{task.id}|1|2026-11-10",
    ]


async def test_repeating_the_confirmation_is_idempotent(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    task = await _confirmed_timed(db_session, actor)

    assert (await _schedule(db_session, task)).inserted == 3
    again = await _schedule(db_session, task)

    assert again == ReminderSchedule(planned=3, inserted=0, cancelled=0)
    assert len(await _stored(db_session, task.id)) == 3


async def test_a_new_due_generation_retires_the_old_one_and_arms_the_new_one(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    task = await _confirmed_timed(db_session, actor)
    await _schedule(db_session, task)
    old = {row.id for row in await _stored(db_session, task.id)}

    task.due_version = 2
    task.due_at = TIMED_DUE + timedelta(days=1)
    await db_session.flush()
    result = await _schedule(db_session, task)

    assert result == ReminderSchedule(planned=3, inserted=3, cancelled=3)
    rows = await _stored(db_session, task.id)
    assert len(rows) == 6
    assert {row.status for row in rows if row.id in old} == {"cancelled"}
    assert {row.status for row in rows if row.id not in old} == {"pending"}
    assert {row.due_version for row in rows if row.id not in old} == {2}
    assert [
        row.scheduled_at for row in rows if row.id not in old
    ] == [
        datetime(2026, 11, 9, 18, 0, tzinfo=UTC),
        datetime(2026, 11, 10, 18, 0, tzinfo=UTC),
        datetime(2026, 11, 11, 16, 0, tzinfo=UTC),
    ]


async def test_an_all_day_task_inserts_the_two_thresholds_at_the_local_clock(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    task = await _confirmed_allday(db_session, actor)

    result = await _schedule(db_session, task)

    assert result == ReminderSchedule(planned=2, inserted=2, cancelled=0)
    rows = await _stored(db_session, task.id)
    assert [row.type for row in rows] == ["reminder_t3d", "reminder_t1d"]
    assert [row.scheduled_at for row in rows] == [
        datetime(2026, 11, 7, 14, 0, tzinfo=UTC),
        datetime(2026, 11, 9, 14, 0, tzinfo=UTC),
    ]
    assert [row.occurrence for row in rows] == [
        date(2026, 11, 7),
        date(2026, 11, 9),
    ]


async def test_an_undated_confirmed_task_inserts_no_occurrence(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    task = await make_task(db_session, actor)
    task.status = "confirmed"
    await db_session.flush()

    result = await _schedule(db_session, task)

    assert result == ReminderSchedule(planned=0, inserted=0, cancelled=0)
    assert await _stored(db_session, task.id) == []


async def test_removing_the_date_retires_the_generation_that_was_armed(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    task = await _confirmed_timed(db_session, actor)
    await _schedule(db_session, task)

    task.due_version = 2
    task.date_status = "missing"
    task.due_at = None
    task.due_date = None
    await db_session.flush()
    result = await _schedule(db_session, task)

    assert result == ReminderSchedule(planned=0, inserted=0, cancelled=3)
    rows = await _stored(db_session, task.id)
    assert {row.status for row in rows} == {"cancelled"}


async def test_the_adapter_uses_the_database_clock_when_now_is_omitted(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    task = await _confirmed_timed(db_session, actor)
    task.due_at = datetime.now(UTC) + timedelta(days=365)
    await db_session.flush()

    result = await schedule_task_reminders(
        db_session,
        ReminderTask.from_task(task),
        user_timezone=BOGOTA,
    )

    assert result == ReminderSchedule(planned=3, inserted=3, cancelled=0)


async def test_the_user_thresholds_are_read_from_the_settings_row(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    absent = await load_reminder_preferences(db_session, actor.user_id)
    settings = CalendarSettings(
        user_id=actor.user_id,
        reminder_offsets_hours=[24],
        reminder_allday_offsets_days=[1],
    )
    db_session.add(settings)
    await db_session.flush()

    stored = await load_reminder_preferences(db_session, actor.user_id)

    assert absent == DEFAULT_REMINDER_PREFERENCES
    assert stored == ReminderPreferences(
        reminder_offsets_hours=(24,), reminder_allday_offsets_days=(1,)
    )


async def test_only_the_user_thresholds_are_inserted(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    task = await _confirmed_timed(db_session, actor)
    preferences = ReminderPreferences(
        reminder_offsets_hours=(24,), reminder_allday_offsets_days=(3,)
    )

    result = await _schedule(db_session, task, preferences=preferences)

    assert result == ReminderSchedule(planned=1, inserted=1, cancelled=0)
    rows = await _stored(db_session, task.id)
    assert [row.type for row in rows] == ["reminder_t24"]

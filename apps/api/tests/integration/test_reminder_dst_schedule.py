"""J4.4 against PostgreSQL: DST occurrences persist once with their canonical dedupe key.

U-S4-JF-02 proves the pure planner; this module proves the J4.3 adapter on the real schema:

- an all-day target on a transition day keeps the user's local wall clock and changes only
  the UTC instant, and a repeated confirmation does not duplicate it;
- a nonexistent local hour is armed at the first valid instant after the gap;
- a repeated local hour is stored once, at its first fold;
- a new `due_version` still cancels the DST generation and arms the new one (A4.3).

No Gmail/Google call, credential or secret is involved.
"""

import uuid
from datetime import UTC, date, datetime, time

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Notification, Task
from app.services.notifications import dedupe_key
from app.services.reminders import (
    ReminderSchedule,
    ReminderTask,
    schedule_task_reminders,
)
from tests.integration.actors import Actor, make_actor, make_task

pytestmark = pytest.mark.integration

SPRING_NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
FALL_NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
NEW_YORK = "America/New_York"


async def _confirmed_allday(
    db: AsyncSession, actor: Actor, due_date: date
) -> Task:
    task = await make_task(db, actor)
    task.status = "confirmed"
    task.all_day = True
    task.date_status = "resolved"
    task.due_date = due_date
    task.due_at = None
    await db.flush()
    return task


async def _schedule(
    db: AsyncSession,
    task: Task,
    *,
    now: datetime,
    local_time: time = time(9, 0),
) -> ReminderSchedule:
    return await schedule_task_reminders(
        db,
        ReminderTask.from_task(task),
        user_timezone=NEW_YORK,
        now=now,
        local_time=local_time,
    )


async def _stored(db: AsyncSession, task_id: uuid.UUID) -> list[Notification]:
    return list(
        (
            await db.scalars(
                select(Notification)
                .where(Notification.task_id == task_id)
                .order_by(Notification.scheduled_at)
                .execution_options(populate_existing=True)
            )
        ).all()
    )


async def test_a_transition_day_all_day_target_is_persisted_once(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    task = await _confirmed_allday(db_session, actor, date(2026, 3, 11))

    first = await _schedule(db_session, task, now=SPRING_NOW)
    again = await _schedule(db_session, task, now=SPRING_NOW)

    assert first == ReminderSchedule(planned=2, inserted=2, cancelled=0)
    assert again == ReminderSchedule(planned=2, inserted=0, cancelled=0)
    rows = await _stored(db_session, task.id)
    assert [(row.type, row.scheduled_at, row.occurrence) for row in rows] == [
        ("reminder_t3d", datetime(2026, 3, 8, 13, 0, tzinfo=UTC), date(2026, 3, 8)),
        ("reminder_t1d", datetime(2026, 3, 10, 13, 0, tzinfo=UTC), date(2026, 3, 10)),
    ]
    assert {row.status for row in rows} == {"pending"}
    assert [row.dedupe_key for row in rows] == [
        dedupe_key(
            user_id=actor.user_id,
            type="reminder_t3d",
            task_id=task.id,
            due_version=1,
            occurrence=date(2026, 3, 8),
        ),
        dedupe_key(
            user_id=actor.user_id,
            type="reminder_t1d",
            task_id=task.id,
            due_version=1,
            occurrence=date(2026, 3, 10),
        ),
    ]


async def test_a_nonexistent_local_hour_is_armed_at_the_gap_end(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    task = await _confirmed_allday(db_session, actor, date(2026, 3, 11))

    first = await _schedule(
        db_session, task, now=SPRING_NOW, local_time=time(2, 30)
    )
    again = await _schedule(
        db_session, task, now=SPRING_NOW, local_time=time(2, 30)
    )

    assert first == ReminderSchedule(planned=2, inserted=2, cancelled=0)
    assert again == ReminderSchedule(planned=2, inserted=0, cancelled=0)
    rows = await _stored(db_session, task.id)
    # 02:30 does not exist on 2026-03-08; the gap ends at 03:00 EDT (07:00 UTC).
    assert rows[0].scheduled_at == datetime(2026, 3, 8, 7, 0, tzinfo=UTC)
    assert rows[0].occurrence == date(2026, 3, 8)
    assert len(rows) == 2


async def test_a_repeated_local_hour_is_persisted_once_at_the_first_fold(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    task = await _confirmed_allday(db_session, actor, date(2026, 11, 4))

    first = await _schedule(
        db_session, task, now=FALL_NOW, local_time=time(1, 30)
    )
    again = await _schedule(
        db_session, task, now=FALL_NOW, local_time=time(1, 30)
    )

    assert first == ReminderSchedule(planned=2, inserted=2, cancelled=0)
    assert again == ReminderSchedule(planned=2, inserted=0, cancelled=0)
    rows = await _stored(db_session, task.id)
    assert len(rows) == 2
    assert rows[0].scheduled_at == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    assert rows[0].occurrence == date(2026, 11, 1)
    assert datetime(2026, 11, 1, 6, 30, tzinfo=UTC) not in [
        row.scheduled_at for row in rows
    ]
    assert len({row.dedupe_key for row in rows}) == len(rows)


async def test_a_new_due_version_cancels_the_dst_generation(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    task = await _confirmed_allday(db_session, actor, date(2026, 11, 4))
    await _schedule(db_session, task, now=FALL_NOW, local_time=time(1, 30))
    old = {row.id for row in await _stored(db_session, task.id)}

    task.due_version = 2
    task.due_date = date(2026, 11, 5)
    await db_session.flush()
    result = await _schedule(
        db_session, task, now=FALL_NOW, local_time=time(1, 30)
    )

    assert result == ReminderSchedule(planned=2, inserted=2, cancelled=2)
    rows = await _stored(db_session, task.id)
    assert len(rows) == 4
    assert {row.status for row in rows if row.id in old} == {"cancelled"}
    assert {row.status for row in rows if row.id not in old} == {"pending"}
    assert {row.due_version for row in rows if row.id not in old} == {2}
    assert [
        row.scheduled_at for row in rows if row.id not in old
    ] == [
        datetime(2026, 11, 2, 6, 30, tzinfo=UTC),
        datetime(2026, 11, 4, 6, 30, tzinfo=UTC),
    ]

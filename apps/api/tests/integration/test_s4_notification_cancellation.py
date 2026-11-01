"""A4.3 against PostgreSQL: the obsolete-occurrence cancellation is persisted, not simulated.

The unit suite U-S4-AN-03 proves the decision core; this module proves the A-area adapter
writes exactly that decision after the real schema and CHECK constraints:

- a new `due_version` cancels the previous generation of the same task and nothing else;
- a preference change cancels the pending reminders/digests whose type was disabled, while
  an already `sent` occurrence and another tenant's rows are untouched;
- a stale row past its instant is cancelled too, closing the retrospective burst;
- the write is idempotent and a `sending` row is never rewritten.

No provider, credential, Gmail message or secret is involved.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Notification, Task
from app.services.notification_cancellation import SqlNotificationCancellationStore
from app.services.notifications import (
    NotificationTransitionError,
    claim,
    dedupe_key,
    enabled_preference_types,
)
from tests.integration.actors import Actor, make_actor, make_task

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
OCCURRENCE = date(2026, 11, 9)


async def _reminder(
    db: AsyncSession,
    actor: Actor,
    task: Task,
    *,
    row_type: str = "reminder_t24",
    due_version: int = 1,
    occurrence: date = OCCURRENCE,
    status: str = "pending",
    scheduled_at: datetime = NOW,
) -> Notification:
    values: dict[str, Any] = {
        "user_id": actor.user_id,
        "task_id": task.id,
        "type": row_type,
        "occurrence": occurrence,
        "due_version": due_version,
        "scheduled_at": scheduled_at,
        "dedupe_key": dedupe_key(
            user_id=actor.user_id,
            type=row_type,
            task_id=task.id,
            due_version=due_version,
            occurrence=occurrence,
        ),
        "status": status,
    }
    if status == "sent":
        values["sent_at"] = NOW - timedelta(hours=1)
        values["provider_message_id"] = f"gmail-{uuid.uuid4().hex[:8]}"
    if status == "sending":
        values["lease_expires_at"] = NOW + timedelta(minutes=5)
        values["attempts"] = 1
    row = Notification(**values)
    db.add(row)
    await db.flush()
    return row


async def _task_less(
    db: AsyncSession,
    actor: Actor,
    *,
    row_type: str = "digest_daily",
    occurrence: date = OCCURRENCE,
) -> Notification:
    row = Notification(
        user_id=actor.user_id,
        task_id=None,
        type=row_type,
        occurrence=occurrence,
        due_version=None,
        scheduled_at=NOW,
        dedupe_key=dedupe_key(
            user_id=actor.user_id,
            type=row_type,
            task_id=None,
            due_version=None,
            occurrence=occurrence,
        ),
    )
    db.add(row)
    await db.flush()
    return row


async def _stored_statuses(
    db: AsyncSession, rows: list[Notification]
) -> dict[uuid.UUID, str]:
    ids = [row.id for row in rows]
    result = await db.execute(
        select(Notification.id, Notification.status).where(Notification.id.in_(ids))
    )
    return {row.id: row.status for row in result}


def _enabled(**overrides: Any) -> frozenset[str]:
    values: dict[str, Any] = {
        "reminder_offsets_hours": (48, 24, 2),
        "reminder_allday_offsets_days": (3, 1),
        "digest_daily_enabled": True,
        "digest_weekly_enabled": True,
    }
    values.update(overrides)
    return enabled_preference_types(**values)


# --- Due-date generation ---------------------------------------------------------------


async def test_a_new_due_generation_cancels_only_the_older_pending_rows(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    task = await make_task(db_session, actor)
    other_task = await make_task(db_session, actor)

    old_pending = await _reminder(db_session, actor, task, due_version=1)
    old_sent = await _reminder(
        db_session, actor, task, row_type="reminder_t48", due_version=1, status="sent"
    )
    current = await _reminder(db_session, actor, task, due_version=2)
    other_task_row = await _reminder(db_session, actor, other_task, due_version=1)
    digest = await _task_less(db_session, actor)
    rows = [old_pending, old_sent, current, other_task_row, digest]

    store = SqlNotificationCancellationStore(db_session)
    assert (
        await store.cancel_obsolete(actor.user_id, task_id=task.id, due_version=2) == 1
    )

    statuses = await _stored_statuses(db_session, rows)
    assert statuses[old_pending.id] == "cancelled"
    assert statuses[old_sent.id] == "sent"
    assert statuses[current.id] == "pending"
    assert statuses[other_task_row.id] == "pending"
    # A due-date rule of one task never reaches the task-less digests of the user.
    assert statuses[digest.id] == "pending"


async def test_a_stale_occurrence_past_its_instant_is_cancelled_and_cannot_be_claimed(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    task = await make_task(db_session, actor)
    row = await _reminder(
        db_session, actor, task, due_version=1, scheduled_at=NOW - timedelta(hours=3)
    )

    store = SqlNotificationCancellationStore(db_session)
    assert (
        await store.cancel_obsolete(actor.user_id, task_id=task.id, due_version=2) == 1
    )

    stored = await db_session.get(Notification, row.id)
    assert stored is not None
    await db_session.refresh(stored)
    assert stored.status == "cancelled"
    # The stale row is terminal: the scheduler cannot claim it and send the burst late.
    with pytest.raises(NotificationTransitionError):
        claim(stored, now=NOW, lease_expires_at=NOW + timedelta(minutes=5))


async def test_a_sending_occurrence_is_never_rewritten_by_a_generation_change(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    task = await make_task(db_session, actor)
    in_flight = await _reminder(db_session, actor, task, due_version=1, status="sending")

    store = SqlNotificationCancellationStore(db_session)
    assert await store.cancel_obsolete(actor.user_id, task_id=task.id, due_version=2) == 0
    assert (await _stored_statuses(db_session, [in_flight]))[in_flight.id] == "sending"


async def test_a_second_cancellation_is_idempotent(db_session: AsyncSession) -> None:
    actor = await make_actor(db_session)
    task = await make_task(db_session, actor)
    row = await _reminder(db_session, actor, task, due_version=1)

    store = SqlNotificationCancellationStore(db_session)
    assert await store.cancel_obsolete(actor.user_id, task_id=task.id, due_version=2) == 1
    assert await store.cancel_obsolete(actor.user_id, task_id=task.id, due_version=2) == 0
    assert (await _stored_statuses(db_session, [row]))[row.id] == "cancelled"


# --- Preferences -----------------------------------------------------------------------


async def test_a_preference_change_cancels_disabled_reminders_and_digests(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    other_actor = await make_actor(db_session)
    task = await make_task(db_session, actor)
    other_task = await make_task(db_session, other_actor)

    t48 = await _reminder(db_session, actor, task, row_type="reminder_t48")
    t24 = await _reminder(db_session, actor, task, row_type="reminder_t24")
    t2 = await _reminder(db_session, actor, task, row_type="reminder_t2")
    daily = await _task_less(db_session, actor)
    weekly = await _task_less(db_session, actor, row_type="digest_weekly")
    test_send = await _task_less(db_session, actor, row_type="test")
    foreign = await _reminder(db_session, other_actor, other_task, row_type="reminder_t2")
    rows = [t48, t24, t2, daily, weekly, test_send, foreign]

    store = SqlNotificationCancellationStore(db_session)
    enabled = _enabled(reminder_offsets_hours=(48, 24), digest_weekly_enabled=False)
    assert await store.cancel_obsolete(actor.user_id, enabled_types=enabled) == 2

    statuses = await _stored_statuses(db_session, rows)
    assert statuses[t48.id] == "pending"
    assert statuses[t24.id] == "pending"
    assert statuses[t2.id] == "cancelled"
    assert statuses[daily.id] == "pending"
    assert statuses[weekly.id] == "cancelled"
    # An explicit test send is not preference-driven and another tenant is untouched.
    assert statuses[test_send.id] == "pending"
    assert statuses[foreign.id] == "pending"


# --- Guards ----------------------------------------------------------------------------


async def test_cancelling_without_a_rule_is_rejected_and_writes_nothing(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    task = await make_task(db_session, actor)
    rows = [
        await _reminder(db_session, actor, task, due_version=1),
        await _reminder(db_session, actor, task, row_type="reminder_t2"),
        await _task_less(db_session, actor),
    ]

    store = SqlNotificationCancellationStore(db_session)
    with pytest.raises(ValueError):
        await store.cancel_obsolete(actor.user_id)
    with pytest.raises(ValueError):
        await store.cancel_obsolete(actor.user_id, due_version=2)
    with pytest.raises(ValueError):
        await store.cancel_obsolete(actor.user_id, task_id=task.id, due_version=0)

    assert set((await _stored_statuses(db_session, rows)).values()) == {"pending"}

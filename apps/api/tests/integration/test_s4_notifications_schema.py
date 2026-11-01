"""I-S4-AN-01/02 (A4.1) against PostgreSQL: the M8 constraints are real.

The unit suites read the SQLAlchemy metadata; this module proves that, after
`alembic upgrade head`, PostgreSQL rejects the rows the contract forbids: a duplicate
occurrence (including NULLS NOT DISTINCT task-less rows), a duplicate dedupe key, an unknown
status/type, an incoherent lease or delivery, a free-text error (PII/payload leak) and a
cross-tenant task link. It also proves that deleting a task cascades to its reminders, and
that the Gmail/digest defaults keep the user opted out.

No audio, provider call, credential or secret is involved.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CalendarSettings, Notification, Task
from app.services.notifications import dedupe_key
from tests.integration.actors import Actor, make_actor, make_task

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
OCCURRENCE = date(2026, 11, 9)


async def _reminder(
    db: AsyncSession, actor: Actor, task: Task, **overrides: Any
) -> Notification:
    """A T-24 reminder of the actor's task with a canonical key unless overridden."""
    occurrence = overrides.get("occurrence", OCCURRENCE)
    values: dict[str, Any] = {
        "user_id": actor.user_id,
        "task_id": task.id,
        "type": "reminder_t24",
        "occurrence": occurrence,
        "due_version": task.due_version,
        "scheduled_at": NOW,
        "dedupe_key": dedupe_key(
            user_id=actor.user_id,
            type=str(overrides.get("type", "reminder_t24")),
            task_id=task.id,
            due_version=task.due_version,
            occurrence=occurrence,
        ),
    }
    values.update(overrides)
    row = Notification(**values)
    db.add(row)
    await db.flush()
    return row


async def _expect_integrity_error(session: AsyncSession, row: object) -> None:
    async with session.begin_nested():
        session.add(row)
        with pytest.raises(IntegrityError):
            await session.flush()


# --- Persisted occurrence --------------------------------------------------------------


async def test_a_pending_notification_persists_its_identity_and_defaults(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    task = await make_task(db_session, actor)
    row = await _reminder(db_session, actor, task)

    stored = await db_session.get(Notification, row.id)
    assert stored is not None
    assert stored.status == "pending"
    assert stored.attempts == 0
    assert stored.lease_expires_at is None
    assert stored.sent_at is None
    assert stored.provider_message_id is None
    assert stored.last_error_code is None
    assert stored.dedupe_key == dedupe_key(
        user_id=actor.user_id,
        type="reminder_t24",
        task_id=task.id,
        due_version=task.due_version,
        occurrence=OCCURRENCE,
    )


# --- Persistent dedupe -----------------------------------------------------------------


async def test_a_duplicate_natural_occurrence_is_rejected_even_with_another_key(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    task = await make_task(db_session, actor)
    await _reminder(db_session, actor, task)

    # A different text spelling must not defeat the frozen natural key.
    await _expect_integrity_error(
        db_session,
        Notification(
            user_id=actor.user_id,
            task_id=task.id,
            type="reminder_t24",
            occurrence=OCCURRENCE,
            due_version=task.due_version,
            scheduled_at=NOW,
            dedupe_key="another-spelling",
        ),
    )


async def test_a_duplicate_dedupe_key_is_rejected_on_its_own(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    task = await make_task(db_session, actor)
    first = await _reminder(db_session, actor, task)

    # A different natural tuple (another local date) cannot reuse the persisted key.
    await _expect_integrity_error(
        db_session,
        Notification(
            user_id=actor.user_id,
            task_id=task.id,
            type="reminder_t24",
            occurrence=OCCURRENCE + timedelta(days=1),
            due_version=task.due_version,
            scheduled_at=NOW,
            dedupe_key=first.dedupe_key,
        ),
    )


async def test_nulls_not_distinct_dedupes_task_less_digests(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    first = Notification(
        user_id=actor.user_id,
        task_id=None,
        type="digest_daily",
        occurrence=OCCURRENCE,
        due_version=None,
        scheduled_at=NOW,
        dedupe_key=dedupe_key(
            user_id=actor.user_id,
            type="digest_daily",
            task_id=None,
            due_version=None,
            occurrence=OCCURRENCE,
        ),
    )
    db_session.add(first)
    await db_session.flush()

    # NULL task/due_version are NOT DISTINCT for this key: one digest per user and date.
    await _expect_integrity_error(
        db_session,
        Notification(
            user_id=actor.user_id,
            task_id=None,
            type="digest_daily",
            occurrence=OCCURRENCE,
            due_version=None,
            scheduled_at=NOW,
            dedupe_key="another-digest-spelling",
        ),
    )


# --- Closed vocabularies ---------------------------------------------------------------


@pytest.mark.parametrize("status", ["queued", "retry", "claimed"])
async def test_an_unknown_status_is_rejected(db_session: AsyncSession, status: str) -> None:
    actor = await make_actor(db_session)
    task = await make_task(db_session, actor)
    await _expect_integrity_error(
        db_session,
        Notification(
            user_id=actor.user_id,
            task_id=task.id,
            type="reminder_t24",
            occurrence=OCCURRENCE,
            due_version=task.due_version,
            scheduled_at=NOW,
            dedupe_key=f"status-{status}",
            status=status,
        ),
    )


@pytest.mark.parametrize("type", ["reminder_t12", "digest", "email"])
async def test_an_unknown_type_is_rejected(db_session: AsyncSession, type: str) -> None:
    actor = await make_actor(db_session)
    await _expect_integrity_error(
        db_session,
        Notification(
            user_id=actor.user_id,
            type=type,
            occurrence=OCCURRENCE,
            scheduled_at=NOW,
            dedupe_key=f"type-{type}",
        ),
    )


async def test_task_and_due_generation_are_coherent_per_type(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    task = await make_task(db_session, actor)

    # A digest never belongs to a task.
    await _expect_integrity_error(
        db_session,
        Notification(
            user_id=actor.user_id,
            task_id=task.id,
            type="digest_daily",
            occurrence=OCCURRENCE,
            due_version=None,
            scheduled_at=NOW,
            dedupe_key="digest-with-task",
        ),
    )
    # A generation without a task is meaningless, and a linked task needs its generation.
    await _expect_integrity_error(
        db_session,
        Notification(
            user_id=actor.user_id,
            type="digest_daily",
            occurrence=OCCURRENCE,
            due_version=1,
            scheduled_at=NOW,
            dedupe_key="digest-with-generation",
        ),
    )
    await _expect_integrity_error(
        db_session,
        Notification(
            user_id=actor.user_id,
            task_id=task.id,
            type="reminder_t24",
            occurrence=OCCURRENCE,
            due_version=None,
            scheduled_at=NOW,
            dedupe_key="reminder-without-generation",
        ),
    )
    await _expect_integrity_error(
        db_session,
        Notification(
            user_id=actor.user_id,
            type="reminder_t24",
            occurrence=OCCURRENCE,
            due_version=0,
            scheduled_at=NOW,
            dedupe_key="reminder-zero-generation",
        ),
    )
    # An unlinked reminder is not a valid persisted shape: deleting its task cascades.
    await _expect_integrity_error(
        db_session,
        Notification(
            user_id=actor.user_id,
            task_id=None,
            type="reminder_t24",
            occurrence=OCCURRENCE,
            due_version=None,
            scheduled_at=NOW,
            dedupe_key="unlinked-reminder",
        ),
    )


# --- Lease and delivery coherence ------------------------------------------------------


async def test_the_lease_check_separates_live_sending_from_pending(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    task = await make_task(db_session, actor)

    # pending with a lease is incoherent.
    await _expect_integrity_error(
        db_session,
        Notification(
            user_id=actor.user_id,
            task_id=task.id,
            type="reminder_t24",
            occurrence=OCCURRENCE,
            due_version=task.due_version,
            scheduled_at=NOW,
            dedupe_key="pending-with-lease",
            lease_expires_at=NOW + timedelta(minutes=5),
        ),
    )
    # sending without a lease is incoherent.
    await _expect_integrity_error(
        db_session,
        Notification(
            user_id=actor.user_id,
            task_id=task.id,
            type="reminder_t24",
            occurrence=OCCURRENCE + timedelta(days=1),
            due_version=task.due_version,
            scheduled_at=NOW,
            dedupe_key="sending-without-lease",
            status="sending",
        ),
    )
    # sending with a bounded lease is the live claim.
    claimed = await _reminder(
        db_session,
        actor,
        task,
        type="reminder_t48",
        occurrence=OCCURRENCE + timedelta(days=2),
        status="sending",
        lease_expires_at=NOW + timedelta(minutes=5),
        attempts=1,
    )
    assert claimed.status == "sending"


async def test_a_sent_row_proves_its_delivery_time_and_message(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    task = await make_task(db_session, actor)

    sent = await _reminder(
        db_session,
        actor,
        task,
        type="reminder_t2",
        occurrence=OCCURRENCE + timedelta(days=3),
        status="sent",
        sent_at=NOW,
        provider_message_id="gmail-msg-1",
        attempts=1,
    )
    assert sent.sent_at == NOW

    await _expect_integrity_error(
        db_session,
        Notification(
            user_id=actor.user_id,
            task_id=task.id,
            type="reminder_t2",
            occurrence=OCCURRENCE + timedelta(days=4),
            due_version=task.due_version,
            scheduled_at=NOW,
            dedupe_key="sent-without-time",
            status="sent",
        ),
    )
    await _expect_integrity_error(
        db_session,
        Notification(
            user_id=actor.user_id,
            task_id=task.id,
            type="reminder_t2",
            occurrence=OCCURRENCE + timedelta(days=5),
            due_version=task.due_version,
            scheduled_at=NOW,
            dedupe_key="pending-with-provider-id",
            provider_message_id="gmail-msg-2",
        ),
    )


# --- Redacted errors -------------------------------------------------------------------


async def test_only_redacted_error_tokens_are_persisted(db_session: AsyncSession) -> None:
    actor = await make_actor(db_session)
    task = await make_task(db_session, actor)

    failed = await _reminder(
        db_session,
        actor,
        task,
        type="reminder_t48",
        occurrence=OCCURRENCE + timedelta(days=1),
        status="failed",
        last_error_code="provider_rejected",
        attempts=1,
    )
    assert failed.last_error_code == "provider_rejected"

    # Free text — an address, a body, spaces, uppercase — is not a token.
    await _expect_integrity_error(
        db_session,
        Notification(
            user_id=actor.user_id,
            task_id=task.id,
            type="reminder_t48",
            occurrence=OCCURRENCE + timedelta(days=2),
            due_version=task.due_version,
            scheduled_at=NOW,
            dedupe_key="failed-with-free-text",
            status="failed",
            last_error_code="Bob <bob@example.test> rejected the body",
        ),
    )
    # A pending occurrence has no error history yet.
    await _expect_integrity_error(
        db_session,
        Notification(
            user_id=actor.user_id,
            task_id=task.id,
            type="reminder_t48",
            occurrence=OCCURRENCE + timedelta(days=3),
            due_version=task.due_version,
            scheduled_at=NOW,
            dedupe_key="pending-with-error",
            last_error_code="provider_rejected",
        ),
    )


# --- Tenant isolation and task cascade --------------------------------------------------


async def test_a_notification_cannot_link_another_users_task(
    db_session: AsyncSession,
) -> None:
    alice, bob = await make_actor(db_session), await make_actor(db_session)
    task = await make_task(db_session, alice)

    await _expect_integrity_error(
        db_session,
        Notification(
            user_id=bob.user_id,
            task_id=task.id,
            type="reminder_t24",
            occurrence=OCCURRENCE,
            due_version=task.due_version,
            scheduled_at=NOW,
            dedupe_key="cross-tenant",
        ),
    )


async def test_deleting_a_task_deletes_its_notifications(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    task = await make_task(db_session, actor)
    row = await _reminder(db_session, actor, task)
    notification_id = row.id

    await db_session.execute(text("DELETE FROM tasks WHERE id = :id"), {"id": task.id})
    await db_session.flush()
    # The delete ran as raw SQL, so the ORM identity map still holds the old row.
    db_session.expire_all()

    assert (
        await db_session.scalar(
            select(Notification).where(Notification.id == notification_id)
        )
    ) is None


async def test_deleting_the_user_cascades_tasks_and_notifications(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    task = await make_task(db_session, actor)
    row = await _reminder(db_session, actor, task)
    task_id, notification_id = task.id, row.id

    # Account deletion (A4.4) already relies on the user cascade; the extra task -> notification
    # edge must not leave the two cascade paths fighting each other.
    await db_session.execute(text("DELETE FROM users WHERE id = :id"), {"id": actor.user_id})
    await db_session.flush()
    db_session.expire_all()

    assert await db_session.get(Task, task_id) is None
    assert await db_session.get(Notification, notification_id) is None


# --- settings extension ----------------------------------------------------------------


async def test_settings_extend_the_same_row_opted_out_by_default(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    row = CalendarSettings(user_id=actor.user_id)
    db_session.add(row)
    await db_session.flush()
    await db_session.refresh(row)

    assert row.gmail_opt_in is False
    assert row.digest_daily_enabled is False
    assert str(row.digest_daily_time) == "07:00:00"
    assert row.digest_weekly_enabled is False
    assert row.digest_weekday == 0
    assert row.reminder_offsets_hours == [48, 24, 2]
    assert row.reminder_allday_offsets_days == [3, 1]


async def test_settings_reject_an_out_of_range_weekday_and_scalar_thresholds(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    await _expect_integrity_error(
        db_session, CalendarSettings(user_id=actor.user_id, digest_weekday=7)
    )
    await _expect_integrity_error(
        db_session,
        CalendarSettings(user_id=actor.user_id, reminder_offsets_hours={"hours": 48}),
    )


async def test_the_due_index_is_partial_on_pending(db_session: AsyncSession) -> None:
    definition = await db_session.scalar(
        text(
            "SELECT indexdef FROM pg_indexes "
            "WHERE schemaname = 'public' AND indexname = 'notifications_due'"
        )
    )
    assert definition is not None
    assert "(status, scheduled_at) WHERE (status = 'pending'::text)" in str(definition)

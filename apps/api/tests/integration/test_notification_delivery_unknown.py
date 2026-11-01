"""I-S4-JF-05 (J4.5): a sender outcome is settled once and never blindly retried.

The suite runs against the real PostgreSQL schema and the real HTTP routes. Gmail is a
deterministic sender double behind the `NotificationSender` contract, so no credential or
provider call is involved. The contract under test is the delivery side of I-S4-JF-05:

- a lost response (or any unexpected sender exception) ends `delivery_unknown` with a
  redacted `response_lost` code and no pending row is created;
- a retried settlement of a terminal occurrence is a no-op, so an at-least-once job cannot
  duplicate the email;
- an expired `sending` lease is uncertain without calling the sender at all;
- only a sender that proves a non-delivery may mark `failed`;
- the A4.2 warned resend is the only way back: it creates a new audited occurrence and the
  original uncertainty stays visible and unchanged.

No real email, credential, class audio or `apps/web` change is involved.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.main import app
from app.models import Notification
from app.services.notification_delivery import DeliveryRequest, settle_delivery
from app.services.notifications import (
    NotificationDeliveryAmbiguous,
    NotificationDeliveryRejected,
    claim,
    dedupe_key,
)
from app.services.scheduler_lease import (
    claim_due_notifications,
    expire_delivery_leases,
)
from tests.integration.actors import Actor, make_actor, make_task

pytestmark = pytest.mark.integration

NOTIFICATIONS = "/notifications"
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
OCCURRENCE = date(2026, 11, 9)


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    async def override() -> AsyncIterator[AsyncSession]:
        yield db_session

    # Handlers commit inside the fixture's savepoint; the outer transaction rolls back.
    app.dependency_overrides[get_session] = override
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://app.maulwurf.test"
    ) as c:
        yield c
    app.dependency_overrides.clear()


class StubSender:
    """Deterministic `NotificationSender`; it never talks to a provider."""

    def __init__(self, outcome: str = "sent") -> None:
        self.outcome = outcome
        self.calls: list[uuid.UUID] = []

    async def send(self, request: DeliveryRequest) -> str:
        self.calls.append(request.notification_id)
        if self.outcome == "sent":
            return f"gmail-{request.notification_id}"
        if self.outcome == "lost":
            raise NotificationDeliveryAmbiguous()
        if self.outcome == "timeout":
            raise TimeoutError("the provider response was lost")
        if self.outcome == "rejected":
            raise NotificationDeliveryRejected()
        if self.outcome == "blank":
            return "   "
        if self.outcome == "cancelled":
            raise asyncio.CancelledError()
        raise AssertionError(f"unknown stub outcome: {self.outcome}")


def _notification(
    actor: Actor,
    *,
    status: str = "pending",
    scheduled_at: datetime = NOW,
    sequence: int = 0,
    attempts: int = 0,
    lease_expires_at: datetime | None = None,
    sent_at: datetime | None = None,
    provider_message_id: str | None = None,
    last_error_code: str | None = None,
) -> Notification:
    return Notification(
        user_id=actor.user_id,
        task_id=None,
        type="test",
        occurrence=OCCURRENCE,
        due_version=None,
        scheduled_at=scheduled_at,
        dedupe_key=dedupe_key(
            user_id=actor.user_id,
            type="test",
            task_id=None,
            due_version=None,
            occurrence=OCCURRENCE,
            occurrence_seq=sequence,
        ),
        occurrence_seq=sequence,
        status=status,
        attempts=attempts,
        sent_at=sent_at,
        provider_message_id=provider_message_id,
        last_error_code=last_error_code,
        lease_expires_at=lease_expires_at,
    )


async def _store(db: AsyncSession, row: Notification) -> Notification:
    db.add(row)
    await db.flush()
    return row


async def _claim(db: AsyncSession, row: Notification) -> None:
    claim(row, now=NOW, lease_expires_at=NOW + timedelta(minutes=5))
    await db.flush()


async def _reload(db: AsyncSession, notification_id: uuid.UUID) -> Notification:
    """Re-read after an HTTP handler committed: a plain `get` returns the expired instance."""
    result = await db.execute(
        select(Notification)
        .where(Notification.id == notification_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one()


async def _count(db: AsyncSession, actor: Actor, *, status: str) -> int:
    return int(
        await db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.user_id == actor.user_id,
                Notification.status == status,
            )
        )
        or 0
    )


async def test_a_lost_response_is_terminal_uncertainty_and_the_retry_is_a_no_op(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await make_actor(db_session)
    row = await _store(db_session, _notification(actor))
    await _claim(db_session, row)
    notification_id = row.id
    sender = StubSender("lost")

    report = await settle_delivery(
        db_session, notification_id, sender=sender, now=NOW
    )

    stored = await db_session.get(Notification, notification_id)
    assert stored is not None
    assert report.outcome == "delivery_unknown"
    assert report.status == "delivery_unknown"
    assert report.error_code == "response_lost"
    assert stored.status == "delivery_unknown"
    assert stored.last_error_code == "response_lost"
    assert stored.attempts == 1
    assert stored.sent_at is None
    assert stored.provider_message_id is None
    assert sender.calls == [notification_id]

    retry = await settle_delivery(
        db_session, notification_id, sender=sender, now=NOW
    )
    assert retry.outcome == "skipped"
    assert retry.status == "delivery_unknown"
    # The at-least-once job may be retried; the occurrence is never sent again.
    assert sender.calls == [notification_id]
    await db_session.refresh(stored)
    assert stored.status == "delivery_unknown"
    assert await _count(db_session, actor, status="pending") == 0
    assert await expire_delivery_leases(db_session) == 0
    # A later scheduler tick only selects pending rows: the uncertain delivery is not
    # reclaimed and therefore never reaches the sender again.
    assert (
        await claim_due_notifications(
            db_session,
            fencing_token=1,
            now=NOW + timedelta(minutes=1),
        )
        == []
    )

    page = await client.get(NOTIFICATIONS, cookies=actor.cookies)
    item = page.json()["items"][0]
    assert item["status"] == "delivery_unknown"
    assert item["last_error_code"] == "response_lost"


async def test_an_unexpected_sender_exception_is_uncertain_delivery(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    row = await _store(db_session, _notification(actor))
    await _claim(db_session, row)
    notification_id = row.id
    sender = StubSender("timeout")

    report = await settle_delivery(
        db_session, notification_id, sender=sender, now=NOW
    )

    stored = await db_session.get(Notification, notification_id)
    assert stored is not None
    assert report.outcome == "delivery_unknown"
    assert stored.last_error_code == "response_lost"
    assert sender.calls == [notification_id]


async def test_an_expired_lease_is_uncertain_without_calling_the_sender(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    row = await _store(
        db_session,
        _notification(
            actor,
            status="sending",
            attempts=1,
            lease_expires_at=NOW - timedelta(seconds=1),
        ),
    )
    notification_id = row.id
    sender = StubSender()

    report = await settle_delivery(
        db_session, notification_id, sender=sender, now=NOW
    )

    stored = await db_session.get(Notification, notification_id)
    assert stored is not None
    assert report.outcome == "delivery_unknown"
    assert report.error_code == "lease_expired"
    assert stored.status == "delivery_unknown"
    assert stored.last_error_code == "lease_expired"
    # A lease that cannot prove an owner never reaches the provider.
    assert sender.calls == []
    assert stored.status != "pending"


async def test_a_cancelled_send_waits_for_the_lease_and_never_returns_to_pending(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    row = await _store(db_session, _notification(actor))
    await _claim(db_session, row)
    notification_id = row.id
    sender = StubSender("cancelled")

    # CancelledError is a BaseException: it must not be swallowed as a normal outcome.
    with pytest.raises(asyncio.CancelledError):
        await settle_delivery(db_session, notification_id, sender=sender, now=NOW)

    stored = await db_session.get(Notification, notification_id)
    assert stored is not None
    assert stored.status == "sending"
    assert stored.last_error_code is None
    assert await _count(db_session, actor, status="pending") == 0
    assert sender.calls == [notification_id]

    # The scheduler later finds the claim without a live owner and closes it as uncertain.
    assert await expire_delivery_leases(
        db_session, now=NOW + timedelta(minutes=6)
    ) == 1
    stored = await _reload(db_session, notification_id)
    assert stored.status == "delivery_unknown"
    assert stored.last_error_code == "lease_expired"
    assert stored.status != "pending"


async def test_only_a_proven_rejection_is_failed(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    row = await _store(db_session, _notification(actor))
    await _claim(db_session, row)
    notification_id = row.id
    sender = StubSender("rejected")

    report = await settle_delivery(
        db_session, notification_id, sender=sender, now=NOW
    )

    stored = await db_session.get(Notification, notification_id)
    assert stored is not None
    assert report.outcome == "failed"
    assert report.error_code == "provider_rejected"
    assert stored.status == "failed"
    assert stored.last_error_code == "provider_rejected"
    assert stored.sent_at is None
    assert sender.calls == [notification_id]

    retry = await settle_delivery(
        db_session, notification_id, sender=sender, now=NOW
    )
    assert retry.outcome == "skipped"
    assert sender.calls == [notification_id]


async def test_a_confirmed_send_is_recorded_once(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    row = await _store(db_session, _notification(actor))
    await _claim(db_session, row)
    notification_id = row.id
    sender = StubSender("sent")

    report = await settle_delivery(
        db_session, notification_id, sender=sender, now=NOW
    )

    stored = await db_session.get(Notification, notification_id)
    assert stored is not None
    assert report.outcome == "sent"
    assert stored.status == "sent"
    assert stored.sent_at == NOW
    assert stored.provider_message_id == f"gmail-{notification_id}"
    assert stored.last_error_code is None

    retry = await settle_delivery(
        db_session, notification_id, sender=sender, now=NOW
    )
    assert retry.outcome == "skipped"
    assert retry.status == "sent"
    assert sender.calls == [notification_id]


async def test_a_success_without_a_provider_identity_is_uncertain(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    row = await _store(db_session, _notification(actor))
    await _claim(db_session, row)
    notification_id = row.id
    sender = StubSender("blank")

    report = await settle_delivery(
        db_session, notification_id, sender=sender, now=NOW
    )

    stored = await db_session.get(Notification, notification_id)
    assert stored is not None
    # A confirmed delivery must prove its provider identity; fail closed otherwise.
    assert report.outcome == "delivery_unknown"
    assert stored.status == "delivery_unknown"
    assert stored.sent_at is None
    assert stored.provider_message_id is None
    assert sender.calls == [notification_id]


async def test_a_row_that_was_never_claimed_is_not_sent_by_a_settlement(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    row = await _store(db_session, _notification(actor))
    notification_id = row.id
    sender = StubSender()

    report = await settle_delivery(
        db_session, notification_id, sender=sender, now=NOW
    )

    stored = await db_session.get(Notification, notification_id)
    assert stored is not None
    assert report.outcome == "skipped"
    assert report.status == "pending"
    assert sender.calls == []


async def test_a_lost_response_can_only_return_through_the_warned_resend(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await make_actor(db_session)
    task = await make_task(db_session, actor)
    row = await _store(
        db_session,
        Notification(
            user_id=actor.user_id,
            task_id=task.id,
            type="reminder_t24",
            occurrence=OCCURRENCE,
            due_version=task.due_version,
            scheduled_at=NOW,
            dedupe_key=dedupe_key(
                user_id=actor.user_id,
                type="reminder_t24",
                task_id=task.id,
                due_version=task.due_version,
                occurrence=OCCURRENCE,
                occurrence_seq=0,
            ),
            occurrence_seq=0,
            status="pending",
        ),
    )
    await _claim(db_session, row)
    original_id = row.id
    original_key = row.dedupe_key
    original_sender = StubSender("lost")
    await settle_delivery(db_session, original_id, sender=original_sender, now=NOW)

    original = await _reload(db_session, original_id)
    original_updated_at = original.updated_at
    original_attempts = original.attempts

    refused = await client.post(
        f"{NOTIFICATIONS}/{original_id}/resend",
        json={"acknowledge_duplicate_risk": False},
        cookies=actor.cookies,
        headers=actor.headers,
    )
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "acknowledgement_required"

    accepted = await client.post(
        f"{NOTIFICATIONS}/{original_id}/resend",
        json={"acknowledge_duplicate_risk": True},
        cookies=actor.cookies,
        headers=actor.headers,
    )
    assert accepted.status_code == 202
    created = accepted.json()
    assert created["status"] == "pending"
    assert created["occurrence_seq"] == 1
    assert created["warning"]["code"] == "duplicate_risk_acknowledged"

    new_id = uuid.UUID(created["id"])
    new_row = await _reload(db_session, new_id)
    assert new_row.dedupe_key == f"{original_key}|n:1"
    assert new_row.dedupe_key != original_key

    original = await _reload(db_session, original_id)
    assert original.status == "delivery_unknown"
    assert original.last_error_code == "response_lost"
    assert original.dedupe_key == original_key
    assert original.attempts == original_attempts
    assert original.updated_at == original_updated_at

    # The new occurrence has its own claim and delivery; the original stays untouched.
    new_sender = StubSender("sent")
    await _claim(db_session, new_row)
    report = await settle_delivery(
        db_session, new_id, sender=new_sender, now=NOW
    )
    assert report.outcome == "sent"
    assert await _count(db_session, actor, status="sent") == 1
    assert await _count(db_session, actor, status="delivery_unknown") == 1

    page = await client.get(
        NOTIFICATIONS, params={"status": "delivery_unknown"}, cookies=actor.cookies
    )
    assert [item["id"] for item in page.json()["items"]] == [str(original_id)]

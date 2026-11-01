"""J4.5: settle one claimed notification after its provider attempt.

The M8 pipeline is split so each ticket owns one hard problem:

- A4.1 owns the occurrence state machine (`pending → sending → sent|failed|
  delivery_unknown`, terminal states) and the persistent dedupe identity;
- J4.2 elects the scheduler and claims due occurrences under a bounded delivery lease;
- J4.1 owns the Gmail adapter and J4.5 owns what happens when its response is ambiguous.

This module is the boundary the Gmail adapter calls. It never imports a provider and never
retries: the sender returns the provider message id on success, raises
:class:`~app.services.notifications.NotificationDeliveryRejected` when it can prove that no
message was accepted, or raises anything else (a timeout, a reset connection, an unexpected
adapter error) when the message may already have left. Only the proven rejection becomes
`failed`; every other failure becomes terminal `delivery_unknown`. Gmail offers no
exactly-once delivery, so an automatic retry could send the email twice.

The caller owns the transaction. The occurrence row is locked for the whole settlement so a
duplicate worker cannot send the same claim concurrently, and a retried settlement of a
terminal row is reported as `skipped` without calling the sender. If the process dies while
holding the lock, PostgreSQL releases it and the scheduler's delivery lease expires the row
as uncertain instead of retrying it. The caller must not retry the function after a database
or commit failure: the sender may already have been called, so the row must be left to the
delivery lease, which closes it as `delivery_unknown` and never as `pending`.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Notification
from app.services.notifications import (
    DELIVERY_FAILED,
    DELIVERY_UNKNOWN,
    LEASE_EXPIRED,
    RESPONSE_LOST,
    delivery_error_outcome,
    expire_lease,
    lease_expired,
    mark_delivery_unknown,
    mark_failed,
    mark_sent,
)

DeliveryOutcome = Literal["sent", "failed", "delivery_unknown", "skipped"]


@dataclass(frozen=True)
class DeliveryRequest:
    """Immutable, non-sensitive view of one occurrence handed to the adapter.

    The adapter never receives the ORM row: it stays bound to the locked transaction and
    could lazily load or mutate state while the delivery lock is held. The request carries
    only identity and scheduling fields; the Gmail adapter (J4.1) loads the recipient and
    renders the body in its own scope, so no address or content crosses this boundary.
    """

    notification_id: uuid.UUID
    user_id: uuid.UUID
    type: str
    occurrence: date | None
    due_version: int | None
    scheduled_at: datetime
    attempts: int


class NotificationSender(Protocol):
    """The provider adapter J4.1 implements (Gmail in the MVP).

    ``send`` returns the provider message id only after the provider confirmed the message.
    An adapter that loses the response must raise
    :class:`~app.services.notifications.NotificationDeliveryAmbiguous` (or any exception):
    the settlement treats it as uncertain and never retries it. The adapter must bound its
    own HTTP call well below the J4.2 delivery lease: `settle_delivery` holds the occurrence
    row lock while the call runs, and a hung call would hold that connection and lock.
    """

    async def send(self, request: DeliveryRequest) -> str: ...


@dataclass(frozen=True)
class DeliveryReport:
    """Outcome of one settlement; counts and status only, never a provider payload."""

    notification_id: uuid.UUID
    outcome: DeliveryOutcome
    # `None` when the occurrence does not exist (or belongs to another tenant under RLS).
    status: str | None
    error_code: str | None = None
    provider_message_id: str | None = None


async def settle_delivery(
    db: AsyncSession,
    notification_id: uuid.UUID,
    *,
    sender: NotificationSender,
    now: datetime | None = None,
) -> DeliveryReport:
    """Settle one claimed occurrence exactly once; never retry an uncertain delivery.

    Only a `sending` row is sent. Any other state is skipped, which makes an at-least-once
    job safe: the second delivery of the same job finds the terminal outcome and does not
    call the provider again. An expired lease is closed as `delivery_unknown` before the
    sender is invoked, because the previous attempt may already have reached Gmail.
    """
    instant = now or datetime.now(UTC)
    if instant.tzinfo is None:
        raise ValueError("the settlement instant must be timezone-aware")

    row = await db.get(Notification, notification_id, with_for_update=True)
    if row is None:
        return DeliveryReport(
            notification_id=notification_id, outcome="skipped", status=None
        )
    if row.status != "sending":
        return DeliveryReport(
            notification_id=notification_id, outcome="skipped", status=row.status
        )
    if lease_expired(row, now=instant):
        expire_lease(row, now=instant)
        await db.flush()
        return DeliveryReport(
            notification_id=notification_id,
            outcome=DELIVERY_UNKNOWN,
            status=row.status,
            error_code=LEASE_EXPIRED,
        )

    request = DeliveryRequest(
        notification_id=row.id,
        user_id=row.user_id,
        type=row.type,
        occurrence=row.occurrence,
        due_version=row.due_version,
        scheduled_at=row.scheduled_at,
        attempts=row.attempts,
    )
    try:
        provider_message_id = await sender.send(request)
    except Exception as error:  # noqa: BLE001 - any sender failure is conservatively uncertain
        outcome, error_code = delivery_error_outcome(error)
        if outcome == DELIVERY_FAILED:
            mark_failed(row, error_code=error_code)
        else:
            mark_delivery_unknown(row, error_code=error_code)
        await db.flush()
        return DeliveryReport(
            notification_id=notification_id,
            outcome=outcome,
            status=row.status,
            error_code=error_code,
        )

    if not isinstance(provider_message_id, str) or not provider_message_id.strip():
        # A success without a provider identity cannot prove a delivery; fail closed.
        mark_delivery_unknown(row, error_code=RESPONSE_LOST)
        await db.flush()
        return DeliveryReport(
            notification_id=notification_id,
            outcome=DELIVERY_UNKNOWN,
            status=row.status,
            error_code=RESPONSE_LOST,
        )

    mark_sent(row, now=instant, provider_message_id=provider_message_id)
    await db.flush()
    return DeliveryReport(
        notification_id=notification_id,
        outcome="sent",
        status=row.status,
        provider_message_id=provider_message_id,
    )

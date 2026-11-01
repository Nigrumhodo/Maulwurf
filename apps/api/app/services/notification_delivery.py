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
as uncertain instead of retrying it.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
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


class NotificationSender(Protocol):
    """The provider adapter J4.1 implements (Gmail in the MVP).

    ``send`` returns the provider message id only after the provider confirmed the message.
    An adapter that loses the response must raise
    :class:`~app.services.notifications.NotificationDeliveryAmbiguous` (or any exception):
    the settlement treats it as uncertain and never retries it.
    """

    async def send(self, notification: Notification) -> str: ...


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

    try:
        provider_message_id = await sender.send(row)
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

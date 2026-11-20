"""ARQ `deliver_notification(notification_id)` worker job (J4.1, M8).

Delivers one claimed ``notifications`` row once via ``GmailNotificationSender`` inside
the J4.5 ``settle_delivery`` boundary. ``max_tries=1`` is strict: an uncertain or lost
provider response must NEVER be retried automatically (terminal ``delivery_unknown``).
"""

import logging
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import timedelta
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import bind_cross_tenant, reset_cross_tenant, session_scope
from app.services.gmail import GmailNotificationSender
from app.services.notification_delivery import NotificationSender, settle_delivery

logger = logging.getLogger(__name__)

# Strict single attempt: Gmail offers no exactly-once guarantee.
MAX_TRIES: Final = 1
JOB_TIMEOUT_S: Final = 60
SENDER_TIMEOUT: Final = timedelta(seconds=30)

# Optional dependency-injection keys for unit tests.
SESSIONS_KEY: Final = "notification_session_factory"
SENDER_KEY: Final = "notification_sender"

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


async def deliver_notification(ctx: dict[str, Any], notification_id: str) -> None:
    """Settle one claimed notification once without retries or PII logging."""
    try:
        parsed_id = uuid.UUID(notification_id)
    except ValueError:
        logger.warning("deliver_notification.invalid_id")
        return

    if not settings.notification_delivery_enabled:
        return

    sessions: SessionFactory = ctx.get(SESSIONS_KEY) or session_scope
    sender: NotificationSender = ctx.get(SENDER_KEY) or GmailNotificationSender(
        session_factory=sessions
    )

    token = bind_cross_tenant()
    try:
        try:
            async with sessions() as db:
                report = await settle_delivery(
                    db,
                    parsed_id,
                    sender=sender,
                    sender_timeout=SENDER_TIMEOUT,
                )
        except Exception as exc:  # noqa: BLE001 - never retry or log PII/DSN text
            logger.warning("deliver_notification.failed error=%s", type(exc).__name__)
            return
    finally:
        reset_cross_tenant(token)

    logger.info(
        "deliver_notification outcome=%s status=%s error_code=%s",
        report.outcome,
        report.status,
        report.error_code,
    )

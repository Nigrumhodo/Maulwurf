"""Gmail delivery adapter for claimed M8 notifications (J4.1).

Implements ``NotificationSender`` on the J4.5 settlement boundary:

- Runs inside its own database scope (``session_factory``) because ``fresh_access_token``
  commits internally and ``settle_delivery`` already released its row lock before invoking
  ``sender.send``.
- Enforces the scheduler fence (``claimed_by_fence`` vs live ``scheduler_leases`` row),
  ``notification_delivery_enabled``, ``user.email_verified``, ``settings.gmail_opt_in``,
  ``gmail.send`` scope, and confirmed task / ``due_version`` currency BEFORE making any
  HTTP request.
- Never retries automatically: HTTP 2xx with a non-empty message id succeeds once; 4xx and
  local pre-flight rejections raise ``NotificationDeliveryRejected`` (``failed``); 5xx,
  timeouts and transport failures raise ``NotificationDeliveryAmbiguous``
  (``delivery_unknown``).
- Never logs email addresses, subjects or provider bodies.
"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import date, datetime
from typing import Any, Final

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import bind_user, reset_user, session_scope
from app.models import (
    CalendarSettings,
    GoogleCredential,
    Notification,
    SchedulerLease,
    Task,
    User,
)
from app.models.notification import DIGEST_TYPES, REMINDER_TYPES
from app.services.gmail_templates import DigestItemView, render_notification_email
from app.services.google_tokens import (
    GoogleTokenRejected,
    GoogleTokenTransient,
    GoogleTokenUnavailable,
    fresh_access_token,
)
from app.services.notification_delivery import DeliveryRequest
from app.services.notifications import (
    RESPONSE_LOST,
    NotificationDeliveryAmbiguous,
    NotificationDeliveryRejected,
)
from app.services.oauth_google import gmail_send_granted
from app.services.scheduler_lease import SCHEDULER_ROLE

GMAIL_API_BASE: Final = "https://gmail.googleapis.com/gmail/v1"
DEFAULT_SENDER_TIMEOUT_S: Final = 15.0
DIGEST_TASK_LIMIT: Final = 20

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


def _digest_due_label(due_at: datetime | None, due_date: date | None) -> str:
    if due_at is not None:
        return due_at.strftime("%Y-%m-%d %H:%M %Z").strip()
    if due_date is not None:
        return due_date.isoformat()
    return "sin fecha"


class GmailNotificationSender:
    """One-shot Gmail API sender implementing ``NotificationSender``."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory = session_scope,
        transport: httpx.AsyncBaseTransport | None = None,
        token_transport: httpx.AsyncBaseTransport | None = None,
        base_url: str = GMAIL_API_BASE,
        timeout_seconds: float = DEFAULT_SENDER_TIMEOUT_S,
    ) -> None:
        self._session_factory = session_factory
        self._transport = transport
        self._token_transport = token_transport
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    async def send(self, request: DeliveryRequest) -> str:
        """Render and send one occurrence once via Gmail API."""
        if not settings.notification_delivery_enabled:
            raise NotificationDeliveryRejected("provider_rejected")

        user_token = bind_user(request.user_id)
        try:
            async with self._session_factory() as db:
                notification = await db.get(Notification, request.notification_id)
                if (
                    notification is None
                    or notification.status != "sending"
                    or notification.user_id != request.user_id
                ):
                    raise NotificationDeliveryRejected("provider_rejected")

                # A missing fence is not a match. Fail closed so a row claimed without
                # an election token cannot be delivered (F4-05).
                lease = await db.get(SchedulerLease, SCHEDULER_ROLE)
                if (
                    notification.claimed_by_fence is None
                    or lease is None
                    or lease.fencing_token != notification.claimed_by_fence
                ):
                    raise NotificationDeliveryRejected("provider_rejected")

                user = await db.get(User, request.user_id)
                if user is None or user.status != "active":
                    raise NotificationDeliveryRejected("provider_rejected")
                if not user.email_verified or not user.email:
                    raise NotificationDeliveryRejected("recipient_unverified")

                pref = await db.get(CalendarSettings, request.user_id)
                if pref is None or not pref.gmail_opt_in:
                    raise NotificationDeliveryRejected("gmail_not_opted_in")

                credential = await db.get(GoogleCredential, request.user_id)
                if (
                    credential is None
                    or credential.status != "connected"
                    or not gmail_send_granted(credential.scopes or ())
                ):
                    raise NotificationDeliveryRejected("provider_rejected")

                task_title: str | None = None
                task_tipo: str | None = None
                due_at = None
                due_date = None
                digest_items: tuple[DigestItemView, ...] = ()

                if request.type in REMINDER_TYPES:
                    if notification.task_id is None:
                        raise NotificationDeliveryRejected("provider_rejected")
                    task = await db.get(Task, notification.task_id)
                    if (
                        task is None
                        or task.user_id != request.user_id
                        or task.status != "confirmed"
                    ):
                        raise NotificationDeliveryRejected("provider_rejected")
                    if request.due_version is not None and task.due_version != request.due_version:
                        raise NotificationDeliveryRejected("provider_rejected")
                    task_title = task.titulo
                    task_tipo = task.tipo
                    due_at = task.due_at
                    due_date = task.due_date
                elif request.type in DIGEST_TYPES:
                    rows = (
                        await db.scalars(
                            select(Task)
                            .where(Task.user_id == request.user_id, Task.status == "confirmed")
                            .order_by(
                                Task.due_date.asc().nulls_last(),
                                Task.due_at.asc().nulls_last(),
                                Task.created_at.asc(),
                            )
                            .limit(DIGEST_TASK_LIMIT)
                        )
                    ).all()
                    digest_items = tuple(
                        DigestItemView(
                            titulo=row.titulo,
                            tipo=row.tipo,
                            due_label=_digest_due_label(row.due_at, row.due_date),
                        )
                        for row in rows
                    )

                email = render_notification_email(
                    recipient=user.email,
                    notification_type=request.type,
                    occurrence=request.occurrence,
                    task_title=task_title,
                    task_tipo=task_tipo,
                    due_at=due_at,
                    due_date=due_date,
                    digest_items=digest_items,
                )

                try:
                    access_token = await fresh_access_token(
                        db, credential, transport=self._token_transport
                    )
                except GoogleTokenRejected as exc:
                    raise NotificationDeliveryRejected("invalid_grant") from exc
                except GoogleTokenUnavailable as exc:
                    raise NotificationDeliveryRejected("provider_rejected") from exc
                except GoogleTokenTransient as exc:
                    raise NotificationDeliveryAmbiguous("provider_unavailable") from exc
        finally:
            reset_user(user_token)

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds, transport=self._transport
            ) as client:
                response = await client.post(
                    f"{self._base_url}/users/me/messages/send",
                    headers={"Authorization": f"Bearer {access_token}"},
                    json={"raw": email.raw_b64url},
                )
        except httpx.HTTPError as exc:
            raise NotificationDeliveryAmbiguous("provider_unavailable") from exc

        if 200 <= response.status_code < 300:
            try:
                body: Any = response.json()
            except ValueError:
                body = None
            msg_id = body.get("id") if isinstance(body, dict) else None
            if isinstance(msg_id, str) and msg_id.strip():
                return msg_id.strip()
            raise NotificationDeliveryAmbiguous(RESPONSE_LOST)

        if response.status_code == 429:
            raise NotificationDeliveryRejected("rate_limited")
        if response.status_code == 403 and _is_gmail_api_disabled(response):
            raise NotificationDeliveryRejected("gmail_api_disabled")
        if response.status_code in {400, 401, 403, 404}:
            raise NotificationDeliveryRejected("provider_rejected")
        raise NotificationDeliveryAmbiguous("provider_unavailable")


def _is_gmail_api_disabled(response: httpx.Response) -> bool:
    try:
        payload = response.json()
    except ValueError:
        return False
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return False
    for entry in error.get("errors") or ():
        if isinstance(entry, dict) and entry.get("reason") == "accessNotConfigured":
            return True
    for detail in error.get("details") or ():
        if isinstance(detail, dict) and detail.get("reason") == "SERVICE_DISABLED":
            return True
    return False

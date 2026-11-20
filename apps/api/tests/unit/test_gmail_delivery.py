"""U-S4-JF-03 (J4.1): opt-in Gmail delivery, MIME templates, fence/opt-in guards, and ARQ job."""

import base64
import logging
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.config import settings
from app.models import (
    CalendarSettings,
    GoogleCredential,
    Notification,
    SchedulerLease,
    Task,
    User,
)
from app.services import gmail as gmail_service
from app.services.gmail import GmailNotificationSender
from app.services.gmail_templates import DigestItemView, render_notification_email
from app.services.google_tokens import GoogleTokenRejected
from app.services.notification_delivery import DeliveryRequest, settle_delivery
from app.services.notifications import NotificationDeliveryAmbiguous, NotificationDeliveryRejected
from app.services.oauth_google import GMAIL_SEND_SCOPE
from app.services.scheduler_lease import SCHEDULER_ROLE
from app.workers.jobs.deliver_notification import MAX_TRIES, deliver_notification


def _decode_raw(raw_b64url: str) -> str:
    padded = raw_b64url + "=" * (-len(raw_b64url) % 4)
    return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")


# --- 1. Templates & HTML escaping ------------------------------------------------------


def test_reminder_template_escapes_html_and_builds_valid_mime() -> None:
    email = render_notification_email(
        recipient="ana@example.test",
        notification_type="reminder_t24",
        task_title="Taller <script>alert(1)</script> & cálculo",
        task_tipo="entrega",
        due_date=date(2026, 11, 22),
    )

    assert "<script>" not in email.html_body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in email.html_body
    assert "Taller" in email.subject
    decoded = _decode_raw(email.raw_b64url)
    assert "To: ana@example.test" in decoded
    assert "multipart/alternative" in decoded


def test_digest_and_test_templates_escape_items_and_reject_crlf() -> None:
    digest = render_notification_email(
        recipient="ana@example.test",
        notification_type="digest_weekly",
        occurrence=date(2026, 11, 22),
        digest_items=(
            DigestItemView(
                titulo="Examen <b>Final</b>",
                tipo="examen",
                due_label="2026-11-24",
            ),
        ),
    )
    assert "&lt;b&gt;Final&lt;/b&gt;" in digest.html_body
    assert "Resumen semanal" in digest.subject

    test_mail = render_notification_email(
        recipient="ana@example.test",
        notification_type="test",
    )
    assert "gmail.send" in test_mail.html_body

    with pytest.raises(NotificationDeliveryRejected) as exc:
        render_notification_email(
            recipient="ana@example.test\r\nBcc: evil@attacker.test",
            notification_type="test",
        )
    assert exc.value.error_code == "template_invalid"


# --- 2. Fake session + Gmail sender + settle_delivery boundary ------------------------


class _FakeScalars:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def all(self) -> list[Any]:
        return list(self._items)


class _FakeSession:
    def __init__(
        self,
        objects: dict[tuple[type[Any], Any], Any],
        *,
        digest_tasks: list[Task] | None = None,
    ) -> None:
        self._objects = objects
        self._digest_tasks = digest_tasks or []
        self.commits = 0

    async def get(self, model: type[Any], key: Any, **_: Any) -> Any:
        return self._objects.get((model, key))

    async def scalars(self, _: Any) -> _FakeScalars:
        return _FakeScalars(self._digest_tasks)

    async def commit(self) -> None:
        self.commits += 1

    async def flush(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


def _seed(
    *,
    status: str = "sending",
    type_: str = "reminder_t24",
    opt_in: bool = True,
    verified: bool = True,
    scopes: list[str] | None = None,
    task_status: str = "confirmed",
    task_due_version: int = 1,
    claimed_fence: int | None = 3,
    live_fence: int = 3,
) -> tuple[dict[tuple[type[Any], Any], Any], Notification, Task, User, _FakeSession]:
    user_id = uuid.uuid4()
    task_id = uuid.uuid4()
    notification_id = uuid.uuid4()
    now = datetime.now(UTC)

    user = User(
        id=user_id,
        google_sub="sub-1",
        email="ana@example.test",
        email_verified=verified,
        display_name="Ana",
        status="active",
    )
    pref = CalendarSettings(user_id=user_id, gmail_opt_in=opt_in)
    enc_access, ver = crypto.encrypt(
        "ya29.fake-access-token", user_id=user_id, field="google_credentials.access_token"
    )
    credential = GoogleCredential(
        user_id=user_id,
        access_token_enc=enc_access,
        refresh_token_enc=None,
        key_version=ver,
        scopes=[GMAIL_SEND_SCOPE] if scopes is None else scopes,
        status="connected",
        token_expires_at=now + timedelta(hours=1),
    )
    task = Task(
        id=task_id,
        user_id=user_id,
        tipo="entrega",
        titulo="Parcial 2",
        due_date=date(2026, 11, 25),
        timezone="UTC",
        all_day=True,
        date_status="resolved",
        status=task_status,
        due_version=task_due_version,
    )
    notification = Notification(
        id=notification_id,
        user_id=user_id,
        type=type_,
        task_id=task_id if type_.startswith("reminder_") else None,
        due_version=1 if type_.startswith("reminder_") else None,
        occurrence=date(2026, 11, 24),
        dedupe_key=f"k:{notification_id}",
        scheduled_at=now - timedelta(seconds=10),
        status=status,
        lease_expires_at=now + timedelta(minutes=4),
        attempts=1,
        claimed_by_fence=claimed_fence,
    )
    lease = SchedulerLease(
        name=SCHEDULER_ROLE,
        holder="sched-1",
        fencing_token=live_fence,
        lease_expires_at=now + timedelta(minutes=2),
        heartbeat_at=now,
    )
    objects: dict[tuple[type[Any], Any], Any] = {
        (Notification, notification_id): notification,
        (User, user_id): user,
        (CalendarSettings, user_id): pref,
        (GoogleCredential, user_id): credential,
        (Task, task_id): task,
        (SchedulerLease, SCHEDULER_ROLE): lease,
    }
    session = _FakeSession(objects, digest_tasks=[task])
    return objects, notification, task, user, session


def _request(notification: Notification) -> DeliveryRequest:
    return DeliveryRequest(
        notification_id=notification.id,
        user_id=notification.user_id,
        type=notification.type,
        occurrence=notification.occurrence,
        due_version=notification.due_version,
        scheduled_at=notification.scheduled_at,
        attempts=notification.attempts,
    )


def _scope(session: _FakeSession) -> Callable[[], AbstractAsyncContextManager[AsyncSession]]:
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield cast(AsyncSession, session)

    return scope


async def test_gmail_sender_succeeds_with_single_post_and_no_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "notification_delivery_enabled", True)
    _, notification, _, _, session = _seed()
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        assert request.headers["authorization"] == "Bearer ya29.fake-access-token"
        return httpx.Response(200, json={"id": "gmail-msg-123"})

    sender = GmailNotificationSender(
        session_factory=_scope(session),
        transport=httpx.MockTransport(handler),
    )

    msg_id = await sender.send(_request(notification))
    assert msg_id == "gmail-msg-123"
    assert calls == [("POST", "https://gmail.googleapis.com/gmail/v1/users/me/messages/send")]


@pytest.mark.parametrize(
    ("status_code", "expected_code"),
    [
        (400, "provider_rejected"),
        (403, "provider_rejected"),
        (404, "provider_rejected"),
        (429, "rate_limited"),
    ],
)
async def test_gmail_sender_maps_explicit_4xx_rejections_without_retry(
    monkeypatch: pytest.MonkeyPatch, status_code: int, expected_code: str
) -> None:
    monkeypatch.setattr(settings, "notification_delivery_enabled", True)
    _, notification, _, _, session = _seed()
    call_count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(status_code, json={"error": {"message": "rejected"}})

    sender = GmailNotificationSender(
        session_factory=_scope(session),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(NotificationDeliveryRejected) as exc:
        await sender.send(_request(notification))

    assert exc.value.error_code == expected_code
    assert call_count == 1


@pytest.mark.parametrize("mode", ["http_500", "http_503", "timeout", "missing_id"])
async def test_gmail_sender_maps_5xx_timeouts_and_lost_id_to_ambiguous(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    monkeypatch.setattr(settings, "notification_delivery_enabled", True)
    _, notification, _, _, session = _seed()
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if mode == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        if mode == "missing_id":
            return httpx.Response(200, json={})
        status_code = 500 if mode == "http_500" else 503
        return httpx.Response(status_code, json={"error": "transient"})

    sender = GmailNotificationSender(
        session_factory=_scope(session),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(NotificationDeliveryAmbiguous):
        await sender.send(_request(notification))

    assert call_count == 1


@pytest.mark.parametrize(
    ("overrides", "expected_code"),
    [
        ({"opt_in": False}, "gmail_not_opted_in"),
        ({"verified": False}, "recipient_unverified"),
        ({"scopes": ["openid", "email"]}, "provider_rejected"),
        ({"claimed_fence": 2, "live_fence": 3}, "provider_rejected"),
        ({"task_status": "needs_review"}, "provider_rejected"),
        ({"task_due_version": 2}, "provider_rejected"),
    ],
)
async def test_gmail_sender_blocks_delivery_before_http_when_guards_fail(
    monkeypatch: pytest.MonkeyPatch, overrides: dict[str, Any], expected_code: str
) -> None:
    monkeypatch.setattr(settings, "notification_delivery_enabled", True)
    _, notification, _, _, session = _seed(**overrides)
    http_called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal http_called
        http_called = True
        return httpx.Response(200, json={"id": "should-never-happen"})

    sender = GmailNotificationSender(
        session_factory=_scope(session),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(NotificationDeliveryRejected) as exc:
        await sender.send(_request(notification))

    assert exc.value.error_code == expected_code
    assert http_called is False


async def test_gmail_sender_handles_invalid_grant_on_token_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "notification_delivery_enabled", True)
    _, notification, _, _, session = _seed()

    async def fail_refresh(*_args: Any, **_kwargs: Any) -> str:
        raise GoogleTokenRejected("invalid_grant")

    monkeypatch.setattr(gmail_service, "fresh_access_token", fail_refresh)

    sender = GmailNotificationSender(session_factory=_scope(session))
    with pytest.raises(NotificationDeliveryRejected) as exc:
        await sender.send(_request(notification))

    assert exc.value.error_code == "invalid_grant"


async def test_deliver_notification_worker_settles_without_logging_pii(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(settings, "notification_delivery_enabled", True)
    monkeypatch.setattr(settings, "notification_scheduler_enabled", True)
    _, notification, _, _, session = _seed(type_="digest_daily")

    scope = _scope(session)
    sender = GmailNotificationSender(
        session_factory=scope,
        transport=httpx.MockTransport(lambda _req: httpx.Response(503)),
    )

    with caplog.at_level(logging.INFO):
        await deliver_notification(
            {
                "notification_session_factory": scope,
                "notification_sender": sender,
            },
            str(notification.id),
        )

    assert MAX_TRIES == 1
    assert notification.status == "delivery_unknown"
    assert "ana@example.test" not in caplog.text
    assert "delivery_unknown" in caplog.text


async def test_settle_delivery_records_sent_from_gmail_sender(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "notification_delivery_enabled", True)
    _, notification, _, _, session = _seed(type_="test")

    sender = GmailNotificationSender(
        session_factory=_scope(session),
        transport=httpx.MockTransport(lambda _req: httpx.Response(200, json={"id": "gm-99"})),
    )

    report = await settle_delivery(
        cast(AsyncSession, session),
        notification.id,
        sender=sender,
        sender_timeout=timedelta(seconds=10),
    )

    assert report.outcome == "sent"
    assert report.status == "sent"
    assert report.provider_message_id == "gm-99"
    assert notification.status == "sent"
    assert notification.provider_message_id == "gm-99"

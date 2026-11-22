"""Thermos G5 regressions: digest period, task-zone due labels, stable reminder dedupe."""

import json
import uuid
from datetime import UTC, date, datetime
from typing import Any

import httpx
import pytest

from app.core.config import settings
from app.services import gmail as gmail_service
from app.services.conflicts import ConflictReport
from app.services.digests import DigestContent, DigestTaskItem
from app.services.gmail import GmailNotificationSender
from app.services.gmail_templates import format_due, render_notification_email
from app.services.reminders import ReminderTask, plan_task_reminders

from .test_gmail_delivery import _decode_raw, _request, _scope, _seed


def test_format_due_uses_task_timezone_and_falls_back() -> None:
    due = datetime(2026, 11, 25, 2, 30, tzinfo=UTC)
    assert format_due(due, None, "America/Bogota") == "2026-11-24 21:30 -05"
    assert format_due(due, None, "Not/AZone") == "2026-11-25 02:30 UTC"
    assert format_due(None, date(2026, 1, 2)) == "2026-01-02"


def test_reminder_email_renders_due_in_task_timezone() -> None:
    email = render_notification_email(
        recipient="ana@example.test",
        notification_type="reminder_t24",
        task_title="Parcial",
        task_tipo="entrega",
        due_at=datetime(2026, 11, 25, 2, 30, tzinfo=UTC),
        task_timezone="America/Bogota",
    )
    assert "2026-11-24 21:30" in email.text_body


async def test_digest_uses_period_content(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "notification_delivery_enabled", True)
    monkeypatch.setattr(settings, "notification_scheduler_enabled", True)
    _, notification, _, user, session = _seed(type_="digest_daily")
    user.timezone = "America/Bogota"
    seen: dict[str, Any] = {}

    async def fake_build(_db: Any, **kwargs: Any) -> DigestContent:
        seen.update(kwargs)
        return DigestContent(
            type=kwargs["type_"],
            local_date=kwargs["local_date"],
            window_start=date(2026, 11, 24),
            window_end=date(2026, 11, 25),
            upcoming_tasks=(
                DigestTaskItem(
                    task_id=uuid.uuid4(),
                    titulo="Informe",
                    tipo="entrega",
                    status="confirmed",
                    all_day=False,
                    local_day=date(2026, 11, 24),
                    due_date=None,
                    due_at=datetime(2026, 11, 25, 2, 30, tzinfo=UTC),
                ),
            ),
            conflicts=ConflictReport(time_conflicts=(), deadline_clashes=()),
        )

    monkeypatch.setattr(gmail_service, "build_digest_content", fake_build)
    bodies: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        bodies.append(_decode_raw(json.loads(req.content)["raw"]))
        return httpx.Response(200, json={"id": "m1"})

    sender = GmailNotificationSender(
        session_factory=_scope(session), transport=httpx.MockTransport(handler)
    )
    await sender.send(_request(notification))

    assert seen["user_timezone"] == "America/Bogota"
    assert seen["local_date"] == notification.occurrence
    assert "Periodo: 2026-11-24 a 2026-11-25" in bodies[0]
    assert "Informe" in bodies[0] and "2026-11-24 21:30" in bodies[0]


def test_timed_occurrence_is_stable_across_user_timezone_changes() -> None:
    due = datetime(2026, 12, 1, 3, 0, tzinfo=UTC)
    now = datetime(2026, 11, 1, tzinfo=UTC)

    def plan(user_tz: str) -> set[str]:
        task = ReminderTask(
            id=uuid.UUID(int=1),
            user_id=uuid.UUID(int=2),
            status="confirmed",
            all_day=False,
            date_status="resolved",
            due_date=None,
            due_at=due,
            due_version=1,
            timezone="America/Bogota",
        )
        return {
            o.dedupe_key
            for o in plan_task_reminders(task, user_timezone=user_tz, now=now)
        }

    assert plan("America/Bogota") == plan("Asia/Tokyo")

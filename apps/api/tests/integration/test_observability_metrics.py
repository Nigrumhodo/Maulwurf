"""J2.5 against real PostgreSQL: outbox lag on `/internal/metrics` and the Caddy 404 rule.

Lag counts only events the dispatcher could take. An event waiting for verified cleanup
is not lag. The Caddy rule is checked as text in `infra/Caddyfile`; Caddy is not started.
"""

import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.routers.internal_metrics as internal_metrics
from app.core.prometheus import Sample
from app.main import app
from app.models import Audio, IngestionAttempt, OutboxEvent, Subject, User
from app.services.outbox_metrics import collect_outbox_samples

pytestmark = pytest.mark.integration

CADDYFILE = Path(__file__).resolve().parents[4] / "infra" / "Caddyfile"


async def _user_audio(db: AsyncSession, cleanup_status: str) -> Audio:
    now = datetime.now(UTC)
    user = User(google_sub=f"sub-{uuid.uuid4()}", email="m@example.test")
    db.add(user)
    await db.flush()
    subject = Subject(user_id=user.id, name="Química")
    db.add(subject)
    await db.flush()
    audio = Audio(
        user_id=user.id, subject_id=subject.id, class_date=date(2026, 9, 21),
        class_timezone="America/Bogota", language_code="es",
    )
    db.add(audio)
    await db.flush()
    db.add(
        IngestionAttempt(
            user_id=user.id, audio_id=audio.id, privacy_notice_version="v1",
            cloud_processing_accepted_at=now, third_party_voice_acknowledged_at=now,
            declared_providers={}, status="succeeded", owner_instance="ingest-test",
            fencing_token=1, cleanup_status=cleanup_status,
        )
    )
    await db.flush()
    return audio


def _gauge(samples: list[Sample], name: str) -> float:
    (sample,) = [s for s in samples if s.name == name and not s.labels]
    return float(sample.value)


async def test_lag_counts_only_events_the_dispatcher_can_take(db_session: AsyncSession) -> None:
    now = datetime.now(UTC)
    verified = await _user_audio(db_session, "verified")
    pending = await _user_audio(db_session, "pending")
    # Same gated type: only the event whose cleanup is verified can advance.
    db_session.add(
        OutboxEvent(
            user_id=verified.user_id, type="analyze_requested", resource_type="audio",
            resource_id=verified.id, resource_version=1, enabled=True,
            created_at=now - timedelta(seconds=120),
        )
    )
    db_session.add(
        OutboxEvent(
            user_id=pending.user_id, type="analyze_requested", resource_type="audio",
            resource_id=pending.id, resource_version=1, enabled=True,
            created_at=now - timedelta(seconds=900),
        )
    )
    await db_session.flush()

    samples = await collect_outbox_samples(db_session, now=now)

    assert 119.0 <= _gauge(samples, "maulwurf_outbox_lag_seconds") <= 121.0
    pending_by_status = {
        s.labels["status"]: s.value for s in samples if s.name == "maulwurf_outbox_events"
    }
    assert pending_by_status["pending"] == 2


async def test_internal_metrics_endpoint_serves_prometheus_text(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    @asynccontextmanager
    async def _over_test_transaction() -> AsyncIterator[AsyncSession]:
        yield db_session

    monkeypatch.setattr(internal_metrics, "session_scope", _over_test_transaction)
    # ASGITransport shares the test's event loop with the DB session; TestClient does not.
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/internal/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain; version=0.0.4")
    assert "# TYPE maulwurf_outbox_lag_seconds gauge" in response.text
    assert "maulwurf_outbox_retrying_events " in response.text


def test_caddy_rule_answers_404_on_internal_paths_and_never_proxies_them() -> None:
    text = CADDYFILE.read_text()

    assert "@internal path /internal/*" in text
    rule = text.split("@internal path /internal/*", 1)[1].split("\n\t}", 1)[0]
    assert "respond 404" in rule
    # No public route (`@api`, upload or progress) may reach `/internal/...`.
    assert not re.search(r"@(api|upload|progress)[^\n]*/internal", text)

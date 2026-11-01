"""J4.8 against real PostgreSQL: budget usage is read cross-tenant on `/internal/metrics`.

The fixture runs as the migration owner (superuser), so the seeded rows are visible.
``collect_usage(cross_tenant=True)`` switches to the jobs role exactly like the outbox
aggregate; the state transitions and label shape are unit-tested in
``tests/unit/test_provider_budget.py``.
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from functools import partial

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import app.routers.internal_metrics as internal_metrics
from app.core.config import Settings
from app.main import app
from app.models import (
    Audio,
    Conversation,
    GoogleRemoteOperation,
    Message,
    Notification,
    OutboxEvent,
    Subject,
    Transcript,
    User,
)
from app.services.provider_budget import collect_budget_samples, collect_usage

pytestmark = pytest.mark.integration

# A month no other test uses: the usage query is deliberately cross-tenant, so a shared
# test database with committed rows from another module must not leak into the assertion.
NOW = datetime(2099, 11, 15, 12, 0, tzinfo=UTC)
IN_MONTH = datetime(2099, 11, 10, 9, 0, tzinfo=UTC)
LAST_MONTH = datetime(2099, 10, 10, 9, 0, tzinfo=UTC)


async def _user(db: AsyncSession) -> User:
    user = User(google_sub=f"sub-{uuid.uuid4()}", email="budget@example.test")
    db.add(user)
    await db.flush()
    return user


async def _audio_with_transcript(
    db: AsyncSession,
    user: User,
    *,
    created_at: datetime,
    duration_seconds: float,
    transcript_created_at: datetime | None = None,
) -> Audio:
    subject = Subject(user_id=user.id, name=f"Materia {uuid.uuid4().hex[:6]}")
    db.add(subject)
    await db.flush()
    audio = Audio(
        user_id=user.id,
        subject_id=subject.id,
        class_date=created_at.date(),
        class_timezone="America/Bogota",
        language_code="es",
        duration_seconds=duration_seconds,
        created_at=created_at,
    )
    db.add(audio)
    await db.flush()
    db.add(
        Transcript(
            user_id=user.id,
            audio_id=audio.id,
            version=1,
            language_requested="es",
            model="test-model",
            model_config={},
            timestamp_precision="none",
            text="texto de clase",
            char_count=14,
            fts_config="spanish",
            created_at=transcript_created_at or created_at,
        )
    )
    await db.flush()
    return audio


async def _conversation(db: AsyncSession, user: User) -> Conversation:
    conversation = Conversation(user_id=user.id, mode="all")
    db.add(conversation)
    await db.flush()
    return conversation


async def _message(
    db: AsyncSession,
    conversation: Conversation,
    *,
    created_at: datetime,
    input_tokens: int,
    output_tokens: int,
) -> Message:
    message = Message(
        user_id=conversation.user_id,
        conversation_id=conversation.id,
        role="assistant",
        content="respuesta",
        status="completed",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        created_at=created_at,
    )
    db.add(message)
    await db.flush()
    return message


async def _seed(db: AsyncSession) -> None:
    user = await _user(db)
    await _audio_with_transcript(db, user, created_at=IN_MONTH, duration_seconds=300.0)
    await _audio_with_transcript(db, user, created_at=LAST_MONTH, duration_seconds=600.0)
    conversation = await _conversation(db, user)
    await _message(db, conversation, created_at=IN_MONTH, input_tokens=700, output_tokens=300)
    await _message(db, conversation, created_at=LAST_MONTH, input_tokens=9_000, output_tokens=1)
    for sent_at, sequence in ((IN_MONTH, 1), (LAST_MONTH, 2)):
        db.add(
            Notification(
                user_id=user.id,
                task_id=None,
                type="test",
                occurrence=sent_at.date(),
                due_version=None,
                scheduled_at=sent_at,
                dedupe_key=f"budget-test-{sequence}-{uuid.uuid4()}",
                occurrence_seq=sequence,
                status="sent",
                sent_at=sent_at,
                provider_message_id=f"provider-{sequence}",
            )
        )
    for created_at, sequence in ((IN_MONTH, 1), (LAST_MONTH, 2)):
        db.add(
            OutboxEvent(
                user_id=user.id,
                type="sync_task_event",
                resource_type="task",
                resource_id=uuid.uuid4(),
                resource_version=1,
                enabled=True,
                dedupe_key=f"budget-sync-{sequence}-{uuid.uuid4()}",
                created_at=created_at,
            )
        )
        db.add(
            GoogleRemoteOperation(
                user_id=user.id,
                kind="delete_event",
                event_id=f"event-{sequence}-{uuid.uuid4()}",
                status="succeeded",
                authorized=True,
                expires_at=NOW + timedelta(days=30),
                created_at=created_at,
            )
        )
    await db.flush()


def _settings(**overrides: float) -> Settings:
    return Settings(
        _env_file=None,
        env="local",
        secret_key="budget-integration-secret-key",
        encryption_key="budget-integration-encryption-key",
        oauth_state_secret="budget-integration-oauth-state-key",
        **overrides,
    )


async def test_usage_counts_only_the_current_month(db_session: AsyncSession) -> None:
    await _seed(db_session)

    usage = await collect_usage(db_session, now=NOW)

    assert usage == {"llm": 1_000.0, "asr": 5.0, "google": 3.0}


async def test_budget_aggregates_have_range_indexes(db_session: AsyncSession) -> None:
    # The endpoint is scraped cross-tenant; every budget query filters a month range, so
    # each table needs an index that a range-only predicate can use. The model metadata and
    # this migration are kept in sync by the `alembic check` drift test.
    result = await db_session.execute(
        text(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE schemaname = 'public' AND indexname = ANY(:names)"
        ),
        {
            "names": [
                "messages_by_created_at",
                "transcripts_by_created_at",
                "notifications_by_status_sent_at",
                "outbox_events_by_type_created_at",
                "google_remote_operations_by_created_at",
            ]
        },
    )
    definitions = {str(name): str(definition) for name, definition in result}

    assert definitions["messages_by_created_at"].endswith("(created_at)")
    assert definitions["transcripts_by_created_at"].endswith("(created_at, audio_id)")
    assert definitions["notifications_by_status_sent_at"].endswith("(status, sent_at)")
    assert definitions["outbox_events_by_type_created_at"].endswith("(type, created_at)")
    assert definitions["google_remote_operations_by_created_at"].endswith("(created_at)")


async def test_a_transcript_committed_this_month_counts_an_audio_uploaded_last_month(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session)
    # Uploaded on the last day of October, committed just after midnight on 1 November:
    # the usage belongs to the November budget, not to either side of a gap.
    await _audio_with_transcript(
        db_session,
        user,
        created_at=datetime(2099, 10, 31, 23, 30, tzinfo=UTC),
        transcript_created_at=datetime(2099, 11, 1, 0, 15, tzinfo=UTC),
        duration_seconds=120.0,
    )
    # The reverse direction: an audio uploaded this month whose only transcript was
    # committed last month is not current-month usage yet.
    await _audio_with_transcript(
        db_session,
        user,
        created_at=IN_MONTH,
        transcript_created_at=LAST_MONTH,
        duration_seconds=300.0,
    )

    usage = await collect_usage(db_session, now=NOW)

    assert usage["asr"] == 2.0


async def test_usage_is_readable_through_the_cross_tenant_jobs_role(
    db_session: AsyncSession,
) -> None:
    await _seed(db_session)

    usage = await collect_usage(db_session, now=NOW, cross_tenant=True)

    assert usage == {"llm": 1_000.0, "asr": 5.0, "google": 3.0}


async def test_configured_caps_turn_measured_usage_into_states(
    db_session: AsyncSession,
) -> None:
    await _seed(db_session)
    settings = _settings(
        budget_llm_tokens=2_000,
        budget_asr_minutes=10.0,
        budget_google_operations=6,
        budget_warning_ratio=0.5,
    )

    samples = await collect_budget_samples(db_session, now=NOW, settings=settings)
    by_name = {
        (sample.name, sample.labels.get("provider", "")): float(sample.value)
        for sample in samples
    }

    assert by_name[("maulwurf_budget_ratio", "llm")] == pytest.approx(0.5)
    assert by_name[("maulwurf_budget_warning", "llm")] == 1.0
    assert by_name[("maulwurf_budget_exhausted", "llm")] == 0.0
    assert by_name[("maulwurf_budget_warning", "asr")] == 1.0
    assert by_name[("maulwurf_budget_warning", "google")] == 1.0


async def test_internal_metrics_endpoint_includes_budget_samples(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)

    @asynccontextmanager
    async def _over_test_transaction() -> AsyncIterator[AsyncSession]:
        yield db_session

    monkeypatch.setattr(internal_metrics, "session_scope", _over_test_transaction)
    # The endpoint uses the real clock; inject the seeded month so the assertion does not
    # depend on when the suite runs.
    monkeypatch.setattr(
        internal_metrics, "collect_budget_samples", partial(collect_budget_samples, now=NOW)
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/internal/metrics")

    assert response.status_code == 200
    assert "# TYPE maulwurf_budget_usage gauge" in response.text
    assert 'maulwurf_budget_usage{provider="llm",unit="tokens"} 1000.0' in response.text
    assert 'maulwurf_budget_configured{provider="llm"} 0.0' in response.text

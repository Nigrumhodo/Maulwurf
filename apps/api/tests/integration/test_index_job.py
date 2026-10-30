"""I-S2-JF-02: idempotent `index(audio_id, transcript_version)` job against PostgreSQL (J2.2).

The job runs through its ARQ entry point, `workers.index_job.index`, with the test's own
transaction injected as the session factory (`db_session` is rolled back after each test). The
embeddings provider is `FakeProvider` (no network); the failing variants raise typed errors.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from arq.worker import Retry
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import (
    Audio,
    Chunk,
    Embedding,
    IndexGeneration,
    IngestionAttempt,
    ProcessingRun,
    Segment,
)
from app.services.embeddings import EmbeddingInputTooLong, EmbeddingRateLimited
from app.services.index_job import index_version_for
from app.services.indexing import INDEX_ACTIVE, INDEX_BUILDING
from app.services.runs import RUN_CONFIG_VERSION
from app.workers.index_job import PROVIDERS_KEY, SESSIONS_KEY, index
from tests.integration.actors import Actor, make_actor, make_class_with_transcript
from tests.unit.test_indexing import FakeProvider

pytestmark = pytest.mark.integration


class ScriptedProvider(FakeProvider):
    """`FakeProvider` that raises `error` on every call, still counting the attempts."""

    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error

    async def embed(self, texts: Any) -> list[list[float]]:
        self.calls.append(list(texts))
        raise self.error


@asynccontextmanager
async def _same_session(db: AsyncSession) -> AsyncIterator[AsyncSession]:
    yield db


def _match_production_session(db: AsyncSession) -> None:
    # Production sessions keep attributes after commit (`expire_on_commit=False`, app/core/db.py).
    db.sync_session.expire_on_commit = False


def _ctx(db: AsyncSession, provider: FakeProvider, job_try: int = 1) -> dict[str, Any]:
    def sessions() -> Any:
        return _same_session(db)

    def providers() -> FakeProvider:
        return provider

    return {SESSIONS_KEY: sessions, PROVIDERS_KEY: providers, "job_try": job_try}


async def _indexable(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> tuple[Actor, Audio, IngestionAttempt]:
    """A verified class with one segment, and D3-Texto closed for the test model."""
    monkeypatch.setattr(settings, "d3_text_closed_models", (settings.embedding_model,))
    _match_production_session(db)
    actor = await make_actor(db)
    audio, attempt, transcript = await make_class_with_transcript(db, actor)
    db.add(
        Segment(
            user_id=actor.user_id,
            transcript_id=transcript.id,
            ordinal=0,
            text=transcript.text,
            char_start=0,
            char_end=transcript.char_count,
        )
    )
    await db.flush()
    return actor, audio, attempt


async def _generations(db: AsyncSession, actor: Actor, status: str | None = None) -> int:
    statement = (
        select(func.count())
        .select_from(IndexGeneration)
        .where(IndexGeneration.user_id == actor.user_id)
    )
    if status is not None:
        statement = statement.where(IndexGeneration.status == status)
    return int((await db.execute(statement)).scalar_one())


async def _run(db: AsyncSession, audio: Audio) -> ProcessingRun | None:
    return (
        await db.execute(
            select(ProcessingRun).where(
                ProcessingRun.audio_id == audio.id, ProcessingRun.stage == "index"
            )
        )
    ).scalar_one_or_none()


def _run_row(actor: Actor, audio: Audio, *, status: str, started_at: datetime) -> ProcessingRun:
    return ProcessingRun(
        user_id=actor.user_id,
        audio_id=audio.id,
        stage="index",
        transcript_version=1,
        config_version=RUN_CONFIG_VERSION,
        status=status,
        started_at=started_at,
    )


async def test_ready_audio_indexes_once(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    actor, audio, _attempt = await _indexable(db_session, monkeypatch)
    provider = FakeProvider()

    await index(_ctx(db_session, provider), str(audio.id), 1)

    version = index_version_for(audio.id, 1)
    generation = (
        await db_session.execute(
            select(IndexGeneration).where(IndexGeneration.user_id == actor.user_id)
        )
    ).scalar_one()
    assert generation.version == version
    assert generation.status == INDEX_ACTIVE
    await db_session.refresh(audio)
    assert audio.active_index_version == version
    run = await _run(db_session, audio)
    assert run is not None
    assert run.status == "succeeded"
    assert len(provider.calls) == 1


async def test_double_delivery_yields_one_index_version(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    actor, audio, _attempt = await _indexable(db_session, monkeypatch)
    provider = FakeProvider()

    await index(_ctx(db_session, provider), str(audio.id), 1)
    await index(_ctx(db_session, provider), str(audio.id), 1)

    assert await _generations(db_session, actor) == 1
    assert await _generations(db_session, actor, INDEX_ACTIVE) == 1
    assert len(provider.calls) == 1  # the second delivery never reaches the provider


async def test_live_lease_blocks_a_concurrent_delivery(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    actor, audio, _attempt = await _indexable(db_session, monkeypatch)
    db_session.add(_run_row(actor, audio, status="running", started_at=datetime.now(UTC)))
    await db_session.flush()
    provider = FakeProvider()

    await index(_ctx(db_session, provider), str(audio.id), 1)

    assert await _generations(db_session, actor) == 0
    assert provider.calls == []


async def test_expired_lease_is_taken_over(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    actor, audio, _attempt = await _indexable(db_session, monkeypatch)
    stale = datetime.now(UTC) - timedelta(hours=1)
    db_session.add(_run_row(actor, audio, status="running", started_at=stale))
    await db_session.flush()

    await index(_ctx(db_session, FakeProvider()), str(audio.id), 1)

    assert await _generations(db_session, actor, INDEX_ACTIVE) == 1


async def test_tombstoned_audio_is_a_noop(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    actor, audio, _attempt = await _indexable(db_session, monkeypatch)
    audio.deleted_at = datetime.now(UTC)
    await db_session.flush()
    provider = FakeProvider()

    await index(_ctx(db_session, provider), str(audio.id), 1)

    assert await _generations(db_session, actor) == 0
    assert await _run(db_session, audio) is None
    assert provider.calls == []


async def test_revoked_owner_is_a_noop(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    actor, audio, _attempt = await _indexable(db_session, monkeypatch)
    actor.user.status = "deleting"
    await db_session.flush()
    provider = FakeProvider()

    await index(_ctx(db_session, provider), str(audio.id), 1)

    assert await _generations(db_session, actor) == 0
    assert provider.calls == []


async def test_stale_transcript_version_is_a_noop(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    actor, audio, _attempt = await _indexable(db_session, monkeypatch)
    audio.active_transcript_version = 2  # a newer transcript superseded the delivered version
    await db_session.flush()
    provider = FakeProvider()

    await index(_ctx(db_session, provider), str(audio.id), 1)

    assert await _generations(db_session, actor) == 0
    assert provider.calls == []


async def test_unverified_cleanup_is_a_noop(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    actor, audio, attempt = await _indexable(db_session, monkeypatch)
    attempt.cleanup_status = "pending"
    await db_session.flush()
    provider = FakeProvider()

    await index(_ctx(db_session, provider), str(audio.id), 1)

    assert await _generations(db_session, actor) == 0
    assert provider.calls == []


async def test_real_text_stays_closed_while_d3_is_open(db_session: AsyncSession) -> None:
    # Default settings: D3-Texto is open, so class text is never indexed (fail closed).
    _match_production_session(db_session)
    actor = await make_actor(db_session)
    audio, _attempt, _transcript = await make_class_with_transcript(db_session, actor)
    provider = FakeProvider()

    await index(_ctx(db_session, provider), str(audio.id), 1)

    assert await _generations(db_session, actor) == 0
    assert await _run(db_session, audio) is None
    assert provider.calls == []


async def test_transient_error_is_retried_at_most_three_times(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    actor, audio, _attempt = await _indexable(db_session, monkeypatch)
    provider = ScriptedProvider(EmbeddingRateLimited("cuota"))

    for job_try in (1, 2, 3):
        with pytest.raises(Retry):
            await index(_ctx(db_session, provider, job_try), str(audio.id), 1)
    await index(_ctx(db_session, provider, 4), str(audio.id), 1)  # budget spent: gives up

    assert len(provider.calls) == 4
    run = await _run(db_session, audio)
    assert run is not None
    assert run.status == "failed"
    assert run.error_code == "retry_exhausted"
    assert run.attempt_count == 4
    assert await _generations(db_session, actor, INDEX_ACTIVE) == 0


async def test_permanent_error_is_not_retried(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _actor, audio, _attempt = await _indexable(db_session, monkeypatch)
    provider = ScriptedProvider(EmbeddingInputTooLong("demasiado largo"))

    await index(_ctx(db_session, provider), str(audio.id), 1)  # no Retry is raised

    assert len(provider.calls) == 1
    run = await _run(db_session, audio)
    assert run is not None
    assert (run.status, run.error_code) == ("failed", "embedding_input_too_long")


async def test_failure_before_activation_leaves_no_active_version(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    actor, audio, _attempt = await _indexable(db_session, monkeypatch)
    provider = ScriptedProvider(EmbeddingInputTooLong("demasiado largo"))

    await index(_ctx(db_session, provider), str(audio.id), 1)

    version = index_version_for(audio.id, 1)
    assert await _generations(db_session, actor, INDEX_ACTIVE) == 0
    generation = (
        await db_session.execute(
            select(IndexGeneration).where(IndexGeneration.version == version)
        )
    ).scalar_one()
    assert generation.status == INDEX_BUILDING
    chunks = await db_session.scalar(
        select(func.count()).select_from(Chunk).where(Chunk.index_version == version)
    )
    embeddings = await db_session.scalar(
        select(func.count()).select_from(Embedding).where(Embedding.index_version == version)
    )
    assert chunks and chunks > 0  # written, but invisible while the generation is building
    assert embeddings == 0
    await db_session.refresh(audio)
    assert audio.active_index_version is None

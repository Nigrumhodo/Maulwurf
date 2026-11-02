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
from arq.connections import ArqRedis
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
from app.services.index_job import (
    RunClaim,
    RunFenced,
    RunFenceOutcome,
    activate_claimed_generation,
    claim_run,
    finish_run,
    index_version_for,
)
from app.services.indexing import (
    INDEX_ACTIVE,
    INDEX_BUILDING,
    build_generation,
    create_generation,
)
from app.services.reconciliation import close_expired_run
from app.services.runs import INDEX_CONFIG_VERSION
from app.workers.index_job import PROVIDERS_KEY, SESSIONS_KEY, index
from app.workers.reconciler import reconcile_once
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


class ClosingProvider(FakeProvider):
    """One embedding per batch; before the second batch, the run is closed by the test.

    Models a reconciler that reclaimed an expired run while the stale worker was between
    batches: the first batch is already durable, the next one must be rejected by the fence.
    """

    def __init__(self, close_run: Any) -> None:
        super().__init__(batch_size=1)
        self.close_run = close_run

    async def embed(self, texts: Any) -> list[list[float]]:
        self.calls.append(list(texts))
        if len(self.calls) == 2:
            await self.close_run()
        return [[0.25] * self.dimension for _ in texts]


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


def _sessions(db: AsyncSession) -> Any:
    def factory() -> Any:
        return _same_session(db)

    return factory


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
        config_version=INDEX_CONFIG_VERSION,
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
    assert run.fencing_token == 1  # the claim that produced this activation
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


async def test_each_claim_takes_the_next_fencing_token(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A takeover is a new claim: the old token cannot finish the row the new one owns."""
    actor, audio, _attempt = await _indexable(db_session, monkeypatch)
    now = datetime.now(UTC)

    first = await claim_run(
        db_session,
        user_id=actor.user_id,
        audio_id=audio.id,
        transcript_version=1,
        now=now - timedelta(hours=1),
    )
    second = await claim_run(
        db_session, user_id=actor.user_id, audio_id=audio.id, transcript_version=1, now=now
    )

    assert isinstance(first, RunClaim) and isinstance(second, RunClaim)
    assert second.run_id == first.run_id
    assert (first.fencing_token, second.fencing_token) == (1, 2)
    stale = await finish_run(
        db_session,
        run_id=first.run_id,
        fencing_token=first.fencing_token,
        status="succeeded",
        error_code=None,
    )
    assert stale is RunFenceOutcome.FENCED
    run = await _run(db_session, audio)
    assert run is not None
    await db_session.refresh(run)
    assert (run.status, run.fencing_token) == ("running", 2)

    applied = await finish_run(
        db_session,
        run_id=second.run_id,
        fencing_token=second.fencing_token,
        status="succeeded",
        error_code=None,
    )
    assert applied is RunFenceOutcome.APPLIED
    await db_session.refresh(run)
    assert run.status == "succeeded"


async def test_finish_after_the_reconciler_reclaimed_the_run_is_fenced(
    db_session: AsyncSession, redis: tuple[ArqRedis, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    pool, queue = redis
    actor, audio, _attempt = await _indexable(db_session, monkeypatch)
    now = datetime.now(UTC)
    claim = await claim_run(
        db_session,
        user_id=actor.user_id,
        audio_id=audio.id,
        transcript_version=1,
        now=now - timedelta(hours=1),
    )
    assert isinstance(claim, RunClaim)

    report = await reconcile_once(_sessions(db_session), pool, queue_name=queue, now=now)

    assert report["expired_runs"] == 1
    outcome = await finish_run(
        db_session,
        run_id=claim.run_id,
        fencing_token=claim.fencing_token,
        status="succeeded",
        error_code=None,
    )
    assert outcome is RunFenceOutcome.FENCED  # typed no-op, never overwrites the terminal row
    run = await _run(db_session, audio)
    assert run is not None
    await db_session.refresh(run)
    assert (run.status, run.error_code, run.fencing_token) == (
        "failed",
        "lease_expired",
        claim.fencing_token,
    )


async def test_activation_after_orphan_cleanup_is_refused(
    db_session: AsyncSession, redis: tuple[ArqRedis, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    pool, queue = redis
    actor, audio, _attempt = await _indexable(db_session, monkeypatch)
    now = datetime.now(UTC)
    claim = await claim_run(
        db_session,
        user_id=actor.user_id,
        audio_id=audio.id,
        transcript_version=1,
        now=now - timedelta(hours=1),
    )
    assert isinstance(claim, RunClaim)
    version = index_version_for(audio.id, 1)
    await create_generation(
        db_session, user_id=actor.user_id, version=version, commit=False
    )
    await db_session.flush()
    await build_generation(
        db_session,
        user_id=actor.user_id,
        version=version,
        provider=FakeProvider(),
        text_kind="real",
    )

    report = await reconcile_once(_sessions(db_session), pool, queue_name=queue, now=now)

    assert report["expired_runs"] == 1
    assert report["orphan_builds"] == 1  # the stale worker's build was discarded
    with pytest.raises(RunFenced):
        await activate_claimed_generation(
            db_session, user_id=actor.user_id, version=version, claim=claim
        )
    assert await _generations(db_session, actor, INDEX_ACTIVE) == 0


async def _two_indexable_classes(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> tuple[Actor, list[Audio]]:
    """Two eligible classes: chunking commits twice and embeddings run in single batches."""
    monkeypatch.setattr(settings, "d3_text_closed_models", (settings.embedding_model,))
    _match_production_session(db)
    actor = await make_actor(db)
    audios: list[Audio] = []
    for _ in range(2):
        audio, _attempt, transcript = await make_class_with_transcript(db, actor)
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
        audios.append(audio)
    return actor, audios


async def test_build_batch_after_the_run_was_closed_is_fenced(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I-S2-AN-17: the batch after the reconciler closes the run never becomes durable.

    Without the per-batch fence the stale worker would commit its second embedding batch and
    the next build could adopt those rows under the same `index_version` (ADR-0009).
    """
    actor, audios = await _two_indexable_classes(db_session, monkeypatch)
    audio = audios[0]
    closed_at = datetime.now(UTC) + timedelta(hours=1)

    async def close_run() -> None:
        run = await _run(db_session, audio)
        assert run is not None
        applied = await close_expired_run(
            db_session,
            run_id=run.id,
            fencing_token=run.fencing_token,
            now=closed_at,
        )
        assert applied

    provider = ClosingProvider(close_run)
    await index(_ctx(db_session, provider), str(audio.id), 1)

    assert len(provider.calls) == 2
    audio_id = audio.id
    db_session.expunge_all()
    run = await _run(db_session, audio)
    assert run is not None
    assert (run.status, run.error_code) == ("failed", "lease_expired")
    version = index_version_for(audio_id, 1)
    embeddings = await db_session.scalar(
        select(func.count()).select_from(Embedding).where(Embedding.index_version == version)
    )
    assert embeddings == 1  # only the batch committed before the close
    assert await _generations(db_session, actor, INDEX_ACTIVE) == 0


async def test_a_stale_claim_cannot_activate_a_second_time(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    actor, audio, _attempt = await _indexable(db_session, monkeypatch)
    provider = FakeProvider()
    await index(_ctx(db_session, provider), str(audio.id), 1)
    run = await _run(db_session, audio)
    assert run is not None
    await db_session.refresh(run)
    assert (run.status, run.fencing_token) == ("succeeded", 1)

    # Same token as the successful claim: only the status='running' predicate refuses it.
    stale = RunClaim(run_id=run.id, fencing_token=run.fencing_token)
    with pytest.raises(RunFenced):
        await activate_claimed_generation(
            db_session,
            user_id=actor.user_id,
            version=index_version_for(audio.id, 1),
            claim=stale,
        )

    assert await _generations(db_session, actor, INDEX_ACTIVE) == 1
    assert len(provider.calls) == 1


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

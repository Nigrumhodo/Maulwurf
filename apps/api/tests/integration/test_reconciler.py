"""I-S2-JF-04: reconciler against PostgreSQL and Redis (J2.4).

Lost dispatches, dead index runs, orphan and surplus generations, and duplicate executions. The
reconciler runs on the test's own transaction (`db_session` is rolled back after each test) and
on an isolated ARQ queue (`redis` fixture), so no worker from compose consumes these jobs.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from arq.connections import ArqRedis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import (
    Audio,
    Chunk,
    Conversation,
    Embedding,
    IndexGeneration,
    Message,
    MessageSource,
    OutboxEvent,
    ProcessingRun,
    Segment,
    Transcript,
)
from app.services.index_job import index_version_for
from app.services.outbox import Outcome
from app.services.runs import RUN_CONFIG_VERSION
from app.workers.dispatcher import dispatch_pending
from app.workers.index_job import MAX_TRIES, PROVIDERS_KEY, SESSIONS_KEY, index
from app.workers.reconciler import outbox_job_keys, reconcile_once
from tests.integration.actors import Actor, make_actor, make_class_with_transcript
from tests.unit.test_indexing import FakeProvider

pytestmark = pytest.mark.integration

QUEUE_CHECKS = ("lost_dispatches", "expired_runs", "orphan_builds", "retired_generations")


@asynccontextmanager
async def _same_session(db: AsyncSession) -> AsyncIterator[AsyncSession]:
    yield db


def _sessions(db: AsyncSession) -> Any:
    def factory() -> Any:
        return _same_session(db)

    return factory


async def _class(db: AsyncSession) -> tuple[Actor, Audio, Transcript]:
    # Production sessions keep attributes after commit (`expire_on_commit=False`).
    db.sync_session.expire_on_commit = False
    actor = await make_actor(db)
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
    return actor, audio, transcript


def _event(
    actor: Actor,
    audio: Audio,
    *,
    status: str,
    published_at: datetime | None,
    attempts: int = 0,
    max_attempts: int = 3,
) -> OutboxEvent:
    return OutboxEvent(
        user_id=actor.user_id,
        type="index_requested",
        resource_type="audio",
        resource_id=audio.id,
        resource_version=1,
        enabled=True,
        status=status,
        published_at=published_at,
        attempts=attempts,
        max_attempts=max_attempts,
    )


def _run(
    actor: Actor,
    audio: Audio,
    *,
    status: str,
    started_at: datetime | None,
    attempt_count: int = 1,
    error_code: str | None = None,
) -> ProcessingRun:
    return ProcessingRun(
        user_id=actor.user_id,
        audio_id=audio.id,
        stage="index",
        transcript_version=1,
        config_version=RUN_CONFIG_VERSION,
        status=status,
        started_at=started_at,
        attempt_count=attempt_count,
        error_code=error_code,
    )


async def _queued_index_jobs(pool: ArqRedis, queue: str) -> list[Any]:
    return [job for job in await pool.queued_jobs(queue_name=queue) if job.function == "index"]


async def test_lost_dispatch_is_republished_once(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    pool, queue = redis
    now = datetime.now(UTC)
    actor, audio, _transcript = await _class(db_session)
    event = _event(actor, audio, status="dispatched", published_at=now - timedelta(hours=2))
    db_session.add(event)
    await db_session.flush()

    report = await reconcile_once(_sessions(db_session), pool, queue_name=queue, now=now)

    assert report["lost_dispatches"] == 1
    assert event.status == "pending" and event.published_at is None
    dispatched = await dispatch_pending(db_session, pool, queue_name=queue)
    assert dispatched[Outcome.PUBLISH] == 1
    jobs = await _queued_index_jobs(pool, queue)
    assert [job.args for job in jobs] == [(str(audio.id), 1)]
    assert jobs[0].job_id == f"outbox:{event.id}"


async def test_lost_dispatch_at_the_attempt_limit_is_left_alone(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    # A delivery that legitimately no-ops (D3-Text open) creates no run. The attempt bound
    # must stop the reconciler from republishing it forever.
    pool, queue = redis
    now = datetime.now(UTC)
    actor, audio, _transcript = await _class(db_session)
    event = _event(
        actor,
        audio,
        status="dispatched",
        published_at=now - timedelta(hours=2),
        attempts=3,
        max_attempts=3,
    )
    db_session.add(event)
    await db_session.flush()

    report = await reconcile_once(_sessions(db_session), pool, queue_name=queue, now=now)

    assert report["lost_dispatches"] == 0
    assert event.status == "dispatched"
    assert await _queued_index_jobs(pool, queue) == []


async def test_failed_lease_expired_dispatch_is_republished(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    # If the recovery job is lost after the run was closed, the outbox must regenerate it.
    pool, queue = redis
    now = datetime.now(UTC)
    actor, audio, _transcript = await _class(db_session)
    event = _event(actor, audio, status="dispatched", published_at=now - timedelta(hours=2))
    run = _run(
        actor,
        audio,
        status="failed",
        started_at=now - timedelta(hours=2),
        error_code="lease_expired",
    )
    db_session.add_all([event, run])
    await db_session.flush()

    report = await reconcile_once(_sessions(db_session), pool, queue_name=queue, now=now)

    assert report["lost_dispatches"] == 1
    assert event.status == "pending" and event.published_at is None


async def test_failed_permanent_dispatch_is_left_alone(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    pool, queue = redis
    now = datetime.now(UTC)
    actor, audio, _transcript = await _class(db_session)
    event = _event(actor, audio, status="dispatched", published_at=now - timedelta(hours=2))
    run = _run(
        actor,
        audio,
        status="failed",
        started_at=now - timedelta(hours=2),
        error_code="provider_error",
    )
    db_session.add_all([event, run])
    await db_session.flush()

    report = await reconcile_once(_sessions(db_session), pool, queue_name=queue, now=now)

    assert report["lost_dispatches"] == 0
    assert event.status == "dispatched"


@pytest.mark.parametrize("key_prefix", ["queued", "in-progress", "result"])
async def test_dispatch_with_a_redis_trace_is_left_alone(
    db_session: AsyncSession, redis: tuple[ArqRedis, str], key_prefix: str
) -> None:
    pool, queue = redis
    now = datetime.now(UTC)
    actor, audio, _transcript = await _class(db_session)
    event = _event(actor, audio, status="dispatched", published_at=now - timedelta(hours=2))
    db_session.add(event)
    await db_session.flush()
    if key_prefix == "queued":
        await pool.enqueue_job(
            "index", str(audio.id), 1, _job_id=f"outbox:{event.id}", _queue_name=queue
        )
    else:
        in_progress, result = outbox_job_keys(event.id)[1:]
        await pool.set(in_progress if key_prefix == "in-progress" else result, b"1", ex=60)
    try:
        report = await reconcile_once(_sessions(db_session), pool, queue_name=queue, now=now)
    finally:
        await pool.delete(*outbox_job_keys(event.id))

    assert report["lost_dispatches"] == 0
    assert event.status == "dispatched" and event.published_at is not None


@pytest.mark.parametrize("case", ["inside_grace", "terminal_run"])
async def test_recent_or_completed_dispatch_is_not_republished(
    db_session: AsyncSession, redis: tuple[ArqRedis, str], case: str
) -> None:
    pool, queue = redis
    now = datetime.now(UTC)
    actor, audio, _transcript = await _class(db_session)
    published = now - timedelta(minutes=5) if case == "inside_grace" else now - timedelta(hours=2)
    event = _event(actor, audio, status="dispatched", published_at=published)
    db_session.add(event)
    if case == "terminal_run":
        db_session.add(_run(actor, audio, status="failed", started_at=None))
    await db_session.flush()

    report = await reconcile_once(_sessions(db_session), pool, queue_name=queue, now=now)

    assert report["lost_dispatches"] == 0
    assert event.status == "dispatched"


async def test_overdue_pending_event_is_left_to_the_dispatcher(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    pool, queue = redis
    now = datetime.now(UTC)
    actor, audio, _transcript = await _class(db_session)
    event = _event(actor, audio, status="pending", published_at=None)
    event.next_attempt_at = now - timedelta(minutes=1)
    db_session.add(event)
    await db_session.flush()

    report = await reconcile_once(_sessions(db_session), pool, queue_name=queue, now=now)
    assert report["lost_dispatches"] == 0

    dispatched = await dispatch_pending(db_session, pool, queue_name=queue, clock=lambda: now)
    assert dispatched[Outcome.PUBLISH] == 1


async def test_expired_run_is_closed_and_reenqueued(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    pool, queue = redis
    now = datetime.now(UTC)
    actor, audio, _transcript = await _class(db_session)
    run = _run(actor, audio, status="running", started_at=now - timedelta(hours=1))
    db_session.add(run)
    await db_session.flush()

    await reconcile_once(_sessions(db_session), pool, queue_name=queue, now=now)

    await db_session.refresh(run)
    assert run.status == "failed"
    assert run.error_code == "lease_expired"
    assert run.finished_at is not None
    jobs = await _queued_index_jobs(pool, queue)
    assert [job.args for job in jobs] == [(str(audio.id), 1)]
    assert jobs[0].job_id == f"reconcile-index:{run.id}:1"


async def test_live_run_is_not_reaped(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    pool, queue = redis
    now = datetime.now(UTC)
    actor, audio, _transcript = await _class(db_session)
    run = _run(actor, audio, status="running", started_at=now - timedelta(minutes=1))
    db_session.add(run)
    await db_session.flush()

    report = await reconcile_once(_sessions(db_session), pool, queue_name=queue, now=now)

    assert report["expired_runs"] == 0
    await db_session.refresh(run)
    assert run.status == "running" and run.error_code is None
    assert await _queued_index_jobs(pool, queue) == []


async def test_exhausted_run_is_closed_without_requeue(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    pool, queue = redis
    now = datetime.now(UTC)
    actor, audio, _transcript = await _class(db_session)
    run = _run(
        actor, audio, status="running", started_at=now - timedelta(hours=1),
        attempt_count=MAX_TRIES,
    )
    db_session.add(run)
    await db_session.flush()

    await reconcile_once(_sessions(db_session), pool, queue_name=queue, now=now)

    await db_session.refresh(run)
    assert (run.status, run.error_code) == ("failed", "lease_expired")
    assert await _queued_index_jobs(pool, queue) == []


async def test_two_cycles_enqueue_one_recovery_job(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    pool, queue = redis
    now = datetime.now(UTC)
    actor, audio, _transcript = await _class(db_session)
    db_session.add(_run(actor, audio, status="running", started_at=now - timedelta(hours=1)))
    await db_session.flush()

    await reconcile_once(_sessions(db_session), pool, queue_name=queue, now=now)
    second = await reconcile_once(_sessions(db_session), pool, queue_name=queue, now=now)

    assert second["expired_runs"] == 0
    assert len(await _queued_index_jobs(pool, queue)) == 1


async def test_recovered_index_job_has_a_single_effect(
    db_session: AsyncSession, redis: tuple[ArqRedis, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    pool, queue = redis
    now = datetime.now(UTC)
    monkeypatch.setattr(settings, "d3_text_closed_models", (settings.embedding_model,))
    actor, audio, _transcript = await _class(db_session)
    db_session.add(_run(actor, audio, status="running", started_at=now - timedelta(hours=1)))
    await db_session.flush()
    await reconcile_once(_sessions(db_session), pool, queue_name=queue, now=now)
    provider = FakeProvider()
    context: dict[str, Any] = {
        SESSIONS_KEY: _sessions(db_session),
        PROVIDERS_KEY: lambda: provider,
        "job_try": 1,
    }

    await index(context, str(audio.id), 1)  # the recovered delivery
    await index(context, str(audio.id), 1)  # a duplicate delivery of the same job

    count = await db_session.scalar(
        select(func.count())
        .select_from(IndexGeneration)
        .where(
            IndexGeneration.user_id == actor.user_id,
            IndexGeneration.status == "active",
            IndexGeneration.version == index_version_for(audio.id, 1),
        )
    )
    assert count == 1
    assert len(provider.calls) == 1


def _chunk(actor: Actor, transcript: Transcript, version: str) -> Chunk:
    return Chunk(
        user_id=actor.user_id,
        audio_id=transcript.audio_id,
        transcript_id=transcript.id,
        transcript_version=transcript.version,
        index_version=version,
        ordinal=1,
        content="fragmento",
        char_start=0,
        char_end=9,
        token_count=1,
    )


def _generation(
    actor: Actor, version: str, status: str, *, activated_at: datetime | None = None
) -> IndexGeneration:
    return IndexGeneration(
        user_id=actor.user_id,
        version=version,
        model="test-model",
        dimension=1536,
        status=status,
        activated_at=activated_at,
    )


async def _materialize(
    db: AsyncSession, actor: Actor, transcript: Transcript, generation: IndexGeneration
) -> Chunk:
    db.add(generation)
    chunk = _chunk(actor, transcript, generation.version)
    db.add(chunk)
    await db.flush()
    db.add(
        Embedding(
            user_id=actor.user_id,
            chunk_id=chunk.id,
            index_version=generation.version,
            model="test-model",
            dimension=1536,
            vector=[0.0] * 1536,
        )
    )
    await db.flush()
    return chunk


async def _count(db: AsyncSession, model: Any, **filters: Any) -> int:
    statement = select(func.count()).select_from(model)
    for column, value in filters.items():
        statement = statement.where(getattr(model, column) == value)
    return int(await db.scalar(statement) or 0)


async def test_orphan_build_is_discarded_with_its_vectors(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    pool, queue = redis
    actor, _audio, transcript = await _class(db_session)
    version = "audio-orphan-t1"
    await _materialize(db_session, actor, transcript, _generation(actor, version, "building"))

    report = await reconcile_once(_sessions(db_session), pool, queue_name=queue)

    assert report["orphan_builds"] == 1
    assert await _count(db_session, IndexGeneration, user_id=actor.user_id) == 0
    assert await _count(db_session, Chunk, user_id=actor.user_id) == 0
    assert await _count(db_session, Embedding, user_id=actor.user_id) == 0


async def test_build_with_a_live_run_is_kept(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    pool, queue = redis
    now = datetime.now(UTC)
    actor, audio, transcript = await _class(db_session)
    version = index_version_for(audio.id, 1)
    await _materialize(db_session, actor, transcript, _generation(actor, version, "building"))
    db_session.add(_run(actor, audio, status="running", started_at=now - timedelta(minutes=1)))
    await db_session.flush()

    report = await reconcile_once(_sessions(db_session), pool, queue_name=queue, now=now)

    assert report["orphan_builds"] == 0
    assert await _count(db_session, IndexGeneration, version=version) == 1
    assert await _count(db_session, Chunk, index_version=version) == 1


async def test_only_the_newest_retired_generation_is_kept(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    pool, queue = redis
    now = datetime.now(UTC)
    actor, _audio, transcript = await _class(db_session)
    older = _generation(actor, "gen-older", "retired", activated_at=now - timedelta(days=2))
    newer = _generation(actor, "gen-newer", "retired", activated_at=now - timedelta(days=1))
    current = _generation(actor, "gen-current", "active", activated_at=now)
    for generation in (older, newer, current):
        await _materialize(db_session, actor, transcript, generation)

    report = await reconcile_once(_sessions(db_session), pool, queue_name=queue, now=now)

    assert report["retired_generations"] == 1
    assert await _count(db_session, Chunk, index_version="gen-older") == 0
    assert await _count(db_session, Chunk, index_version="gen-newer") == 1
    assert await _count(db_session, Chunk, index_version="gen-current") == 1
    assert await _count(db_session, Embedding, index_version="gen-older") == 0
    assert await _count(db_session, IndexGeneration, status="active") == 1
    assert await _count(db_session, IndexGeneration, status="retired") == 1


async def test_retired_generation_with_a_cited_chunk_is_kept(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    """A delivered citation keeps its generation: message_sources has no ON DELETE CASCADE."""
    pool, queue = redis
    now = datetime.now(UTC)
    actor, _audio, transcript = await _class(db_session)
    cited = _generation(actor, "gen-cited", "retired", activated_at=now - timedelta(days=3))
    surplus = _generation(actor, "gen-surplus", "retired", activated_at=now - timedelta(days=2))
    rollback = _generation(actor, "gen-rollback", "retired", activated_at=now - timedelta(days=1))
    current = _generation(actor, "gen-current", "active", activated_at=now)
    cited_chunk = await _materialize(db_session, actor, transcript, cited)
    for generation in (surplus, rollback, current):
        await _materialize(db_session, actor, transcript, generation)

    conversation = Conversation(user_id=actor.user_id, mode="all")
    db_session.add(conversation)
    await db_session.flush()
    message = Message(
        user_id=actor.user_id,
        conversation_id=conversation.id,
        role="assistant",
        status="completed",
        content="respuesta",
    )
    db_session.add(message)
    await db_session.flush()
    db_session.add(
        MessageSource(
            user_id=actor.user_id,
            message_id=message.id,
            kind="transcript",
            audio_id=transcript.audio_id,
            transcript_id=transcript.id,
            transcript_version=transcript.version,
            chunk_id=cited_chunk.id,
        )
    )
    await db_session.flush()

    report = await reconcile_once(_sessions(db_session), pool, queue_name=queue, now=now)

    assert report["retired_generations"] == 1
    assert await _count(db_session, Chunk, index_version="gen-cited") == 1
    assert await _count(db_session, Embedding, index_version="gen-cited") == 1
    assert await _count(db_session, IndexGeneration, version="gen-cited") == 1
    assert await _count(db_session, MessageSource) == 1
    assert await _count(db_session, Chunk, index_version="gen-surplus") == 0
    assert await _count(db_session, IndexGeneration, version="gen-surplus") == 0
    assert await _count(db_session, Chunk, index_version="gen-rollback") == 1

"""I-S2-JF-03: losing Redis does not lose work, and never reconstructs audio (J2.3).

Redis holds only the ARQ queue. The source of truth is PostgreSQL: the transcript, the outbox
event and the `processing_runs` row. The test runs `FLUSHALL` on the test Redis (CI owns this
instance), then regenerates the index job with the J2.4 reconciler and the outbox dispatcher, and
consumes it with a real ARQ burst worker until it completes.

Audio is never reconstructed: an attempt that never committed text is not regenerated, and no
index job is queued for it. The embeddings provider is `FakeProvider` (no network).
"""

import sys
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import asyncpg
import pytest
from arq.connections import ArqRedis
from arq.worker import Worker, func
from sqlalchemy import func as sql_func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import (
    Audio,
    IndexGeneration,
    IngestionAttempt,
    OutboxEvent,
    ProcessingRun,
    Segment,
    Transcript,
)
from app.services.index_job import index_version_for
from app.services.outbox import Outcome
from app.services.reconciliation import DISPATCH_GRACE
from app.services.runs import RUN_CONFIG_VERSION
from app.workers.dispatcher import dispatch_pending
from app.workers.index_job import JOB_TIMEOUT_S, MAX_TRIES, PROVIDERS_KEY, SESSIONS_KEY, index
from app.workers.reconciler import reconcile_once
from tests.integration.actors import (
    Actor,
    make_actor,
    make_attempt,
    make_audio,
    make_class_with_transcript,
)
from tests.unit.test_indexing import FakeProvider

# The ingest sweep lives in apps/ingest; it imports only the standard library and asyncpg.
sys.path.append(str(Path(__file__).resolve().parents[3] / "ingest"))
from maulwurf_ingest.reconciler import sweep_stranded  # noqa: E402

pytestmark = pytest.mark.integration


@asynccontextmanager
async def _same_session(db: AsyncSession) -> AsyncIterator[AsyncSession]:
    yield db


def _sessions(db: AsyncSession) -> Any:
    def factory() -> Any:
        return _same_session(db)

    return factory


@pytest.fixture
def indexable_text(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """D3-Texto closed for the test model, so the index job may build from text."""
    monkeypatch.setattr(settings, "d3_text_closed_models", (settings.embedding_model,))
    yield


async def _committed_class(db: AsyncSession) -> tuple[Actor, Audio, Transcript]:
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


async def _stranded_attempt(db: AsyncSession, actor: Actor) -> IngestionAttempt:
    """An attempt whose ingest owner died mid-transcription. Its text was never committed."""
    audio = await make_audio(db, actor)
    attempt = await make_attempt(db, actor, audio=audio)
    attempt.status = "transcribing"
    attempt.owner_instance = "ingest:dead"
    attempt.lease_expires_at = datetime.now(UTC) - timedelta(hours=1)
    await db.flush()
    return attempt


def _index_event(actor: Actor, audio: Audio) -> OutboxEvent:
    return OutboxEvent(
        user_id=actor.user_id,
        type="index_requested",
        resource_type="audio",
        resource_id=audio.id,
        resource_version=1,
        enabled=True,
        status="pending",
        published_at=None,
    )


def _worker(pool: ArqRedis, queue: str, context: dict[str, Any]) -> Worker:
    return Worker(
        functions=[func(index, max_tries=MAX_TRIES, timeout=JOB_TIMEOUT_S)],
        queue_name=queue,
        redis_pool=pool,
        burst=True,
        handle_signals=False,
        ctx=context,
        poll_delay=0.01,
    )


async def _queued_args(pool: ArqRedis, queue: str) -> list[tuple[Any, ...]]:
    return [job.args for job in await pool.queued_jobs(queue_name=queue)]


async def test_flushall_regenerates_the_index_job_to_completion(
    db_session: AsyncSession,
    redis: tuple[ArqRedis, str],
    indexable_text: None,
) -> None:
    pool, queue = redis
    actor, audio, _transcript = await _committed_class(db_session)
    published = datetime.now(UTC)
    event = _index_event(actor, audio)
    db_session.add(event)
    await db_session.flush()
    await dispatch_pending(db_session, pool, queue_name=queue, clock=lambda: published)
    assert await _queued_args(pool, queue) == [(str(audio.id), 1)]

    await pool.flushall()
    assert await pool.queued_jobs(queue_name=queue) == []

    # The lost job is only regenerated after the grace margin: a dispatch still in flight is not.
    later = published + DISPATCH_GRACE + timedelta(minutes=1)
    report = await reconcile_once(_sessions(db_session), pool, queue_name=queue, now=later)
    assert report["lost_dispatches"] == 1
    republished = await dispatch_pending(db_session, pool, queue_name=queue, clock=lambda: later)
    assert republished[Outcome.PUBLISH] == 1
    assert await _queued_args(pool, queue) == [(str(audio.id), 1)]

    provider = FakeProvider()
    context: dict[str, Any] = {
        SESSIONS_KEY: _sessions(db_session),
        PROVIDERS_KEY: lambda: provider,
    }
    await _worker(pool, queue, context).async_run()

    version = index_version_for(audio.id, 1)
    active = await db_session.scalar(
        select(sql_func.count())
        .select_from(IndexGeneration)
        .where(
            IndexGeneration.user_id == actor.user_id,
            IndexGeneration.status == "active",
            IndexGeneration.version == version,
        )
    )
    assert active == 1
    assert len(provider.calls) == 1
    await db_session.refresh(audio)
    assert audio.active_index_version == version
    run = await db_session.scalar(
        select(ProcessingRun.status).where(
            ProcessingRun.audio_id == audio.id,
            ProcessingRun.stage == "index",
            ProcessingRun.config_version == RUN_CONFIG_VERSION,
        )
    )
    assert run == "succeeded"

    # A second reconciler pass after completion regenerates nothing and runs nothing twice.
    again = await reconcile_once(_sessions(db_session), pool, queue_name=queue, now=later)
    assert again["lost_dispatches"] == 0 and again["expired_runs"] == 0
    assert await _queued_args(pool, queue) == []
    assert len(provider.calls) == 1


async def test_audio_without_committed_text_is_never_reconstructed(
    db_session: AsyncSession,
    redis: tuple[ArqRedis, str],
    indexable_text: None,
) -> None:
    pool, queue = redis
    actor, _audio, _transcript = await _committed_class(db_session)
    stranded = await _stranded_attempt(db_session, actor)
    transcripts_before = await db_session.scalar(
        select(sql_func.count()).select_from(Transcript).where(Transcript.user_id == actor.user_id)
    )
    await pool.flushall()

    later = datetime.now(UTC) + DISPATCH_GRACE + timedelta(minutes=1)
    await reconcile_once(_sessions(db_session), pool, queue_name=queue, now=later)
    provider = FakeProvider()
    context: dict[str, Any] = {
        SESSIONS_KEY: _sessions(db_session),
        PROVIDERS_KEY: lambda: provider,
    }
    await _worker(pool, queue, context).async_run()

    # Nothing was queued for the stranded audio: queued jobs carry IDs only, never audio.
    assert await _queued_args(pool, queue) == []
    assert provider.calls == []
    await db_session.refresh(stranded)
    assert stranded.status == "transcribing"
    transcripts_after = await db_session.scalar(
        select(sql_func.count()).select_from(Transcript).where(Transcript.user_id == actor.user_id)
    )
    assert transcripts_after == transcripts_before


async def _driver_connection(db: AsyncSession) -> asyncpg.Connection:
    """The asyncpg connection behind the test transaction, so the sweep sees its rows."""
    connection = await db.connection()
    raw = await connection.get_raw_connection()
    return cast(asyncpg.Connection, raw.driver_connection)


async def test_stranded_attempt_ends_requires_reupload(
    db_session: AsyncSession,
    redis: tuple[ArqRedis, str],
) -> None:
    """I-S2-JF-03: the ingest sweep ends a stranded attempt. No ASR, no audio, no index job."""
    pool, queue = redis
    actor = await make_actor(db_session)
    stranded = await _stranded_attempt(db_session, actor)
    await db_session.refresh(stranded)
    token_before = stranded.fencing_token
    transcripts_before = await db_session.scalar(
        select(sql_func.count()).select_from(Transcript).where(Transcript.user_id == actor.user_id)
    )
    await pool.flushall()

    ended = await sweep_stranded(await _driver_connection(db_session))

    assert stranded.id in ended
    await db_session.refresh(stranded)
    assert (stranded.status, stranded.error_code) == ("requires_reupload", "technical_loss")
    assert stranded.fencing_token == token_before + 1
    assert await _queued_args(pool, queue) == []
    transcripts_after = await db_session.scalar(
        select(sql_func.count()).select_from(Transcript).where(Transcript.user_id == actor.user_id)
    )
    assert transcripts_after == transcripts_before

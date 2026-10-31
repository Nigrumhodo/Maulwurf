"""I-S3-SG-01 against PostgreSQL and Redis: analysis backfill only from persisted text.

Evidence for S3.1: an F1 class (``required_stages=['index']``) with an active persisted
transcript is upgraded to F2 and receives exactly one ``analyze`` run plus its cleanup-gated
``analyze_requested`` event. A class without usable text, with pending cleanup, tombstoned or
belonging to another tenant is never queued, and nothing in ``ingestion_attempts``,
``audios.sha256``/``original_bytes`` or the transcript changes. The test also proves the
dispatcher publishes only the audio id (the worker reads text from the database) and that the
class detail exposes the explicit queue state (D3.5).
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from arq.connections import ArqRedis
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session
from app.main import app
from app.models import IngestionAttempt, OutboxEvent, ProcessingRun, Transcript
from app.services.analysis_backfill import (
    BackfillStatus,
    backfill_classes,
)
from app.services.outbox import Outcome
from app.services.runs import RUN_CONFIG_VERSION
from app.workers.dispatcher import dispatch_pending
from tests.integration import actors

pytestmark = pytest.mark.integration


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    async def override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = override
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=settings.allowed_origin
    ) as c:
        yield c
    app.dependency_overrides.clear()


async def _attempt_rows(
    db: AsyncSession, *, user_id: uuid.UUID
) -> list[tuple[object, ...]]:
    rows = await db.execute(
        select(IngestionAttempt)
        .where(IngestionAttempt.user_id == user_id)
        .order_by(IngestionAttempt.id)
    )
    return [
        (
            attempt.id,
            attempt.status,
            attempt.cleanup_status,
            attempt.fencing_token,
            attempt.received_bytes,
            attempt.error_code,
        )
        for attempt in rows.scalars()
    ]


async def _transcript_rows(
    db: AsyncSession, *, user_id: uuid.UUID
) -> list[tuple[object, ...]]:
    rows = await db.execute(
        select(Transcript).where(Transcript.user_id == user_id).order_by(Transcript.id)
    )
    return [
        (
            transcript.id,
            transcript.version,
            transcript.text,
            transcript.char_count,
            transcript.timestamp_precision,
        )
        for transcript in rows.scalars()
    ]


async def _analysis_runs(
    db: AsyncSession, *, user_id: uuid.UUID, audio_id: uuid.UUID
) -> list[ProcessingRun]:
    rows = await db.execute(
        select(ProcessingRun)
        .where(
            ProcessingRun.user_id == user_id,
            ProcessingRun.audio_id == audio_id,
            ProcessingRun.stage == "analyze",
        )
        .order_by(ProcessingRun.created_at)
    )
    return list(rows.scalars())


async def _analysis_events(
    db: AsyncSession, *, user_id: uuid.UUID, audio_id: uuid.UUID
) -> list[OutboxEvent]:
    rows = await db.execute(
        select(OutboxEvent)
        .where(
            OutboxEvent.user_id == user_id,
            OutboxEvent.resource_id == audio_id,
            OutboxEvent.type == "analyze_requested",
        )
        .order_by(OutboxEvent.created_at)
    )
    return list(rows.scalars())


async def test_backfill_queues_f1_class_without_touching_ingestion(
    db_session: AsyncSession,
) -> None:
    actor = await actors.make_actor(db_session)
    audio, _attempt, transcript = await actors.make_class_with_transcript(
        db_session, actor, required_stages=["index"]
    )
    await actors.make_timed_segment(
        db_session, actor, transcript_id=transcript.id, ordinal=1, t_start=0.0, t_end=2.0
    )
    db_session.add(
        ProcessingRun(
            user_id=actor.user_id,
            audio_id=audio.id,
            stage="index",
            transcript_version=transcript.version,
            config_version=RUN_CONFIG_VERSION,
            status="succeeded",
        )
    )
    await db_session.flush()
    attempts_before = await _attempt_rows(db_session, user_id=actor.user_id)
    transcripts_before = await _transcript_rows(db_session, user_id=actor.user_id)
    audio_before = (
        audio.sha256,
        audio.original_bytes,
        audio.duration_seconds,
        audio.active_transcript_version,
        audio.subject_id,
    )

    report = await backfill_classes(db_session, user_id=actor.user_id, apply=True)

    assert report.applied is True
    assert report.truncated is False
    assert report.counts[BackfillStatus.QUEUED] == 1
    item = report.items[0]
    assert item.audio_id == audio.id
    assert item.transcript_version == transcript.version
    assert item.run_id is not None
    assert item.run_status == "pending"

    assert audio.required_stages == ["index", "analyze"]
    runs = await _analysis_runs(db_session, user_id=actor.user_id, audio_id=audio.id)
    assert len(runs) == 1
    run = runs[0]
    assert run.id == item.run_id
    assert run.status == "pending"
    assert run.transcript_version == transcript.version
    assert run.config_version == RUN_CONFIG_VERSION

    events = await _analysis_events(db_session, user_id=actor.user_id, audio_id=audio.id)
    assert len(events) == 1
    event = events[0]
    assert event.resource_type == "audio"
    assert event.resource_version == transcript.version
    assert event.enabled is True
    assert event.blocked_reason is None
    assert event.status == "pending"
    # Only IDs/versions; no text, path, audio or token enters the outbox.
    assert event.payload == {}

    # The backfill is text-only: the attempt, the transcript and the audio identity are
    # byte-for-byte unchanged (only the stage list gained `analyze`).
    assert await _attempt_rows(db_session, user_id=actor.user_id) == attempts_before
    assert await _transcript_rows(db_session, user_id=actor.user_id) == transcripts_before
    assert (
        audio.sha256,
        audio.original_bytes,
        audio.duration_seconds,
        audio.active_transcript_version,
        audio.subject_id,
    ) == audio_before


async def test_dry_run_does_not_write_or_upgrade_anything(
    db_session: AsyncSession,
) -> None:
    actor = await actors.make_actor(db_session)
    audio, _attempt, _transcript = await actors.make_class_with_transcript(
        db_session, actor, required_stages=["index"]
    )

    report = await backfill_classes(db_session, user_id=actor.user_id, apply=False)

    assert report.applied is False
    assert report.counts[BackfillStatus.WOULD_QUEUE] == 1
    assert audio.required_stages == ["index"]
    assert await _analysis_runs(db_session, user_id=actor.user_id, audio_id=audio.id) == []
    assert await _analysis_events(db_session, user_id=actor.user_id, audio_id=audio.id) == []


async def test_second_apply_is_idempotent(db_session: AsyncSession) -> None:
    actor = await actors.make_actor(db_session)
    audio, _attempt, _transcript = await actors.make_class_with_transcript(
        db_session, actor, required_stages=["index"]
    )

    first = await backfill_classes(db_session, user_id=actor.user_id, apply=True)
    second = await backfill_classes(db_session, user_id=actor.user_id, apply=True)

    assert first.counts[BackfillStatus.QUEUED] == 1
    assert second.counts[BackfillStatus.ALREADY_QUEUED] == 1
    assert len(await _analysis_runs(db_session, user_id=actor.user_id, audio_id=audio.id)) == 1
    assert len(await _analysis_events(db_session, user_id=actor.user_id, audio_id=audio.id)) == 1


async def test_cleanup_pending_is_never_queued(db_session: AsyncSession) -> None:
    actor = await actors.make_actor(db_session)
    audio, _attempt, _transcript, _segments, _events = (
        await actors.make_committed_cleanup_pending(db_session, actor)
    )

    report = await backfill_classes(db_session, user_id=actor.user_id, apply=True)

    assert report.counts[BackfillStatus.CLEANUP_PENDING] == 1
    assert audio.required_stages == ["index"]
    assert await _analysis_runs(db_session, user_id=actor.user_id, audio_id=audio.id) == []
    assert [
        event
        for event in await _analysis_events(
            db_session, user_id=actor.user_id, audio_id=audio.id
        )
        if event.enabled
    ] == []


async def test_needs_persisted_text_and_stays_inside_the_tenant(
    db_session: AsyncSession,
) -> None:
    actor = await actors.make_actor(db_session)
    queued, _attempt, _transcript = await actors.make_class_with_transcript(
        db_session, actor, required_stages=["index"]
    )
    no_text = await actors.make_audio(db_session, actor)
    tombstoned, _attempt2, _transcript2 = await actors.make_class_with_transcript(
        db_session, actor, required_stages=["index"]
    )
    tombstoned.deleted_at = datetime.now(UTC)
    alien_actor = await actors.make_actor(db_session)
    alien, _attempt3, _transcript3 = await actors.make_class_with_transcript(
        db_session, alien_actor, required_stages=["index"]
    )
    await db_session.flush()

    report = await backfill_classes(
        db_session,
        user_id=actor.user_id,
        audio_ids=[queued.id, no_text.id, tombstoned.id, alien.id],
        apply=True,
    )

    statuses = {item.audio_id: item.status for item in report.items}
    assert statuses[queued.id] is BackfillStatus.QUEUED
    assert statuses[no_text.id] is BackfillStatus.NOT_SELECTED
    assert statuses[tombstoned.id] is BackfillStatus.NOT_SELECTED
    # A foreign class is indistinguishable from missing: its existence never leaks.
    assert statuses[alien.id] is BackfillStatus.NOT_FOUND
    excluded = (
        (no_text.id, actor.user_id),
        (tombstoned.id, actor.user_id),
        (alien.id, alien_actor.user_id),
    )
    for audio_id, owner_id in excluded:
        assert (
            await _analysis_runs(db_session, user_id=owner_id, audio_id=audio_id) == []
        )
        assert (
            await _analysis_events(db_session, user_id=owner_id, audio_id=audio_id) == []
        )


async def test_failed_analysis_is_requeued_with_a_fresh_cycle(
    db_session: AsyncSession,
) -> None:
    actor = await actors.make_actor(db_session)
    audio, _attempt, transcript = await actors.make_class_with_transcript(
        db_session, actor, required_stages=["index"]
    )
    failed = ProcessingRun(
        user_id=actor.user_id,
        audio_id=audio.id,
        stage="analyze",
        transcript_version=transcript.version,
        config_version=RUN_CONFIG_VERSION,
        status="failed",
        error_code="analysis_unexpected",
        attempt_count=0,
    )
    db_session.add(failed)
    await db_session.flush()
    failed_id = failed.id

    report = await backfill_classes(db_session, user_id=actor.user_id, apply=True)

    assert report.counts[BackfillStatus.QUEUED] == 1
    assert report.items[0].run_id == failed_id
    status = await db_session.scalar(
        select(ProcessingRun.status).where(ProcessingRun.id == failed_id)
    )
    attempts = await db_session.scalar(
        select(ProcessingRun.attempt_count).where(ProcessingRun.id == failed_id)
    )
    assert status == "pending"
    assert attempts == 1
    assert audio.required_stages == ["index", "analyze"]
    assert len(await _analysis_events(db_session, user_id=actor.user_id, audio_id=audio.id)) == 1


async def test_f2_classes_are_left_to_retry_unless_analysis_already_succeeded(
    db_session: AsyncSession,
) -> None:
    actor = await actors.make_actor(db_session)
    missing_run, _attempt, _transcript = await actors.make_class_with_transcript(
        db_session, actor, required_stages=["index", "analyze"]
    )
    analyzed, _attempt2, transcript2 = await actors.make_class_with_transcript(
        db_session, actor, required_stages=["index", "analyze"]
    )
    db_session.add(
        ProcessingRun(
            user_id=actor.user_id,
            audio_id=analyzed.id,
            stage="analyze",
            transcript_version=transcript2.version,
            config_version=RUN_CONFIG_VERSION,
            status="succeeded",
        )
    )
    await db_session.flush()

    report = await backfill_classes(db_session, user_id=actor.user_id, apply=True)

    statuses = {item.audio_id: item.status for item in report.items}
    # F2 without a run already owes analysis: retry is its entry point and the backfill
    # must say so instead of mislabeling the class as F1.
    assert statuses[missing_run.id] is BackfillStatus.ALREADY_F2_NO_RUN
    assert statuses[analyzed.id] is BackfillStatus.ALREADY_ANALYZED
    assert await _analysis_runs(
        db_session, user_id=actor.user_id, audio_id=missing_run.id
    ) == []
    assert len(await _analysis_runs(db_session, user_id=actor.user_id, audio_id=analyzed.id)) == 1
    # Neither class is an F1 candidate, so no stage list was rewritten.
    assert missing_run.required_stages == ["index", "analyze"]
    assert analyzed.required_stages == ["index", "analyze"]


@pytest.mark.parametrize(
    ("run_status", "expected"),
    [
        ("succeeded", BackfillStatus.ALREADY_ANALYZED),
        ("pending", BackfillStatus.ALREADY_QUEUED),
        ("running", BackfillStatus.ALREADY_QUEUED),
    ],
)
async def test_apply_does_not_promote_f1_when_nothing_is_queued(
    db_session: AsyncSession, run_status: str, expected: BackfillStatus
) -> None:
    """The F1 → F2 upgrade must only persist on the branch that really queues a run."""
    actor = await actors.make_actor(db_session)
    audio, _attempt, transcript = await actors.make_class_with_transcript(
        db_session, actor, required_stages=["index"]
    )
    db_session.add(
        ProcessingRun(
            user_id=actor.user_id,
            audio_id=audio.id,
            stage="analyze",
            transcript_version=transcript.version,
            config_version=RUN_CONFIG_VERSION,
            status=run_status,
        )
    )
    await db_session.flush()
    audio_id = audio.id

    report = await backfill_classes(db_session, user_id=actor.user_id, apply=True)

    assert report.counts[expected] == 1
    assert await _analysis_events(db_session, user_id=actor.user_id, audio_id=audio_id) == []
    # The script's `session_scope()` commits after the pass: the committed row must still
    # prove that no upgrade happened when no run/event was written.
    await db_session.commit()
    stages = await db_session.scalar(
        select(Audio.required_stages).where(Audio.id == audio_id)
    )
    assert stages == ["index"]


async def test_apply_does_not_promote_f1_on_unknown_run_state(
    db_session: AsyncSession,
) -> None:
    actor = await actors.make_actor(db_session)
    audio, _attempt, transcript = await actors.make_class_with_transcript(
        db_session, actor, required_stages=["index"]
    )
    # The API CHECK forbids writing corrupt run states; dropping it inside this test
    # transaction simulates the drift the backfill must fail closed on and is rolled back
    # with the rest of the test.
    await db_session.execute(
        text("ALTER TABLE processing_runs DROP CONSTRAINT ck_processing_runs_status")
    )
    db_session.add(
        ProcessingRun(
            user_id=actor.user_id,
            audio_id=audio.id,
            stage="analyze",
            transcript_version=transcript.version,
            config_version=RUN_CONFIG_VERSION,
            status="paused",
        )
    )
    await db_session.flush()
    audio_id = audio.id

    report = await backfill_classes(db_session, user_id=actor.user_id, apply=True)

    assert report.counts[BackfillStatus.UNKNOWN_RUN_STATE] == 1
    assert await _analysis_events(db_session, user_id=actor.user_id, audio_id=audio_id) == []
    await db_session.commit()
    stages = await db_session.scalar(
        select(Audio.required_stages).where(Audio.id == audio_id)
    )
    assert stages == ["index"]


async def test_detail_shows_the_explicit_queue_state(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    audio, _attempt, _transcript = await actors.make_class_with_transcript(
        db_session, actor, required_stages=["index"]
    )
    db_session.add(
        ProcessingRun(
            user_id=actor.user_id,
            audio_id=audio.id,
            stage="index",
            transcript_version=1,
            config_version=RUN_CONFIG_VERSION,
            status="succeeded",
        )
    )
    await db_session.flush()

    before = await client.get(f"/audios/{audio.id}", cookies=actor.cookies)
    assert before.status_code == 200
    assert before.json()["analysis_backfill_available"] is True
    assert before.json()["stages"]["analyze"] == "not_requested"

    await backfill_classes(db_session, user_id=actor.user_id, apply=True)

    after = await client.get(f"/audios/{audio.id}", cookies=actor.cookies)
    assert after.status_code == 200
    body = after.json()
    assert body["analysis_backfill_available"] is False
    assert body["stages"]["analyze"] == "pending"
    assert body["aggregate_status"] == "processing"


async def test_dispatcher_publishes_only_the_audio_id(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    actor = await actors.make_actor(db_session)
    audio, _attempt, _transcript = await actors.make_class_with_transcript(
        db_session, actor, required_stages=["index"]
    )
    pool, queue = redis
    await backfill_classes(db_session, user_id=actor.user_id, apply=True)

    report = await dispatch_pending(db_session, pool, queue_name=queue)

    assert report[Outcome.PUBLISH] == 1
    jobs = await pool.queued_jobs(queue_name=queue)
    assert [job.function for job in jobs] == ["analyze"]
    assert jobs[0].args == (str(audio.id),)
    assert jobs[0].kwargs == {}
    events = await _analysis_events(db_session, user_id=actor.user_id, audio_id=audio.id)
    assert events[0].status == "dispatched"

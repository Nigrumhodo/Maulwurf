"""I-S2-AN-10 (A2.10): the text outbox waits for verified cleanup; other types do not.

The S2.5 transaction stores the transcript, its segments and the disabled
``index_requested``/``analyze_requested`` rows. This suite proves that the API
dispatcher does not publish them before the cleanup gate opens, that it publishes
them once the reconciler enables them, and that Calendar, notification and deletion
events neither wait for the audio cleanup nor block the text events.

Assertions are scoped to this suite's rows and ``outbox:<id>`` jobs: the migrated
database is shared with the rest of the integration session, and earlier tests that
commit through the API can leave unrelated pending events behind.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from arq.connections import ArqRedis
from arq.jobs import JobDef
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OutboxEvent, Transcript
from app.workers.dispatcher import dispatch_pending
from tests.integration import actors
from tests.integration.test_dispatcher import redis

pytestmark = pytest.mark.integration

_ = redis


def _non_gated_event(
    actor: actors.Actor,
    audio_id: uuid.UUID,
    type_: str,
    *,
    status: str = "pending",
) -> OutboxEvent:
    return OutboxEvent(
        user_id=actor.user_id,
        type=type_,
        resource_type="audio",
        resource_id=audio_id,
        resource_version=1,
        payload={},
        enabled=True,
        blocked_reason=None,
        status=status,
    )


def _job_id(event: OutboxEvent) -> str:
    return f"outbox:{event.id}"


async def _jobs_for(
    pool: ArqRedis, queue: str, events: list[OutboxEvent]
) -> dict[str, JobDef]:
    wanted = {_job_id(event) for event in events}
    jobs: dict[str, JobDef] = {}
    for job in await pool.queued_jobs(queue_name=queue):
        if job.job_id is not None and job.job_id in wanted:
            jobs[job.job_id] = job
    return jobs


async def _transcripts(db: AsyncSession, audio_id: uuid.UUID) -> list[Transcript]:
    return list(
        (
            await db.scalars(
                select(Transcript).where(Transcript.audio_id == audio_id)
            )
        ).all()
    )


async def test_commit_state_keeps_text_outbox_disabled(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    """I-S2-AN-10: the committed text does not reach Redis while cleanup is pending."""
    pool, queue = redis
    actor = await actors.make_actor(db_session)
    audio, attempt, _transcript, _segments, events = (
        await actors.make_committed_cleanup_pending(
            db_session, actor, required_stages=["index", "analyze"]
        )
    )

    await dispatch_pending(db_session, pool, queue_name=queue)

    assert await _jobs_for(pool, queue, list(events.values())) == {}
    assert attempt.status == "transcript_committed_cleanup_pending"
    assert attempt.cleanup_status == "pending"
    for event in events.values():
        assert event.type in {"index_requested", "analyze_requested"}
        assert event.enabled is False
        assert event.blocked_reason == "cleanup_pending"
        assert event.status == "pending"
        assert event.published_at is None


async def test_enabled_gated_event_still_waits_for_verified_cleanup(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    """I-S2-AN-10: an enabled event still needs the latest verified cleanup."""
    pool, queue = redis
    actor = await actors.make_actor(db_session)
    _audio, attempt, _transcript, _segments, events = (
        await actors.make_committed_cleanup_pending(
            db_session, actor, required_stages=["index", "analyze"]
        )
    )
    # A faulty writer could flip `enabled` without the reconciler's cleanup proof: the
    # table CHECK allows enabled+NULL. The dispatcher must still fail closed.
    for event in events.values():
        event.enabled = True
        event.blocked_reason = None
    await db_session.flush()
    assert attempt.cleanup_status == "pending"

    await dispatch_pending(db_session, pool, queue_name=queue)

    assert await _jobs_for(pool, queue, list(events.values())) == {}
    for event in events.values():
        assert event.status == "pending"
        assert event.published_at is None


async def test_verified_cleanup_enables_and_publishes_once(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    """I-S2-AN-10: only the verified-cleanup path releases both text events."""
    pool, queue = redis
    actor = await actors.make_actor(db_session)
    audio, attempt, _transcript, _segments, events = (
        await actors.make_committed_cleanup_pending(
            db_session, actor, required_stages=["index", "analyze"]
        )
    )
    # Same effects as persistence.enable_text_outbox after verified cleanup (S2.5/S2.6).
    attempt.status = "succeeded"
    attempt.cleanup_status = "verified"
    attempt.cleanup_verified_at = datetime.now(UTC)
    for event in events.values():
        event.enabled = True
        event.blocked_reason = None
    await db_session.flush()

    await dispatch_pending(db_session, pool, queue_name=queue)
    await dispatch_pending(db_session, pool, queue_name=queue)

    jobs = await _jobs_for(pool, queue, list(events.values()))
    assert set(jobs) == {_job_id(event) for event in events.values()}
    assert jobs[_job_id(events["index"])].function == "index"
    assert jobs[_job_id(events["index"])].args == (str(audio.id), 1)
    assert jobs[_job_id(events["analyze"])].function == "analyze"
    assert jobs[_job_id(events["analyze"])].args == (str(audio.id),)
    assert all(not job.kwargs for job in jobs.values())
    assert all(event.status == "dispatched" for event in events.values())
    assert all(event.published_at is not None for event in events.values())


async def test_non_gated_outbox_never_waits_for_cleanup_nor_blocks_it(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    """I-S2-AN-10: Calendar/notification/deletion events are outside the audio gate."""
    pool, queue = redis
    actor = await actors.make_actor(db_session)
    _audio, attempt, _transcript, _segments, events = (
        await actors.make_committed_cleanup_pending(
            db_session, actor, required_stages=["index", "analyze"]
        )
    )
    calendar = _non_gated_event(actor, events["index"].resource_id, "sync_task_event")
    notification = _non_gated_event(actor, events["index"].resource_id, "notify")
    deletion = _non_gated_event(
        actor, events["index"].resource_id, "delete_event", status="failed"
    )
    db_session.add_all([calendar, notification, deletion])
    await db_session.flush()

    await dispatch_pending(db_session, pool, queue_name=queue)

    tracked = [*events.values(), calendar, notification, deletion]
    assert await _jobs_for(pool, queue, tracked) == {}
    assert calendar.status == "pending"
    assert notification.status == "pending"
    assert deletion.status == "failed"
    assert all(event.enabled is False for event in events.values())

    attempt.status = "succeeded"
    attempt.cleanup_status = "verified"
    attempt.cleanup_verified_at = datetime.now(UTC)
    for event in events.values():
        event.enabled = True
        event.blocked_reason = None
    await db_session.flush()

    await dispatch_pending(db_session, pool, queue_name=queue)

    jobs = await _jobs_for(pool, queue, tracked)
    assert set(jobs) == {_job_id(event) for event in events.values()}
    assert calendar.status == "pending"
    assert notification.status == "pending"
    assert deletion.status == "failed"
    assert {event.status for event in events.values()} == {"dispatched"}


async def test_post_commit_recovery_does_not_retranscribe_or_publish_early(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    """I-S2-AN-10: a dead post-commit owner keeps one transcript and no early jobs."""
    pool, queue = redis
    actor = await actors.make_actor(db_session)
    audio, attempt, transcript, _segments, events = (
        await actors.make_committed_cleanup_pending(
            db_session, actor, required_stages=["index", "analyze"]
        )
    )
    attempt.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.flush()

    await dispatch_pending(db_session, pool, queue_name=queue)

    assert await _jobs_for(pool, queue, list(events.values())) == {}
    assert len(await _transcripts(db_session, audio.id)) == 1
    assert (await _transcripts(db_session, audio.id))[0].version == transcript.version
    assert all(event.enabled is False for event in events.values())

    # Only the reconciler's verified-cleanup transaction enables the events (S2.7).
    attempt.status = "succeeded"
    attempt.cleanup_status = "verified"
    attempt.cleanup_verified_at = datetime.now(UTC)
    for event in events.values():
        event.enabled = True
        event.blocked_reason = None
    await db_session.flush()

    await dispatch_pending(db_session, pool, queue_name=queue)

    assert set(await _jobs_for(pool, queue, list(events.values()))) == {
        _job_id(event) for event in events.values()
    }
    stored = await _transcripts(db_session, audio.id)
    assert len(stored) == 1
    assert stored[0].version == transcript.version


async def test_only_latest_attempt_opens_the_gate(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    """I-S2-AN-10: an older verified attempt cannot release a newer pending commit."""
    pool, queue = redis
    actor = await actors.make_actor(db_session)
    audio = await actors.make_audio(db_session, actor)
    older = await actors.make_attempt(db_session, actor, audio=audio)
    older.status = "succeeded"
    older.cleanup_status = "verified"
    older.created_at = datetime.now(UTC) - timedelta(hours=1)
    await db_session.flush()
    _audio, attempt, _transcript, _segments, events = (
        await actors.make_committed_cleanup_pending(
            db_session, actor, audio=audio, required_stages=["index"]
        )
    )

    await dispatch_pending(db_session, pool, queue_name=queue)

    assert await _jobs_for(pool, queue, [events["index"]]) == {}
    assert events["index"].enabled is False

    attempt.status = "succeeded"
    attempt.cleanup_status = "verified"
    attempt.cleanup_verified_at = datetime.now(UTC)
    events["index"].enabled = True
    events["index"].blocked_reason = None
    await db_session.flush()

    await dispatch_pending(db_session, pool, queue_name=queue)

    assert set(await _jobs_for(pool, queue, [events["index"]])) == {
        _job_id(events["index"])
    }


@pytest.mark.parametrize(
    ("resource_type", "resource_version"),
    [("audio", None), ("task", 1)],
)
async def test_malformed_gated_event_is_skipped_not_published(
    db_session: AsyncSession,
    redis: tuple[ArqRedis, str],
    resource_type: str,
    resource_version: int | None,
) -> None:
    """I-S2-AN-10: a malformed gated event is terminal, never an index without version."""
    pool, queue = redis
    actor = await actors.make_actor(db_session)
    _audio, attempt, _transcript, _segments, events = (
        await actors.make_committed_cleanup_pending(
            db_session, actor, required_stages=["index"]
        )
    )
    attempt.status = "succeeded"
    attempt.cleanup_status = "verified"
    attempt.cleanup_verified_at = datetime.now(UTC)
    event = events["index"]
    event.enabled = True
    event.blocked_reason = None
    event.resource_type = resource_type
    event.resource_version = resource_version
    await db_session.flush()

    await dispatch_pending(db_session, pool, queue_name=queue)

    assert event.status == "skipped"
    assert event.published_at is None
    assert await _jobs_for(pool, queue, [event]) == {}

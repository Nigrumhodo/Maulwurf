"""I-S2-AN-10 (A2.10): the text outbox waits for verified cleanup; other types do not.

The S2.5 transaction stores the transcript, its segments and the disabled
``index_requested``/``analyze_requested`` rows. This suite proves that the API
dispatcher does not publish them before the cleanup gate opens, publishes them once
after the reconciler enables them, and that notification and deletion rows
do not delay the gated events.

Layering: the dispatcher has two redundant cleanup checks, the ``LATEST_CLEANUP``
filter in the selection query and ``services.outbox.decide()``. These integration
cases exercise the query/DB layer end to end; ``decide()`` is covered separately by
U-S1-JF-04 (``tests/unit/test_outbox_policy.py``). A mutation of either layer alone
leaves this suite green, which is the intended defense in depth.

Overlap: ``test_cleanup_gate.py`` (I-S1-SG-05) starts from an expired lease and one
event; this file starts from the S2.5 committed state, covers both text events, the
non-gated types, latest-attempt selection, missing attempts and malformed rows.

Assertions are scoped to this suite's rows and ``outbox:<id>`` jobs. The migrated
database is shared with the rest of the integration session, so an autouse fixture
hides committed rows from earlier API tests inside the test's rolled-back transaction.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from arq.connections import ArqRedis
from arq.jobs import JobDef
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OutboxEvent, Transcript
from app.services.outbox import Outcome, decide
from app.workers.dispatcher import dispatch_pending
from tests.integration import actors

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def _hide_committed_outbox(db_session: AsyncSession) -> None:
    """Make the global dispatcher see only rows created by this test.

    Earlier API tests commit real audios and outbox rows into the shared migrated
    database. This delete runs inside ``db_session``'s transaction, which is rolled
    back at teardown, so no committed row is actually removed.
    """
    await db_session.execute(delete(OutboxEvent))
    await db_session.flush()


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

    report = await dispatch_pending(db_session, pool, queue_name=queue)

    assert sum(report.values()) == 0
    assert report[Outcome.PUBLISH] == 0
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

    report = await dispatch_pending(db_session, pool, queue_name=queue)

    assert sum(report.values()) == 0
    assert await _jobs_for(pool, queue, list(events.values())) == {}
    for event in events.values():
        assert event.status == "pending"
        assert event.published_at is None


async def test_verified_cleanup_enables_and_publishes_once(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    """I-S2-AN-10: verified cleanup releases each text event exactly once."""
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

    first = await dispatch_pending(db_session, pool, queue_name=queue)
    assert first[Outcome.PUBLISH] == len(events)
    assert sum(first.values()) == len(events)

    # ARQ hides a requeue behind the same `job_id`; the dispatcher counters are the
    # proof that the second pass wrote no status and no `published_at`.
    second = await dispatch_pending(db_session, pool, queue_name=queue)
    assert sum(second.values()) == 0

    jobs = await _jobs_for(pool, queue, list(events.values()))
    assert set(jobs) == {_job_id(event) for event in events.values()}
    assert jobs[_job_id(events["index"])].function == "index"
    assert jobs[_job_id(events["index"])].args == (str(audio.id), 1)
    assert jobs[_job_id(events["analyze"])].function == "analyze"
    assert jobs[_job_id(events["analyze"])].args == (str(audio.id),)
    assert all(not job.kwargs for job in jobs.values())
    assert all(event.status == "dispatched" for event in events.values())
    assert all(event.published_at is not None for event in events.values())


async def test_non_gated_events_do_not_block_text_events(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    """I-S2-AN-10: notification/deletion rows never delay the gated events.

    Those types are in ``KNOWN_WITHOUT_CONSUMER``, so the dispatcher removes them from
    the batch before the gate is evaluated. This test proves non-interference and
    pins the ``decide()`` semantics that the query bypasses.
    """
    pool, queue = redis
    actor = await actors.make_actor(db_session)
    _audio, attempt, _transcript, _segments, events = (
        await actors.make_committed_cleanup_pending(
            db_session, actor, required_stages=["index", "analyze"]
        )
    )
    cancelled = _non_gated_event(actor, events["index"].resource_id, "task_cancelled")
    notification = _non_gated_event(actor, events["index"].resource_id, "notify")
    deletion = _non_gated_event(
        actor, events["index"].resource_id, "delete_event", status="failed"
    )
    db_session.add_all([cancelled, notification, deletion])
    await db_session.flush()

    for event_type in ("task_cancelled", "notify", "delete_event"):
        decision = decide(
            event_type, enabled=True, blocked_reason=None, cleanup_status="pending"
        )
        assert decision.outcome is Outcome.NO_CONSUMER

    report = await dispatch_pending(db_session, pool, queue_name=queue)

    tracked = [*events.values(), cancelled, notification, deletion]
    assert report[Outcome.PUBLISH] == 0
    assert await _jobs_for(pool, queue, tracked) == {}
    assert (cancelled.status, notification.status, deletion.status) == (
        "pending",
        "pending",
        "failed",
    )
    assert all(event.enabled is False for event in events.values())

    attempt.status = "succeeded"
    attempt.cleanup_status = "verified"
    attempt.cleanup_verified_at = datetime.now(UTC)
    for event in events.values():
        event.enabled = True
        event.blocked_reason = None
    await db_session.flush()

    report = await dispatch_pending(db_session, pool, queue_name=queue)

    assert report[Outcome.PUBLISH] == len(events)
    jobs = await _jobs_for(pool, queue, tracked)
    assert set(jobs) == {_job_id(event) for event in events.values()}
    assert (cancelled.status, notification.status, deletion.status) == (
        "pending",
        "pending",
        "failed",
    )
    assert {event.status for event in events.values()} == {"dispatched"}


async def test_expired_post_commit_lease_keeps_text_events_disabled(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    """I-S2-AN-10: an expired lease keeps the committed events disabled.

    This checks the API-side state only. The reconciler's retranscription-free
    recovery of the committed attempt is I-S2-SG-08 in ``apps/ingest``.
    """
    pool, queue = redis
    actor = await actors.make_actor(db_session)
    audio, attempt, transcript, _segments, events = (
        await actors.make_committed_cleanup_pending(
            db_session, actor, required_stages=["index", "analyze"]
        )
    )
    attempt.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.flush()

    report = await dispatch_pending(db_session, pool, queue_name=queue)

    assert sum(report.values()) == 0
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

    report = await dispatch_pending(db_session, pool, queue_name=queue)

    assert report[Outcome.PUBLISH] == len(events)
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

    blocked = await dispatch_pending(db_session, pool, queue_name=queue)

    assert sum(blocked.values()) == 0
    assert await _jobs_for(pool, queue, [events["index"]]) == {}
    assert events["index"].enabled is False

    attempt.status = "succeeded"
    attempt.cleanup_status = "verified"
    attempt.cleanup_verified_at = datetime.now(UTC)
    events["index"].enabled = True
    events["index"].blocked_reason = None
    await db_session.flush()

    released = await dispatch_pending(db_session, pool, queue_name=queue)

    assert released[Outcome.PUBLISH] == 1
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

    report = await dispatch_pending(db_session, pool, queue_name=queue)

    assert report[Outcome.UNKNOWN_TYPE] == 1
    assert event.status == "skipped"
    assert event.published_at is None
    assert await _jobs_for(pool, queue, [event]) == {}


@pytest.mark.parametrize("cleanup_status", ["pending", "failed"])
async def test_gate_fails_closed_without_verified_cleanup(
    db_session: AsyncSession,
    redis: tuple[ArqRedis, str],
    cleanup_status: str,
) -> None:
    """I-S2-AN-10: only ``verified`` opens the gate; ``failed`` behaves like pending."""
    pool, queue = redis
    actor = await actors.make_actor(db_session)
    _audio, attempt, _transcript, _segments, events = (
        await actors.make_committed_cleanup_pending(
            db_session, actor, required_stages=["index"]
        )
    )
    attempt.cleanup_status = cleanup_status
    events["index"].enabled = True
    events["index"].blocked_reason = None
    await db_session.flush()

    report = await dispatch_pending(db_session, pool, queue_name=queue)

    assert sum(report.values()) == 0
    assert report[Outcome.PUBLISH] == 0
    assert events["index"].status == "pending"
    assert events["index"].published_at is None
    assert await _jobs_for(pool, queue, [events["index"]]) == {}


async def test_gate_fails_closed_without_any_attempt(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    """I-S2-AN-10: a missing attempt leaves ``LATEST_CLEANUP`` NULL; no publish."""
    pool, queue = redis
    actor = await actors.make_actor(db_session)
    audio = await actors.make_audio(db_session, actor)
    event = OutboxEvent(
        user_id=actor.user_id,
        type="index_requested",
        resource_type="audio",
        resource_id=audio.id,
        resource_version=1,
        payload={},
        enabled=True,
        blocked_reason=None,
    )
    db_session.add(event)
    await db_session.flush()

    report = await dispatch_pending(db_session, pool, queue_name=queue)

    assert sum(report.values()) == 0
    assert report[Outcome.PUBLISH] == 0
    assert event.status == "pending"
    assert event.published_at is None
    assert await _jobs_for(pool, queue, [event]) == {}

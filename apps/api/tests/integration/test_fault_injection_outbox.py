"""I-S2-JF-05 (J2.7), API side: held index/analyze events are never published.

The ingest fault cases leave the text outbox in shapes the dispatcher must refuse: the
owner died after the commit with cleanup ``pending``, cleanup was recorded ``failed``, or a
successor now owns the attempt while cleanup is still ``pending``. Each shape keeps the
gated events disabled and blocked with ``cleanup_pending``. The dispatcher must publish
none of them across repeated cycles. A positive control proves the same events publish
once the gate opens, so the negative result is not an artifact of the harness.
"""

from datetime import UTC, datetime, timedelta

import pytest
from arq.connections import ArqRedis
from arq.jobs import JobDef
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OutboxEvent
from app.services.outbox import Outcome
from app.workers.dispatcher import dispatch_pending
from tests.integration import actors

pytestmark = pytest.mark.integration

_STAGES = ["index", "analyze"]


@pytest.fixture(autouse=True)
async def _hide_committed_outbox(db_session: AsyncSession) -> None:
    """Keep the global dispatcher blind to outbox rows committed by earlier API tests."""
    await db_session.execute(delete(OutboxEvent))
    await db_session.flush()


def _job_id(event: OutboxEvent) -> str:
    return f"outbox:{event.id}"


async def _jobs_for(
    pool: ArqRedis, queue: str, events: list[OutboxEvent],
) -> dict[str, JobDef]:
    wanted = {_job_id(event) for event in events}
    return {
        job.job_id: job
        for job in await pool.queued_jobs(queue_name=queue)
        if job.job_id is not None and job.job_id in wanted
    }


@pytest.mark.parametrize(
    ("cleanup_status", "lease_expired"),
    [
        ("pending", True),  # owner died after commit; reconciler has not run yet
        ("pending", False),  # a successor owns the attempt while cleanup is pending
        ("failed", True),  # cleanup was recorded failed, then the owner died
        ("failed", False),  # cleanup failed under a live lease
    ],
)
async def test_held_text_events_are_never_published(
    db_session: AsyncSession,
    redis: tuple[ArqRedis, str],
    cleanup_status: str,
    lease_expired: bool,
) -> None:
    """I-S2-JF-05(d): the gate holds index/analyze across cycles, whatever the lease says."""
    pool, queue = redis
    actor = await actors.make_actor(db_session)
    _audio, attempt, _transcript, _segments, events = (
        await actors.make_committed_cleanup_pending(
            db_session, actor, required_stages=_STAGES,
        )
    )
    attempt.cleanup_status = cleanup_status
    if lease_expired:
        attempt.lease_expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await db_session.flush()

    for _cycle in range(2):
        report = await dispatch_pending(db_session, pool, queue_name=queue)
        assert report[Outcome.PUBLISH] == 0

    assert await _jobs_for(pool, queue, list(events.values())) == {}
    assert set(events) == set(_STAGES)
    for event in events.values():
        assert (event.enabled, event.blocked_reason) == (False, "cleanup_pending")
        assert event.status == "pending"
        assert event.published_at is None


async def test_same_events_publish_once_after_verified_cleanup_and_enable(
    db_session: AsyncSession, redis: tuple[ArqRedis, str],
) -> None:
    """I-S2-JF-05(d) control: after verification and enablement, both events publish once."""
    pool, queue = redis
    actor = await actors.make_actor(db_session)
    _audio, attempt, _transcript, _segments, events = (
        await actors.make_committed_cleanup_pending(
            db_session, actor, required_stages=_STAGES,
        )
    )
    attempt.cleanup_status = "verified"
    attempt.status = "succeeded"
    for event in events.values():
        event.enabled = True
        event.blocked_reason = None
    await db_session.flush()

    first = await dispatch_pending(db_session, pool, queue_name=queue)
    second = await dispatch_pending(db_session, pool, queue_name=queue)

    assert first[Outcome.PUBLISH] == 2
    assert sum(second.values()) == 0
    assert set(await _jobs_for(pool, queue, list(events.values()))) == {
        _job_id(event) for event in events.values()
    }
    for event in events.values():
        assert event.status == "dispatched"
        assert event.published_at is not None

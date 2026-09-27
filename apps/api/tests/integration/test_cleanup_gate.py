"""I-S1-SG-05 (S1.B6, G1): sin cleanup verificado la outbox de índice no se publica.

El efecto durable de un lease vencido es `cleanup_status=pending`. El dispatcher no
cambia esa fila ni encola el job.
"""
from datetime import UTC, datetime, timedelta

import pytest
from arq.connections import ArqRedis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import IngestionAttempt
from app.services.outbox import Outcome
from app.workers.dispatcher import dispatch_pending
from tests.integration.test_dispatcher import _audio, _event, redis

pytestmark = pytest.mark.integration

_ = redis


async def test_expired_lease_cleanup_pending_blocks_outbox(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    """I-S1-SG-05: lease vencido, cleanup pending, index_requested sigue pending."""
    pool, queue = redis
    user, audio = await _audio(db_session, "pending")
    attempt = (
        await db_session.execute(
            select(IngestionAttempt).where(IngestionAttempt.audio_id == audio.id)
        )
    ).scalar_one()
    attempt.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    attempt.status = "transcript_committed_cleanup_pending"
    event = _event(user, audio, "index_requested")
    db_session.add(event)
    await db_session.flush()

    report = await dispatch_pending(db_session, pool, queue_name=queue)

    assert sum(report.values()) == 0
    assert report[Outcome.PUBLISH] == 0
    assert await pool.queued_jobs(queue_name=queue) == []
    assert event.status == "pending" and event.published_at is None
    assert attempt.cleanup_status == "pending"
    assert attempt.cleanup_verified_at is None

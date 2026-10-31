"""Selection and state changes for the J2.4 reconciler: lost dispatches and dead index runs.

Two failures leave work stranded without an error anywhere:

- An outbox event is `dispatched` but its job never ran to a terminal run. `dispatch_pending`
  only picks `pending` rows, so nothing publishes it again.
- An `index` run is `running` after its worker died. `claim_run` takes over an expired lease
  only when another delivery arrives, and nothing else enqueues one.

Both selections lock the rows they return (`FOR UPDATE SKIP LOCKED`), so two reconciler cycles
never act on the same row at once. Redis state is checked by the caller (`workers/reconciler.py`).
"""

from datetime import datetime, timedelta
from typing import Final

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OutboxEvent, ProcessingRun
from app.services.index_job import LEASE, STAGE_INDEX
from app.services.runs import RUN_CONFIG_VERSION

# Longer than a live `index` job can last (`LEASE`), so a dispatch still in flight is never
# republished. Only `index_requested` is recovered: `analyze` has no run ledger, and republishing a
# job that does nothing would loop forever.
DISPATCH_GRACE: Final = LEASE + timedelta(minutes=15)
LEASE_EXPIRED_CODE: Final = "lease_expired"


async def select_lost_dispatches(
    db: AsyncSession, *, now: datetime, limit: int
) -> list[OutboxEvent]:
    """Index events dispatched before the grace margin with no terminal run for their version.

    A `failed` run with `lease_expired` is recoverable: its recovery job can itself be lost
    when Redis restarts. The retry budget and `OutboxEvent.max_attempts` bound the loop.
    """
    terminal_run = exists().where(
        ProcessingRun.user_id == OutboxEvent.user_id,
        ProcessingRun.audio_id == OutboxEvent.resource_id,
        ProcessingRun.stage == STAGE_INDEX,
        ProcessingRun.transcript_version == OutboxEvent.resource_version,
        ProcessingRun.config_version == RUN_CONFIG_VERSION,
        or_(
            ProcessingRun.status.in_(("succeeded", "cancelled")),
            and_(
                ProcessingRun.status == "failed",
                ProcessingRun.error_code.is_distinct_from(LEASE_EXPIRED_CODE),
            ),
        ),
    )
    statement = (
        select(OutboxEvent)
        .where(
            OutboxEvent.type == "index_requested",
            OutboxEvent.status == "dispatched",
            OutboxEvent.published_at < now - DISPATCH_GRACE,
            OutboxEvent.resource_version.is_not(None),
            # A no-op delivery (e.g. D3-Text still open) creates no processing run. Without
            # this bound, the event would be republished every grace period forever.
            OutboxEvent.attempts < OutboxEvent.max_attempts,
            ~terminal_run,
        )
        .order_by(OutboxEvent.published_at, OutboxEvent.id)
        .limit(limit)
        .with_for_update(skip_locked=True, of=OutboxEvent)
    )
    return list((await db.execute(statement)).scalars().all())


def reset_to_pending(event: OutboxEvent) -> None:
    """Hands the event back to `dispatch_pending`, which reruns the gate and revalidation."""
    event.status = "pending"
    event.published_at = None


async def select_expired_index_runs(
    db: AsyncSession, *, now: datetime, limit: int
) -> list[ProcessingRun]:
    """Index runs still `running` after their lease: the worker that claimed them is gone."""
    statement = (
        select(ProcessingRun)
        .where(
            ProcessingRun.stage == STAGE_INDEX,
            ProcessingRun.config_version == RUN_CONFIG_VERSION,
            ProcessingRun.status == "running",
            ProcessingRun.started_at < now - LEASE,
        )
        .order_by(ProcessingRun.started_at, ProcessingRun.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return list((await db.execute(statement)).scalars().all())


def mark_lease_expired(run: ProcessingRun, *, now: datetime) -> None:
    """Closes a dead run so that `claim_run` accepts its next delivery."""
    run.status = "failed"
    run.error_code = LEASE_EXPIRED_CODE
    run.finished_at = now

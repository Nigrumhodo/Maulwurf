"""Selection and state changes for the J2.4 reconciler: lost dispatches and dead index runs.

Two failures leave work stranded without an error anywhere:

- An outbox event is `dispatched` but its job never ran to a terminal run. `dispatch_pending`
  only picks `pending` rows, so nothing publishes it again.
- An `index` run is `running` after its worker died. `claim_run` takes over an expired lease
  only when another delivery arrives, and nothing else enqueues one.

J2.11 keeps Redis I/O out of the row-lock window: callers select candidates in one short
transaction, commit, inspect/enqueue in Redis without holding any lock, and then apply the
change in a second short transaction. Each state change is a compare-and-set against the
snapshot read during selection (`status`, `published_at`, `attempts` for outbox events and
`status`, `started_at`, `attempt_count`, `fencing_token` for runs), so overlapping cycles
cannot apply the same change twice. The selections still use `FOR UPDATE SKIP LOCKED` so
concurrent cycles prefer disjoint batches; the CAS is what makes the post-commit window safe.
Closing an expired run is also a compare-and-set on its fencing token and `running` status, so
it can never overwrite a claim that was renewed in between.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from sqlalchemy import and_, exists, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OutboxEvent, ProcessingRun
from app.services.index_job import LEASE, STAGE_INDEX
from app.services.runs import RUN_CONFIG_VERSION

# Longer than a live `index` job can last (`LEASE`), so a dispatch still in flight is never
# republished. Only `index_requested` is recovered: `analyze` has no run ledger, and republishing a
# job that does nothing would loop forever.
DISPATCH_GRACE: Final = LEASE + timedelta(minutes=15)
LEASE_EXPIRED_CODE: Final = "lease_expired"


@dataclass(frozen=True)
class LostDispatch:
    """Snapshot of a lost outbox event, safe to carry across transactions and Redis calls."""

    id: uuid.UUID
    status: str
    published_at: datetime | None
    attempts: int


@dataclass(frozen=True)
class ExpiredRun:
    """Snapshot of an expired index run, safe to carry across transactions and Redis calls."""

    id: uuid.UUID
    audio_id: uuid.UUID
    transcript_version: int
    status: str
    started_at: datetime | None
    attempt_count: int
    fencing_token: int


async def select_lost_dispatches(
    db: AsyncSession, *, now: datetime, limit: int
) -> list[LostDispatch]:
    """Index events dispatched before the grace margin with no terminal run for their version.

    A `failed` run with `lease_expired` is recoverable: its recovery job can itself be lost
    when Redis restarts. The retry budget and `OutboxEvent.max_attempts` bound the loop.
    Returns ids plus the fields the later compare-and-set needs; no ORM state is kept.
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
        select(
            OutboxEvent.id,
            OutboxEvent.status,
            OutboxEvent.published_at,
            OutboxEvent.attempts,
        )
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
    rows = (await db.execute(statement)).all()
    return [
        LostDispatch(
            id=row.id,
            status=row.status,
            published_at=row.published_at,
            attempts=row.attempts,
        )
        for row in rows
    ]


def reset_to_pending(event: OutboxEvent) -> None:
    """Hands the event back to `dispatch_pending`, which reruns the gate and revalidation."""
    event.status = "pending"
    event.published_at = None


async def reset_to_pending_if_unchanged(db: AsyncSession, candidate: LostDispatch) -> bool:
    """Applies the reset only while the row still matches the selected snapshot (J2.11)."""
    event = await db.scalar(
        select(OutboxEvent)
        .where(OutboxEvent.id == candidate.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if event is None:
        return False
    if (
        event.status != candidate.status
        or event.published_at != candidate.published_at
        or event.attempts != candidate.attempts
    ):
        return False
    reset_to_pending(event)
    return True


async def select_expired_index_runs(
    db: AsyncSession, *, now: datetime, limit: int
) -> list[ExpiredRun]:
    """Index runs still `running` after their lease: the worker that claimed them is gone.

    Returns ids plus the fields the later compare-and-set needs; no ORM state is kept.
    """
    statement = (
        select(
            ProcessingRun.id,
            ProcessingRun.audio_id,
            ProcessingRun.transcript_version,
            ProcessingRun.status,
            ProcessingRun.started_at,
            ProcessingRun.attempt_count,
            ProcessingRun.fencing_token,
        )
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
    rows = (await db.execute(statement)).all()
    return [
        ExpiredRun(
            id=row.id,
            audio_id=row.audio_id,
            transcript_version=row.transcript_version,
            status=row.status,
            started_at=row.started_at,
            attempt_count=row.attempt_count,
            fencing_token=row.fencing_token,
        )
        for row in rows
    ]


async def close_expired_run(
    db: AsyncSession, *, run_id: uuid.UUID, fencing_token: int, now: datetime
) -> bool:
    """Closes an expired run with a compare-and-set on `(id, token, status='running')`.

    The token is the one the selection read. If a claim renewed the run in between, the token
    changed and the update affects no row: the reconciler must not close a live lease.
    """
    result = await db.execute(
        update(ProcessingRun)
        .where(
            ProcessingRun.id == run_id,
            ProcessingRun.fencing_token == fencing_token,
            ProcessingRun.status == "running",
            ProcessingRun.started_at < now - LEASE,
        )
        .values(status="failed", error_code=LEASE_EXPIRED_CODE, finished_at=now)
        .returning(ProcessingRun.id)
        .execution_options(synchronize_session=False)
    )
    return result.scalar_one_or_none() is not None

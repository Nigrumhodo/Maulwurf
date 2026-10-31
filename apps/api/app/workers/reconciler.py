"""Reconciler cycle (J2.4, J2.11): repairs work that a dead worker or a lost Redis left behind.

ARQ runs `reconcile_once` as a cron on the scheduler (`workers/arq_app.py`). It has four steps,
each bounded by `BATCH_SIZE`. A failing step is logged by its exception type and the other
steps still run.

- Lost dispatches go back to `pending`, so `dispatch_pending` republishes them through the usual
  gate and revalidation.
- Expired `index` runs are closed as `lease_expired`, and re-enqueued while the retry budget
  lasts. `claim_run` accepts the closed row, so a duplicate delivery is absorbed by the row.
- Reservations past `upload_expires_at` that never reached an ingest lease are cancelled
  (A2.14), freeing their quota and slot; `ingest` keeps owning rows that do have a lease.
- Orphan `building` generations and surplus `retired` generations are deleted.

J2.11: the two steps that touch Redis select candidates in a short transaction, commit, do the
`redis.exists`/`enqueue_job` work with no row lock held, and then apply the change in a second
short transaction guarded by a compare-and-set. A slow or blocked Redis therefore cannot keep
`FOR UPDATE` locks (or an idle transaction) open, and overlapping cycles apply each change once.

The reconciler never reads audio and never retranscribes. Logs carry IDs and counts only.
"""

import logging
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Final

from arq.connections import ArqRedis
from arq.constants import in_progress_key_prefix, job_key_prefix, result_key_prefix
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.audios import expire_stale_reservations
from app.services.generation_cleanup import (
    RETIRED_GENERATIONS_KEPT,
    discard_orphan_building,
    discard_surplus_retired,
    select_orphan_building,
    select_surplus_retired,
)
from app.services.reconciliation import (
    ExpiredRun,
    mark_lease_expired_if_unchanged,
    reset_to_pending_if_unchanged,
    select_expired_index_runs,
    select_lost_dispatches,
)
from app.workers.index_job import MAX_TRIES

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
BATCH_SIZE: Final = 100


def outbox_job_keys(event_id: uuid.UUID) -> tuple[str, ...]:
    """Redis keys that exist while an outbox job is queued, running, or has a kept result."""
    job_id = f"outbox:{event_id}"
    return tuple(
        f"{prefix}{job_id}"
        for prefix in (job_key_prefix, in_progress_key_prefix, result_key_prefix)
    )


def has_retry_budget(attempt_count: int) -> bool:
    """Whether another delivery of a run that already made `attempt_count` claims is allowed."""
    return attempt_count < MAX_TRIES


async def reconcile_once(
    sessions: SessionFactory,
    redis: ArqRedis,
    *,
    queue_name: str,
    now: datetime | None = None,
) -> Counter[str]:
    """One reconciler cycle. Returns how many rows each step changed."""
    clock = now or datetime.now(UTC)
    report: Counter[str] = Counter()
    report["lost_dispatches"] = await _guarded(
        "lost_dispatches", _republish_lost(sessions, redis, clock)
    )
    report["expired_runs"] = await _guarded(
        "expired_runs", _recover_expired(sessions, redis, queue_name, clock)
    )
    report["expired_reservations"] = await _guarded(
        "expired_reservations", _expire_reservations(sessions, clock)
    )
    report["orphan_builds"] = await _guarded(
        "orphan_builds", _discard_orphans(sessions, clock)
    )
    report["retired_generations"] = await _guarded(
        "retired_generations", _discard_retired(sessions)
    )
    if any(report.values()):
        logger.info("reconciler.cycle %s", dict(report))
    return report


async def _guarded(step: str, work: Awaitable[int]) -> int:
    try:
        return await work
    except Exception as exc:
        # Only the type: messages can carry the DSN or provider content.
        logger.warning("reconciler.step_failed step=%s error=%s", step, type(exc).__name__)
        return 0


async def _republish_lost(sessions: SessionFactory, redis: ArqRedis, now: datetime) -> int:
    async with sessions() as db:
        candidates = await select_lost_dispatches(db, now=now, limit=BATCH_SIZE)
        # Release the selection locks before any Redis call (J2.11).
        await db.commit()

    lost = []
    for event in candidates:
        if not await _any_key_exists(redis, outbox_job_keys(event.id)):
            lost.append(event)
    if not lost:
        return 0

    reset = 0
    async with sessions() as db:
        for event in lost:
            # One savepoint per row: a deadlock/timeout on one candidate must not roll back
            # the resets already applied to the rest of the batch.
            try:
                async with db.begin_nested():
                    applied = await reset_to_pending_if_unchanged(db, event)
            except Exception as exc:
                logger.warning(
                    "reconciler.reset_cas_failed event_id=%s error=%s",
                    event.id,
                    type(exc).__name__,
                )
                continue
            if applied:
                reset += 1
                logger.info("reconciler.dispatch_reset event_id=%s", event.id)
        await db.commit()
    return reset


async def _recover_expired(
    sessions: SessionFactory, redis: ArqRedis, queue_name: str, now: datetime
) -> int:
    async with sessions() as db:
        candidates = await select_expired_index_runs(db, now=now, limit=BATCH_SIZE)
        # Release the selection locks before any Redis call (J2.11).
        await db.commit()

    # Close first, commit, then enqueue. A job enqueued before the close commit would point
    # at a run that can roll back to `running`; a Redis failure after the close is recovered
    # by the lost-dispatch step instead.
    closed: list[tuple[ExpiredRun, bool]] = []
    async with sessions() as db:
        for run in candidates:
            # Keep the per-row isolation: one CAS failure must not revert the runs already
            # closed in this batch.
            try:
                async with db.begin_nested():
                    applied = await mark_lease_expired_if_unchanged(db, run, now=now)
            except Exception as exc:
                logger.warning(
                    "reconciler.lease_cas_failed run_id=%s error=%s",
                    run.id,
                    type(exc).__name__,
                )
                continue
            if applied:
                closed.append((run, has_retry_budget(run.attempt_count)))
                logger.info("reconciler.lease_expired run_id=%s", run.id)
        await db.commit()

    for run, requeue in closed:
        if not requeue:
            continue
        try:
            await _reenqueue_index(
                redis, queue_name, run.id, run.audio_id, run.transcript_version,
                run.attempt_count,
            )
        except Exception as exc:
            logger.warning("reconciler.requeue_failed run_id=%s error=%s",
                           run.id, type(exc).__name__)
    return len(closed)


async def _reenqueue_index(
    redis: ArqRedis,
    queue_name: str,
    run_id: uuid.UUID,
    audio_id: uuid.UUID,
    version: int,
    attempt_count: int,
) -> None:
    # `_job_id` per (run, attempt): a second cycle that sees the same closed row gets None back.
    await redis.enqueue_job(
        "index",
        str(audio_id),
        version,
        _job_id=f"reconcile-index:{run_id}:{attempt_count}",
        _queue_name=queue_name,
    )


async def _expire_reservations(sessions: SessionFactory, now: datetime) -> int:
    async with sessions() as db:
        expired = await expire_stale_reservations(db, now=now, limit=BATCH_SIZE)
        if expired:
            logger.info("reconciler.reservations_expired count=%s", expired)
        return expired


async def _discard_orphans(sessions: SessionFactory, now: datetime) -> int:
    discarded = 0
    async with sessions() as db:
        for user_id, version in await select_orphan_building(db, now=now, limit=BATCH_SIZE):
            try:
                async with db.begin_nested():
                    done = await discard_orphan_building(
                        db, user_id=user_id, version=version, now=now
                    )
            except Exception as exc:
                logger.warning("reconciler.orphan_failed user_id=%s error=%s",
                               user_id, type(exc).__name__)
                continue
            if done:
                discarded += 1
                logger.info("reconciler.orphan_discarded user_id=%s", user_id)
    return discarded


async def _discard_retired(sessions: SessionFactory) -> int:
    discarded = 0
    async with sessions() as db:
        candidates = await select_surplus_retired(
            db, keep=RETIRED_GENERATIONS_KEPT, limit=BATCH_SIZE
        )
        for user_id, version in candidates:
            try:
                async with db.begin_nested():
                    done = await discard_surplus_retired(db, user_id=user_id, version=version)
            except Exception as exc:
                logger.warning("reconciler.retired_failed user_id=%s error=%s",
                               user_id, type(exc).__name__)
                continue
            if done:
                discarded += 1
    return discarded


async def _any_key_exists(redis: ArqRedis, keys: tuple[str, ...]) -> bool:
    return bool(await redis.exists(*keys))

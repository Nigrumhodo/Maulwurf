"""J3.3: resume Calendar work after a (re)connection without resurrecting old state.

Contract (DISENO §3.8, plan S3 J3.3/A3.7): when the Calendar integration returns, only the
CURRENT version of a task that is waiting for the Calendar (`calendar_sync_status='blocked'`)
moves back to `pending`, and exactly one `sync_task_event` outbox row is created or reused.
The rules this module enforces:

- **Only blocked, still-confirmed tasks.** `completed`/`dismissed` tasks (the tombstones of
  the task lifecycle) and tasks that were never confirmed are untouched.
- **The active task row is the current version.** `tasks.version` is the only version a
  worker may sync; pending revisions live in `task_revisions` and are deliberately NOT read
  or activated here, so reconnecting never promotes an obsolete proposal.
- **Create or reuse the outbox.** A pending/dispatched `sync_task_event` for the same
  `(task, version)` is reused; otherwise a new event is inserted with a deterministic
  per-cycle dedupe key (`reconnect:<version>:<cycle>`). The `UPDATE ... RETURNING` locks
  the task rows, so two concurrent reconnects serialize instead of duplicating work.
- **Atomic with the connection.** No commit happens here: the callback commits the tokens,
  the calendar ID, the task transitions and the outbox in one transaction.

A3.1's `tasks` and `task_revisions` tables are not in this revision of `develop`. The
module checks for the table at runtime: before A3.1 lands it reports `available=False` and
changes nothing, so the connection still succeeds without touching a table that does not
exist yet. The statement follows the DISENO §2.8 shape and is exercised by I-S3-JF-06
against that shape.

Validation status of the raw SQL (2026-10-31): the `ON CONFLICT` target
`(type, resource_id, resource_version, dedupe_key)` matches the real unique constraint
`uq_outbox_events_type_resource_id_resource_version_dedupe_key` created by migration
`0001` (`NULLS NOT DISTINCT`), and an integration case inserts twice against the real
`outbox_events` table to prove PostgreSQL infers it. `outbox_events` exists in `develop`,
so that half no longer needs A3.1. The `UPDATE tasks ... RETURNING` statement and the
`tasks.version`/`calendar_sync_status` columns still have no real table: they remain
pending until A3.1's migration lands, and no integration case may claim otherwise.
"""

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import func, inspect, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OutboxEvent

logger = logging.getLogger(__name__)

SYNC_TASK_EVENT = "sync_task_event"
# Only a confirmed task owes a Calendar event; completed/dismissed rows are tombstones.
_RESUMABLE_TASKS = text(
    """
    UPDATE tasks
       SET calendar_sync_status = 'pending',
           updated_at = now()
     WHERE user_id = :user_id
       AND calendar_sync_status = 'blocked'
       AND status = 'confirmed'
    RETURNING id, version
    """
)


@dataclass(frozen=True)
class ResumedTask:
    """Flat evidence of one task moved back to `pending` (IDs and versions only)."""

    task_id: uuid.UUID
    version: int
    outbox_event_id: uuid.UUID
    outbox_created: bool


@dataclass(frozen=True)
class ResumeReport:
    """Outcome of one resumption pass. `available=False` means A3.1's table is absent."""

    available: bool
    tasks: tuple[ResumedTask, ...]


async def tasks_table_available(db: AsyncSession) -> bool:
    """Whether A3.1's `tasks` table exists in the connected schema.

    The check resolves through the same `search_path` as the raw statements below, so a
    deployment that moves the tables does not get a false positive against another schema.
    """
    connection = await db.connection()
    return bool(
        await connection.run_sync(lambda sync: inspect(sync).has_table("tasks"))
    )


async def _active_event(
    db: AsyncSession, *, user_id: uuid.UUID, task_id: uuid.UUID, version: int
) -> uuid.UUID | None:
    found: uuid.UUID | None = await db.scalar(
        select(OutboxEvent.id)
        .where(
            OutboxEvent.user_id == user_id,
            OutboxEvent.type == SYNC_TASK_EVENT,
            OutboxEvent.resource_id == task_id,
            OutboxEvent.resource_version == version,
            OutboxEvent.status.in_(("pending", "dispatched")),
        )
        .order_by(OutboxEvent.created_at.desc(), OutboxEvent.id.desc())
        .limit(1)
    )
    return found


async def _cycle(db: AsyncSession, *, user_id: uuid.UUID, task_id: uuid.UUID) -> int:
    count = await db.scalar(
        select(func.count())
        .select_from(OutboxEvent)
        .where(
            OutboxEvent.user_id == user_id,
            OutboxEvent.type == SYNC_TASK_EVENT,
            OutboxEvent.resource_id == task_id,
        )
    )
    return int(count or 0)


async def _ensure_outbox(
    db: AsyncSession, *, user_id: uuid.UUID, task_id: uuid.UUID, version: int
) -> tuple[uuid.UUID, bool]:
    """Reuse the live sync event for this task version, or insert the next cycle."""
    existing = await _active_event(db, user_id=user_id, task_id=task_id, version=version)
    if existing is not None:
        return existing, False
    cycle = await _cycle(db, user_id=user_id, task_id=task_id)
    # The conflict target mirrors migration 0001's unique constraint exactly; PostgreSQL
    # infers a `NULLS NOT DISTINCT` unique index for the same column set (validated by
    # `test_sync_task_event_conflict_target_matches_the_real_unique_index`). `dedupe_key`
    # is always set here, so the NULLS NOT DISTINCT semantics are not relied upon.
    inserted = await db.execute(
        pg_insert(OutboxEvent)
        .values(
            user_id=user_id,
            type=SYNC_TASK_EVENT,
            resource_type="task",
            resource_id=task_id,
            resource_version=version,
            payload={},
            # Not cleanup-gated: the CHECK requires enabled events without a blocked reason.
            enabled=True,
            blocked_reason=None,
            dedupe_key=f"reconnect:{version}:{cycle}",
        )
        .on_conflict_do_nothing(
            index_elements=["type", "resource_id", "resource_version", "dedupe_key"]
        )
        .returning(OutboxEvent.id)
    )
    event_id = inserted.scalar_one_or_none()
    if event_id is not None:
        return event_id, True
    # The unique key swallowed the insert: another writer created this cycle between the
    # reads. Reuse whatever live event exists instead of failing the whole connection.
    existing = await _active_event(db, user_id=user_id, task_id=task_id, version=version)
    if existing is not None:
        return existing, False
    raise RuntimeError("sync_task_event could not be created or reused")


async def resume_blocked_tasks(db: AsyncSession, *, user_id: uuid.UUID) -> ResumeReport:
    """Move current blocked tasks to `pending` and (re)create their sync outbox."""
    if not await tasks_table_available(db):
        logger.info("calendar.resume_skipped reason=tasks_table_missing")
        return ResumeReport(available=False, tasks=())
    rows = (await db.execute(_RESUMABLE_TASKS, {"user_id": user_id})).all()
    resumed: list[ResumedTask] = []
    for task_id, version in rows:
        event_id, created = await _ensure_outbox(
            db, user_id=user_id, task_id=task_id, version=int(version)
        )
        resumed.append(
            ResumedTask(
                task_id=task_id,
                version=int(version),
                outbox_event_id=event_id,
                outbox_created=created,
            )
        )
    logger.info("calendar.resumed count=%d", len(resumed))
    return ResumeReport(available=True, tasks=tuple(resumed))

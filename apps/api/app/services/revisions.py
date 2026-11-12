"""Pending task revisions shared by PATCH and reanalysis (A3.5).

A revision is a proposal, never an active write. The active `tasks` row only changes when
`confirm` promotes exactly one pending row (A3.4). Both entry points go through this
module so these invariants cannot drift:

- `base_task_version` is the version observed under the task row lock, so a plan computed
  before a concurrent PATCH/confirm still produces a revision the user can promote;
- at most one `pending_confirmation` row exists per task (the partial unique index
  `task_revisions_one_pending` is the database guard);
- a replay of the same analysis run is idempotent and never resurrects a discarded row;
- a terminal task (`completed`/`dismissed`) never gains a revision that `confirm` would
  reject;
- an equivalent pending proposal is reused instead of discarded and recreated, so a
  double analysis does not churn a revision the user is already reviewing.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Task, TaskRevision

PENDING_REVISION_STATUS = "pending_confirmation"
DISCARDED_REVISION_STATUS = "discarded"
TERMINAL_TASK_STATUSES = frozenset({"completed", "dismissed"})
REANALYSIS_REVISION = "reanalysis"
EDIT_REVISION = "edit_of_confirmed"


def same_proposed_fields(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """The proposal is unchanged: there is nothing new for the user to compare."""
    return left == right


async def discard_pending_revisions(
    db: AsyncSession, user_id: uuid.UUID, task_id: uuid.UUID
) -> None:
    """Retire the task's pending proposal before a replacement is inserted.

    The flush is required before the caller inserts: the partial unique index only sees
    the retired row once the `UPDATE` has reached PostgreSQL.
    """
    await db.execute(
        update(TaskRevision)
        .where(
            TaskRevision.user_id == user_id,
            TaskRevision.task_id == task_id,
            TaskRevision.status == PENDING_REVISION_STATUS,
        )
        .values(status=DISCARDED_REVISION_STATUS)
        .execution_options(synchronize_session="fetch")
    )
    await db.flush()


async def record_pending_revision(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    task_id: uuid.UUID,
    proposed_fields: dict[str, Any],
    revision_type: str,
    run_id: uuid.UUID | None = None,
    evidence_validated: bool = True,
) -> TaskRevision | None:
    """Record one pending proposal without touching the active task columns.

    The task row is locked and `base_task_version` comes from that locked row, never from
    the caller's snapshot. Returns the reused row, the new row, or `None` when the task
    disappeared, is terminal, or the same analysis run already published a revision.
    """
    task = (
        await db.execute(
            select(Task)
            .where(Task.user_id == user_id, Task.id == task_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if task is None or task.status in TERMINAL_TASK_STATUSES:
        return None
    base_version = task.version

    if revision_type == REANALYSIS_REVISION and run_id is not None:
        replay = (
            await db.execute(
                select(TaskRevision)
                .where(
                    TaskRevision.user_id == user_id,
                    TaskRevision.task_id == task_id,
                    TaskRevision.revision_type == revision_type,
                    TaskRevision.run_id == run_id,
                )
                .order_by(TaskRevision.created_at.desc(), TaskRevision.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if replay is not None:
            if replay.status == PENDING_REVISION_STATUS and same_proposed_fields(
                dict(replay.proposed_fields), proposed_fields
            ):
                return replay
            # A replay never resurrects a row the user discarded or promoted.
            return None

    current = (
        await db.execute(
            select(TaskRevision)
            .where(
                TaskRevision.user_id == user_id,
                TaskRevision.task_id == task_id,
                TaskRevision.status == PENDING_REVISION_STATUS,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if (
        current is not None
        and current.base_task_version == base_version
        and same_proposed_fields(dict(current.proposed_fields), proposed_fields)
    ):
        return current

    await discard_pending_revisions(db, user_id, task_id)
    revision = TaskRevision(
        user_id=user_id,
        task_id=task_id,
        run_id=run_id,
        base_task_version=base_version,
        revision_type=revision_type,
        proposed_fields=proposed_fields,
        evidence_validated=evidence_validated,
        status=PENDING_REVISION_STATUS,
    )
    db.add(revision)
    await db.flush()
    return revision

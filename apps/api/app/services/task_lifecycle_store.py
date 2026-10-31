"""PostgreSQL adapter for the J3.6 task lifecycle (complete/dismiss).

The adapter owns the row-level work but **not** the transaction: the injected
`SessionFactory` must yield a session that commits when the context exits successfully and
rolls back when an exception escapes (production uses `app.core.db.session_scope`). It locks
the task row **scoped to the caller's tenant**, validates the transition, bumps `version`,
writes the new status/timestamp and cancels the task's not-yet-sent reminders, so a
completed task can never receive a stale Gmail reminder and a Calendar job queued for the
previous version can no longer win its compare-and-set. A task of another user is
indistinguishable from a missing one (`NOT_FOUND`). Google Calendar is not touched here:
the linked event is kept, and removing it requires the explicit remote-operation flow (S4).

A3.1 owns the real ``tasks`` table and S4 owns ``notifications``. Until their migrations
land, `TASKS` comes from `task_event_store` and `NOTIFICATIONS` mirrors the frozen §2.9
projection; both are created by the integration suite when the real tables are absent.
"""

import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Column,
    DateTime,
    MetaData,
    Table,
    Text,
    func,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.task_event_store import TASKS
from app.services.task_lifecycle import (
    LifecycleOutcome,
    LifecycleTransition,
    ensure_task_transition,
)

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

# `complete` and `dismiss` are the only commands this adapter writes.
LIFECYCLE_TARGETS = frozenset({"completed", "dismissed"})

NOTIFICATIONS = Table(
    "notifications",
    MetaData(),
    Column("id", PgUUID(as_uuid=True), primary_key=True),
    Column("user_id", PgUUID(as_uuid=True), nullable=False),
    Column("task_id", PgUUID(as_uuid=True)),
    Column("type", Text, nullable=False),
    Column("scheduled_at", DateTime(timezone=True), nullable=False),
    Column("status", Text, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)


class SqlTaskLifecycleStore:
    """One locked transaction per command, mirroring `services.task_lifecycle`.

    `sessions` must be a factory whose context manager commits on a clean exit and rolls
    back on error; the adapter never calls `commit()` itself.
    """

    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def transition(
        self,
        user_id: uuid.UUID,
        task_id: uuid.UUID,
        *,
        expected_version: int,
        target: str,
        now: datetime,
    ) -> LifecycleTransition:
        if target not in LIFECYCLE_TARGETS:
            raise ValueError(f"unsupported lifecycle target: {target!r}")
        async with self._sessions() as db:
            row = (
                await db.execute(
                    select(
                        TASKS.c.user_id,
                        TASKS.c.version,
                        TASKS.c.status,
                        TASKS.c.calendar_event_id,
                    )
                    .where(TASKS.c.id == task_id, TASKS.c.user_id == user_id)
                    .with_for_update()
                )
            ).mappings().one_or_none()
            if row is None:
                return LifecycleTransition(outcome=LifecycleOutcome.NOT_FOUND)
            if row["version"] != expected_version:
                return LifecycleTransition(
                    outcome=LifecycleOutcome.VERSION_CONFLICT,
                    status=row["status"],
                    current_version=row["version"],
                    calendar_event_id=row["calendar_event_id"],
                )
            if row["status"] == target:
                # Repeating the command with the current version is a no-op for the task, but
                # leftovers are still cancelled; the stored version does not change.
                cancelled = await self._cancel_pending(
                    db, user_id=row["user_id"], task_id=task_id
                )
                return LifecycleTransition(
                    outcome=LifecycleOutcome.ALREADY,
                    status=target,
                    current_version=row["version"],
                    calendar_event_id=row["calendar_event_id"],
                    cancelled_reminders=cancelled,
                )
            ensure_task_transition(row["status"], target)
            values: dict[str, Any] = {
                "status": target,
                # Bump the token so queued Calendar jobs and stale client commands lose.
                "version": TASKS.c.version + 1,
                "updated_at": func.now(),
                f"{target}_at": now,
            }
            await db.execute(
                update(TASKS)
                .where(TASKS.c.id == task_id, TASKS.c.version == expected_version)
                .values(**values)
            )
            cancelled = await self._cancel_pending(
                db, user_id=row["user_id"], task_id=task_id
            )
            return LifecycleTransition(
                outcome=LifecycleOutcome.UPDATED,
                status=target,
                current_version=expected_version + 1,
                calendar_event_id=row["calendar_event_id"],
                cancelled_reminders=cancelled,
            )

    @staticmethod
    async def _cancel_pending(
        db: AsyncSession, *, user_id: uuid.UUID, task_id: uuid.UUID
    ) -> int:
        """Cancel every reminder of this task that has not been sent yet.

        `pending` is the only cancellable state: `sending` may already be leaving through
        Gmail (`delivery_unknown` when the lease expires), and `sent`/`failed` are history.
        An occurrence that is already due but was never dispatched is cancelled too: a
        completed task must not notify later. The `user_id` filter keeps the write inside
        the tenant even when the caller already scoped the task.
        """
        result = await db.execute(
            update(NOTIFICATIONS)
            .where(
                NOTIFICATIONS.c.user_id == user_id,
                NOTIFICATIONS.c.task_id == task_id,
                NOTIFICATIONS.c.status == "pending",
            )
            .values(status="cancelled", updated_at=func.now())
            .returning(NOTIFICATIONS.c.id)
        )
        return len(result.all())

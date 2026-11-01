"""A4.3 persistence adapter: cancel obsolete pending occurrences in the caller's transaction.

The decision is **not** duplicated here. The adapter loads the candidate pending rows of one
user (optionally one task), delegates the stale-generation/stale-preference rule to
:func:`app.services.notifications.obsolete_pending`, and writes ``cancelled`` only for the
selected ids. Keeping one authority means the scheduler and the preference endpoint cannot
drift into two different definitions of "obsolete".

The caller owns the transaction: a preference change or a `due_version` bump and this
cancellation commit together, so a crash cannot leave stale rows armed for a later burst.
The final ``status = 'pending'`` guard resolves the only race that matters: if the scheduler
claimed a row between the read and the write, it is already in flight and is left alone
(``sending`` must never be rewritten by a preference change). A second call is idempotent
because the rows it selected are already terminal.
"""

import uuid
from types import SimpleNamespace
from typing import cast

from sqlalchemy import Table, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification
from app.services.notifications import obsolete_pending

NOTIFICATIONS = cast(Table, Notification.__table__)


class NotificationCancellationStore:
    """Apply the A4.3 cancellation to PostgreSQL; the caller commits the transaction."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def cancel_obsolete(
        self,
        user_id: uuid.UUID,
        *,
        task_id: uuid.UUID | None = None,
        due_version: int | None = None,
        enabled_types: frozenset[str] | None = None,
    ) -> int:
        """Cancel the user's stale pending occurrences and return how many were written.

        ``task_id``/``due_version`` retire the previous reminder generation of one task;
        ``enabled_types`` (the complete set from ``enabled_preference_types``) retires the
        reminders and digests a preference change disabled. At least one rule is required.
        """
        if due_version is None and enabled_types is None:
            raise ValueError("at least one cancellation rule is required")
        if due_version is not None:
            if task_id is None:
                raise ValueError("a due_version rule must be scoped to its task")
            if due_version < 1:
                raise ValueError("due_version must be positive")

        query = select(
            NOTIFICATIONS.c.id,
            NOTIFICATIONS.c.status,
            NOTIFICATIONS.c.type,
            NOTIFICATIONS.c.task_id,
            NOTIFICATIONS.c.due_version,
        ).where(
            NOTIFICATIONS.c.user_id == user_id,
            NOTIFICATIONS.c.status == "pending",
        )
        if task_id is not None:
            query = query.where(NOTIFICATIONS.c.task_id == task_id)
        rows = (await self._db.execute(query)).mappings().all()

        candidates = [SimpleNamespace(**dict(row)) for row in rows]
        obsolete = obsolete_pending(
            candidates,
            task_id=task_id,
            due_version=due_version,
            enabled_types=enabled_types,
        )
        if not obsolete:
            return 0
        ids = [row.id for row in obsolete]
        result = await self._db.execute(
            update(NOTIFICATIONS)
            .where(
                NOTIFICATIONS.c.user_id == user_id,
                NOTIFICATIONS.c.id.in_(ids),
                NOTIFICATIONS.c.status == "pending",
            )
            .values(status="cancelled", updated_at=func.now())
            .returning(NOTIFICATIONS.c.id)
        )
        return len(result.all())

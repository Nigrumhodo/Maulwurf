"""PostgreSQL adapter for the idempotent Calendar sync (J3.4).

A3.1 owns the full ``tasks`` table. This module declares only the projection J3.4 reads and
the two compare-and-set writes it needs, so the job can be implemented and tested against the
frozen §2.8 contract before that migration lands. ``TASKS`` is a standalone Core table (not an
ORM model and not part of the Alembic metadata), and every statement is explicit.
"""

import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Integer,
    MetaData,
    Table,
    Text,
    func,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.task_event_sync import TaskSnapshot

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

TASKS = Table(
    "tasks",
    MetaData(),
    Column("id", PgUUID(as_uuid=True), primary_key=True),
    Column("user_id", PgUUID(as_uuid=True), nullable=False),
    Column("version", Integer, nullable=False),
    Column("calendar_sync_status", Text, nullable=False),
    Column("calendar_id", Text),
    Column("calendar_event_id", Text),
    Column("titulo", Text, nullable=False),
    Column("detalle", Text),
    Column("timezone", Text, nullable=False),
    Column("all_day", Boolean, nullable=False),
    Column("event_start_at", DateTime(timezone=True)),
    Column("event_end_at", DateTime(timezone=True)),
    Column("event_start_date", Date),
    Column("event_end_date_exclusive", Date),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)


class SqlTaskEventStore:
    """Task reads and status writes, each in its own transaction."""

    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def load(self, task_id: uuid.UUID) -> TaskSnapshot | None:
        async with self._sessions() as db:
            row = (
                await db.execute(select(TASKS).where(TASKS.c.id == task_id))
            ).mappings().one_or_none()
        if row is None:
            return None
        return TaskSnapshot(
            task_id=row["id"],
            user_id=row["user_id"],
            version=row["version"],
            calendar_sync_status=row["calendar_sync_status"],
            calendar_id=row["calendar_id"],
            calendar_event_id=row["calendar_event_id"],
            title=row["titulo"],
            detail=row["detalle"],
            timezone=row["timezone"],
            all_day=row["all_day"],
            event_start_at=row["event_start_at"],
            event_end_at=row["event_end_at"],
            event_start_date=row["event_start_date"],
            event_end_date_exclusive=row["event_end_date_exclusive"],
        )

    async def mark_synced(
        self, task_id: uuid.UUID, version: int, event_id: str
    ) -> bool:
        return await self._set_status(
            task_id, version, "synced", calendar_event_id=event_id
        )

    async def mark_failed(self, task_id: uuid.UUID, version: int) -> bool:
        return await self._set_status(task_id, version, "failed")

    async def mark_blocked(self, task_id: uuid.UUID, version: int) -> bool:
        return await self._set_status(task_id, version, "blocked")

    async def _set_status(
        self,
        task_id: uuid.UUID,
        version: int,
        status: str,
        *,
        calendar_event_id: str | None = None,
    ) -> bool:
        values: dict[str, Any] = {
            "calendar_sync_status": status,
            "updated_at": func.now(),
        }
        if calendar_event_id is not None:
            values["calendar_event_id"] = calendar_event_id
        async with self._sessions() as db:
            result = await db.execute(
                update(TASKS)
                .where(TASKS.c.id == task_id, TASKS.c.version == version)
                .values(**values)
                .returning(TASKS.c.id)
            )
            return result.first() is not None

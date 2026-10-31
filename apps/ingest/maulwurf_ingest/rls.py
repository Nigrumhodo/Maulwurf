"""Runtime role and per-transaction tenant binding for ingest (A2.17).

Ingest authenticates with the shared DSN owner, then every transaction downgrades itself
to ``maulwurf_ingest`` (NOLOGIN, no ``BYPASSRLS``) with ``SET LOCAL ROLE`` and sets
``app.user_id`` with ``set_config(..., true)``. Both are transaction-local, so a connection
returned to the pool never carries the previous attempt's tenant.

The only explicit cross-tenant path is the attempt queue: ``ingestion_attempts`` has a
permissive policy for this role because the admission latch, the post-commit reconciler
and the stranded sweep operate across users by design. That is an explicit accepted risk
recorded in ADR-0006 (blast radius and review trigger); everything else (audios,
transcripts, segments, chunks, the outbox) stays bound to the attempt's ``user_id``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg

DATABASE_ROLE = "maulwurf_ingest"

# Production always passes a pool; the API integration sweep passes the test transaction's
# connection so it sees uncommitted rows, so both shapes are accepted.
type Executor = asyncpg.Pool | asyncpg.Connection


async def bind_transaction(
    connection: asyncpg.Connection, user_id: uuid.UUID | None
) -> None:
    """Apply the ingest role and, when known, the tenant to the open transaction."""
    await connection.execute(f'SET LOCAL ROLE "{DATABASE_ROLE}"')
    if user_id is not None:
        await connection.execute(
            "SELECT set_config('app.user_id', $1, true)", str(user_id)
        )


@asynccontextmanager
async def transaction(
    executor: Executor, user_id: uuid.UUID | None
) -> AsyncIterator[asyncpg.Connection]:
    """One transaction as the constrained ingest role, optionally tenant-bound."""
    if isinstance(executor, asyncpg.Connection):
        connection = executor
        if connection.is_in_transaction():
            # A caller-owned transaction (tests): SET LOCAL lasts until that transaction
            # ends, so the role and tenant are restored before returning to the caller.
            await bind_transaction(connection, user_id)
            try:
                yield connection
            except BaseException:
                raise
            else:
                await connection.execute("RESET ROLE")
                await connection.execute("RESET app.user_id")
            return
        async with connection.transaction():
            await bind_transaction(connection, user_id)
            yield connection
        return
    async with executor.acquire() as connection, connection.transaction():
        await bind_transaction(connection, user_id)
        yield connection


async def execute(
    executor: Executor, user_id: uuid.UUID | None, query: str, *args: object
) -> str:
    async with transaction(executor, user_id) as connection:
        status: str = await connection.execute(query, *args)
        return status


async def fetchrow(
    executor: Executor, user_id: uuid.UUID | None, query: str, *args: object
) -> asyncpg.Record | None:
    async with transaction(executor, user_id) as connection:
        return await connection.fetchrow(query, *args)


async def fetch(
    executor: Executor, user_id: uuid.UUID | None, query: str, *args: object
) -> list[asyncpg.Record]:
    async with transaction(executor, user_id) as connection:
        rows: list[asyncpg.Record] = await connection.fetch(query, *args)
        return rows


async def fetchval(
    executor: Executor, user_id: uuid.UUID | None, query: str, *args: object
) -> object:
    async with transaction(executor, user_id) as connection:
        return await connection.fetchval(query, *args)

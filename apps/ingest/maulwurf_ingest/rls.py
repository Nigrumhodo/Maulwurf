"""Runtime role and per-transaction tenant binding for ingest (A2.17).

Ingest authenticates with the shared DSN owner, then every transaction downgrades itself
to ``maulwurf_ingest`` (NOLOGIN, no ``BYPASSRLS``) with ``SET LOCAL ROLE`` and sets
``app.user_id`` with ``set_config(..., true)``. Both are transaction-local, so a connection
returned to the pool never carries the previous attempt's tenant.

The only explicit cross-tenant path is the attempt queue: ``ingestion_attempts`` has a
permissive policy for this role because the admission latch, the post-commit reconciler
and the stranded sweep operate across users by design. Everything else (audios,
transcripts, segments, chunks, the outbox) stays bound to the attempt's ``user_id``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg

DATABASE_ROLE = "maulwurf_ingest"


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
    pool: asyncpg.Pool, user_id: uuid.UUID | None
) -> AsyncIterator[asyncpg.Connection]:
    """One transaction as the constrained ingest role, optionally tenant-bound."""
    async with pool.acquire() as connection, connection.transaction():
        await bind_transaction(connection, user_id)
        yield connection


async def execute(
    pool: asyncpg.Pool, user_id: uuid.UUID | None, query: str, *args: object
) -> str:
    async with transaction(pool, user_id) as connection:
        status: str = await connection.execute(query, *args)
        return status


async def fetchrow(
    pool: asyncpg.Pool, user_id: uuid.UUID | None, query: str, *args: object
) -> asyncpg.Record | None:
    async with transaction(pool, user_id) as connection:
        return await connection.fetchrow(query, *args)


async def fetch(
    pool: asyncpg.Pool, user_id: uuid.UUID | None, query: str, *args: object
) -> list[asyncpg.Record]:
    async with transaction(pool, user_id) as connection:
        rows: list[asyncpg.Record] = await connection.fetch(query, *args)
        return rows


async def fetchval(
    pool: asyncpg.Pool, user_id: uuid.UUID | None, query: str, *args: object
) -> object:
    async with transaction(pool, user_id) as connection:
        return await connection.fetchval(query, *args)

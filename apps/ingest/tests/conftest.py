"""Fixtures de integración de ingest con PostgreSQL real.

Crea una BD `maulwurf_ingest_test_*` en el servidor de `MAULWURF_DATABASE_URL`, la migra
con el Alembic de `apps/api` (dueño único del esquema) y la borra al terminar. Sin esa
variable, los tests que la piden se saltan.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import asyncpg
import pytest

API_ROOT = Path(__file__).resolve().parents[2] / "api"


def _plain_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _with_database(url: str, name: str) -> str:
    base, _, _ = url.rpartition("/")
    return f"{base}/{name}"


async def _admin(url: str, sql: str) -> None:
    conn = await asyncpg.connect(_plain_dsn(_with_database(url, "postgres")), timeout=5)
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def migrated_database_url() -> Iterator[str]:
    url = os.environ.get("MAULWURF_DATABASE_URL")
    if not url:
        pytest.skip("MAULWURF_DATABASE_URL sin definir: no hay PostgreSQL para integración")
    name = f"maulwurf_ingest_test_{uuid.uuid4().hex[:12]}"
    asyncio.run(_admin(url, f'CREATE DATABASE "{name}"'))
    test_url = _with_database(url, name)
    try:
        env = {**os.environ, "MAULWURF_DATABASE_URL": test_url}
        migrated = subprocess.run(  # noqa: S603 — argv fija, sin shell
            ["uv", "run", "--frozen", "alembic", "upgrade", "head"],  # noqa: S607
            cwd=API_ROOT, env=env, capture_output=True, text=True, timeout=300, check=False,
        )
        assert migrated.returncode == 0, migrated.stderr[-1000:]
        yield test_url
    finally:
        asyncio.run(_admin(url, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@pytest.fixture
async def pool(migrated_database_url: str) -> AsyncIterator[asyncpg.Pool]:
    created = await asyncpg.create_pool(_plain_dsn(migrated_database_url), min_size=1, max_size=4)
    try:
        yield created
    finally:
        await created.close()

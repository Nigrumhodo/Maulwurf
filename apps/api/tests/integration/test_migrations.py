"""I-S1-AN-04 (A1.7): Alembic desde vacío, ida y vuelta y ejecución concurrente.

Usa la BD aislada de la sesión: cada test la baja a `base` (esquema vacío) y la deja en
`head` al terminar, así el resto de la suite sigue encontrando el esquema completo.
"""
import asyncio
import os
import subprocess
import sys
import time
from collections.abc import Iterator

import pytest
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from tests.integration.conftest import API_ROOT, alembic_config

pytestmark = pytest.mark.integration

S1_TABLES = {
    "audios",
    "google_credentials",
    "ingestion_attempts",
    "outbox_events",
    "sessions",
    "subjects",
    "users",
}


def _head(url: str) -> str:
    head = ScriptDirectory.from_config(alembic_config(url)).get_current_head()
    assert head is not None
    return head


def _state(url: str) -> tuple[set[str], list[str]]:
    """Tablas de la app y filas de `alembic_version`."""

    async def read() -> tuple[set[str], list[str]]:
        engine = create_async_engine(url)
        async with engine.connect() as conn:
            tables = set(
                (
                    await conn.execute(
                        text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                    )
                ).scalars()
            )
            versions: list[str] = []
            if "alembic_version" in tables:
                result = await conn.execute(text("SELECT version_num FROM alembic_version"))
                versions = list(result.scalars())
        await engine.dispose()
        return tables - {"alembic_version"}, versions

    return asyncio.run(read())


@pytest.fixture
def empty_schema(migrated_database_url: str) -> Iterator[str]:
    """La BD de la sesión en `base`; al terminar vuelve a `head` pase lo que pase."""
    cfg = alembic_config(migrated_database_url)
    command.downgrade(cfg, "base")
    try:
        yield migrated_database_url
    finally:
        command.upgrade(cfg, "head")


def test_upgrade_from_empty_reaches_head(empty_schema: str) -> None:
    assert _state(empty_schema) == (set(), [])

    command.upgrade(alembic_config(empty_schema), "head")

    assert _state(empty_schema) == (S1_TABLES, [_head(empty_schema)])


def test_downgrade_and_upgrade_again(empty_schema: str) -> None:
    cfg = alembic_config(empty_schema)
    command.upgrade(cfg, "head")

    command.downgrade(cfg, "-1")
    assert _state(empty_schema) == (set(), [])

    command.upgrade(cfg, "head")
    assert _state(empty_schema)[0] == S1_TABLES


def test_upgrade_is_idempotent(empty_schema: str) -> None:
    cfg = alembic_config(empty_schema)
    command.upgrade(cfg, "head")
    command.upgrade(cfg, "head")

    assert _state(empty_schema)[1] == [_head(empty_schema)]


def test_concurrent_upgrades_serialize_without_errors(empty_schema: str) -> None:
    # Varias réplicas de la API migran al arrancar; el advisory lock las serializa. Se usan
    # procesos, no hilos: el `context` de Alembic es global al proceso y dos hilos se lo
    # pisan (así una réplica queda con el lock tomado y las demás esperan para siempre).
    env = {**os.environ, "MAULWURF_DATABASE_URL": empty_schema}
    replicas = [
        subprocess.Popen(  # noqa: S603 - argumentos fijos, sin entrada externa
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=API_ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        for _ in range(4)
    ]
    deadline = time.monotonic() + 90  # presupuesto TOTAL, no 90 s por réplica
    for replica in replicas:
        try:
            _, stderr = replica.communicate(timeout=max(1.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            # Sin matarlas, una réplica colgada retendría el advisory lock y el `finally`
            # del fixture (`upgrade head`) esperaría hasta el lock_timeout.
            for pending in replicas:
                pending.kill()
                pending.communicate()
            raise
        assert replica.returncode == 0, stderr.decode()[-500:]

    assert _state(empty_schema) == (S1_TABLES, [_head(empty_schema)])

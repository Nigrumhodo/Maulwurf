"""Fixtures de integración con PostgreSQL real.

Cada sesión de pytest crea una base de datos aislada en el servidor de
`MAULWURF_DATABASE_URL`, la migra con Alembic desde vacío y la elimina al terminar, así
las pruebas no dependen ni ensucian la BD de desarrollo.
"""
import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from alembic import command
from app.core.config import settings

API_ROOT = Path(__file__).resolve().parents[2]


async def _admin(sql: str) -> None:
    admin = create_async_engine(
        make_url(str(settings.database_url)).set(database="postgres"),
        isolation_level="AUTOCOMMIT",
    )
    async with admin.connect() as conn:
        await conn.execute(text(sql))
    await admin.dispose()


def alembic_config(url: str) -> Config:
    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@pytest.fixture(scope="session")
def empty_database_url() -> Iterator[str]:
    """URL de una BD recién creada y vacía, eliminada al final de la sesión."""
    name = f"maulwurf_test_{uuid.uuid4().hex[:12]}"
    asyncio.run(_admin(f'CREATE DATABASE "{name}"'))
    try:
        yield make_url(str(settings.database_url)).set(database=name).render_as_string(
            hide_password=False
        )
    finally:
        asyncio.run(_admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@pytest.fixture(scope="session")
def migrated_database_url(empty_database_url: str) -> str:
    command.upgrade(alembic_config(empty_database_url), "head")
    return empty_database_url


@pytest.fixture
async def db_session(migrated_database_url: str) -> AsyncIterator[AsyncSession]:
    """Sesión dentro de una transacción que se revierte: cada test parte limpio."""
    engine = create_async_engine(migrated_database_url)
    async with engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(bind=conn, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            await session.close()
            await trans.rollback()
    await engine.dispose()

"""Entorno de Alembic con engine async; la URL sale de Settings, no de alembic.ini.

Varias réplicas pueden arrancar `alembic upgrade head` a la vez (la API migra al iniciar):
un advisory lock transaccional las serializa, y la que llega tarde encuentra la BD ya en
`head` y no hace nada. El lock se libera con el commit de la migración.
"""
import asyncio
from logging.config import fileConfig

from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from app.core.config import settings
from app.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

# Clave fija del advisory lock de migraciones (cualquier bigint estable sirve).
MIGRATION_LOCK_KEY = 0x4D41554C5746  # "MAULWF"


def _database_url() -> str:
    # Los tests pasan una BD aislada por `sqlalchemy.url`; si no, manda la configuración.
    return config.get_main_option("sqlalchemy.url") or str(settings.database_url)


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        # Tope de espera: si otra réplica se cuelga con el lock, esta falla en vez de
        # bloquear el arranque indefinidamente.
        connection.execute(text("SET LOCAL lock_timeout = '120s'"))
        connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": MIGRATION_LOCK_KEY})
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_database_url(), poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())

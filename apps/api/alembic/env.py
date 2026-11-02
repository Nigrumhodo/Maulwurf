"""Entorno de Alembic con engine async; la URL sale de Settings, no de alembic.ini.

Varias réplicas pueden arrancar `alembic upgrade head` a la vez (la API migra al iniciar):
un advisory lock de sesión las serializa, y la que llega tarde encuentra la BD ya en
`head` y no hace nada. Es de sesión, no transaccional, porque la migración `0019` commitea
en su bloque autocommit (CONCURRENTLY no admite transacción) y el lock debe seguir vivo
hasta que la última revisión quede registrada. Se libera al terminar la cadena.
"""
import asyncio
import contextlib
import time
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
LOCK_TIMEOUT_S = 120.0
LOCK_POLL_INTERVAL_S = 0.5


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


def _acquire_migration_lock(connection: Connection) -> None:
    """Try the session lock without keeping a snapshot while another replica migrates.

    A blocking `pg_advisory_lock()` waits inside a transaction, and `CREATE INDEX
    CONCURRENTLY` waits for every older snapshot: the two would deadlock (PostgreSQL kills
    the waiter). Each attempt is a short statement committed immediately instead, so a
    polling replica never blocks the index build.
    """
    deadline = time.monotonic() + LOCK_TIMEOUT_S
    while True:
        acquired = bool(
            connection.execute(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": MIGRATION_LOCK_KEY}
            ).scalar_one()
        )
        connection.commit()
        if acquired:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"another replica held the migration lock for {LOCK_TIMEOUT_S:.0f}s"
            )
        time.sleep(LOCK_POLL_INTERVAL_S)


def _run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    # Session-scoped on purpose: `0019` commits inside `autocommit_block()` to build its
    # indexes with CONCURRENTLY, which would release a transaction-scoped lock and let a
    # second replica run the same migration.
    connection.execute(text("SET lock_timeout = '120s'"))
    connection.commit()
    _acquire_migration_lock(connection)
    try:
        with context.begin_transaction():
            context.run_migrations()
    finally:
        # Closing the NullPool connection releases a session lock too; unlock explicitly so
        # a future pooled engine cannot leave the next migration waiting.
        with contextlib.suppress(Exception):
            connection.execute(
                text("SELECT pg_advisory_unlock(:key)"), {"key": MIGRATION_LOCK_KEY}
            )


async def run_migrations_online() -> None:
    engine = create_async_engine(_database_url(), poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())

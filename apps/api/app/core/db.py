"""Engine async y sesiones de SQLAlchemy compartidos por API, workers y migraciones (A1.2).

El engine se crea al importar pero no conecta hasta el primer uso, así que importar este
módulo no exige Postgres disponible.

Convención de transacciones: `get_session` NO confirma. Cada handler que escribe llama a
`await db.commit()` antes de devolver la respuesta, para que un fallo de commit sea un error
visible de esa petición y no un efecto tras enviarla. Si el handler lanza, la sesión se
revierte y no persiste nada a medias.

A2.17 (ADR-0006): cada transacción empieza con `SET LOCAL ROLE` a un rol de runtime sin
`BYPASSRLS`. El tenant viaja en un `ContextVar` que la resolución de sesión fija; el
listener de `begin` lo traduce a `SET LOCAL app.user_id`. `SET LOCAL` muere al terminar la
transacción, así que una conexión devuelta al pool no arrastra el usuario anterior.
"""
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from contextvars import ContextVar, Token

from sqlalchemy import event, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

engine: AsyncEngine = create_async_engine(
    str(settings.database_url),
    pool_pre_ping=True,  # descarta conexiones muertas tras reinicios de Postgres
    pool_size=5,
    max_overflow=5,
)

SessionFactory = async_sessionmaker(engine, expire_on_commit=False)

# The tenant of the current asyncio context. It is set only from authenticated backend
# state (the resolved session), never from a payload, a path parameter, or model output.
_current_user: ContextVar[uuid.UUID | None] = ContextVar("maulwurf_user_id", default=None)
# Only the dispatcher/reconciler/index workers set this; the API never does. The policy
# is scoped to the `maulwurf_jobs` role, so it cannot widen the API even if set by mistake.
_cross_tenant: ContextVar[bool] = ContextVar("maulwurf_cross_tenant", default=False)


def bind_user(user_id: uuid.UUID) -> Token[uuid.UUID | None]:
    """Bind the tenant for transactions opened later in this asyncio context."""
    return _current_user.set(user_id)


def reset_user(token: Token[uuid.UUID | None]) -> None:
    _current_user.reset(token)


def clear_user() -> None:
    """Drop the tenant from the context (a worker task may be reused by the next job)."""
    _current_user.set(None)


def bind_cross_tenant() -> Token[bool]:
    """Enable the explicit cross-tenant policy of the jobs role for this context."""
    return _cross_tenant.set(True)


def reset_cross_tenant(token: Token[bool]) -> None:
    _cross_tenant.reset(token)


def clear_cross_tenant() -> None:
    """Drop the explicit cross-tenant switch from the context."""
    _cross_tenant.set(False)


async def set_current_user(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Bind the tenant now, for the open transaction and for the ones that follow.

    `SET LOCAL` is transactional: a later `commit()` ends it, and the begin listener
    re-applies `app.user_id` from the ContextVar on the next transaction.
    """
    bind_user(user_id)
    await db.execute(
        text("SELECT set_config('app.user_id', :user_id, true)"),
        {"user_id": str(user_id)},
    )


async def bind_google_sub(db: AsyncSession, sub: str) -> None:
    """Bind the verified Google `sub` for the `users` bootstrap policies (A2.17).

    First login and the E2E seed create a user before any tenant exists. The narrow
    `auth_user_lookup`/`auth_user_insert` policies admit exactly the row whose `sub` is
    bound here; without it the insert fails closed, and inserting as the owner would
    bypass the barrier the smoke is supposed to exercise.
    """
    await db.execute(
        text("SELECT set_config('app.google_sub', :sub, true)"), {"sub": sub}
    )


@event.listens_for(engine.sync_engine, "begin")
def _apply_tenant_settings(connection: Connection) -> None:
    # Role and UUID are validated (Settings pattern / uuid.UUID), so these literals cannot
    # carry SQL from a request, a transcript, or a provider response.
    connection.exec_driver_sql(f'SET LOCAL ROLE "{settings.database_role}"')
    user_id = _current_user.get()
    if user_id is not None:
        connection.exec_driver_sql(f"SELECT set_config('app.user_id', '{user_id}', true)")
    if _cross_tenant.get():
        connection.exec_driver_sql("SELECT set_config('app.cross_tenant', 'on', true)")


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependencia FastAPI: una sesión por petición, revertida si el handler falla."""
    async with SessionFactory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def session_scope() -> AsyncGenerator[AsyncSession, None]:
    """Para workers y scripts (sin handler HTTP): confirma al salir bien, revierte si falla."""
    async with SessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

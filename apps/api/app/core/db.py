"""Engine async y sesiones de SQLAlchemy compartidos por API, workers y migraciones (A1.2).

El engine se crea al importar pero no conecta hasta el primer uso, así que importar este
módulo no exige Postgres disponible.

Convención de transacciones: `get_session` NO confirma. Cada handler que escribe llama a
`await db.commit()` antes de devolver la respuesta, para que un fallo de commit sea un error
visible de esa petición y no un efecto tras enviarla. Si el handler lanza, la sesión se
revierte y no persiste nada a medias.
"""
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

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

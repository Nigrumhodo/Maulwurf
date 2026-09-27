"""Engine async y sesiones de SQLAlchemy compartidos por API, workers y migraciones (A1.2).

El engine se crea al importar pero no conecta hasta el primer uso, así que importar este
módulo no exige Postgres disponible.
"""
from collections.abc import AsyncGenerator

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


async def get_session() -> AsyncGenerator[AsyncSession]:
    """Dependencia FastAPI: una sesión por petición, cerrada al terminar."""
    async with SessionFactory() as session:
        yield session

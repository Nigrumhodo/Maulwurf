"""A1.2: el engine compartido es async y no conecta al importarse."""
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import db


def test_engine_uses_async_driver() -> None:
    assert db.engine.url.drivername == "postgresql+asyncpg"


async def test_get_session_yields_session_without_connecting() -> None:
    sessions = db.get_session()
    session = await anext(sessions)

    assert isinstance(session, AsyncSession)
    assert not session.in_transaction()
    await sessions.aclose()

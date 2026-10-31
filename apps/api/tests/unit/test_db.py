"""A1.2: el engine compartido es async y no conecta al importarse.

A2.17: el listener de `begin` traduce el contexto de asincronía a `SET LOCAL ROLE` y
`set_config('app.user_id', ..., true)`. La prueba usa un doble de conexión: no hay Postgres
en la suite unitaria, y lo que se fija es el contrato del listener.
"""
import uuid
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import db
from app.core.config import settings


def test_engine_uses_async_driver() -> None:
    assert db.engine.url.drivername == "postgresql+asyncpg"


class _RecordingConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def exec_driver_sql(self, statement: str) -> None:
        self.statements.append(statement)


def _begin() -> _RecordingConnection:
    connection = _RecordingConnection()
    db._apply_tenant_settings(cast(Any, connection))
    return connection


def test_begin_binds_the_constrained_role_without_a_user() -> None:
    connection = _begin()
    assert connection.statements == [f'SET LOCAL ROLE "{settings.database_role}"']


def test_begin_binds_the_tenant_only_while_the_contextvar_is_set() -> None:
    user_id = uuid.uuid4()
    token = db.bind_user(user_id)
    try:
        connection = _begin()
    finally:
        db.reset_user(token)
    assert connection.statements[0].startswith("SET LOCAL ROLE ")
    assert connection.statements[1] == f"SELECT set_config('app.user_id', '{user_id}', true)"

    assert _begin().statements == [f'SET LOCAL ROLE "{settings.database_role}"']


def test_cross_tenant_is_opt_in_and_scoped_to_the_jobs_role() -> None:
    token = db.bind_cross_tenant()
    try:
        connection = _begin()
    finally:
        db.reset_cross_tenant(token)
    assert "SELECT set_config('app.cross_tenant', 'on', true)" in connection.statements
    assert _begin().statements == [f'SET LOCAL ROLE "{settings.database_role}"']


async def test_get_session_yields_session_without_connecting() -> None:
    sessions = db.get_session()
    session = await anext(sessions)

    assert isinstance(session, AsyncSession)
    assert not session.in_transaction()
    await sessions.aclose()


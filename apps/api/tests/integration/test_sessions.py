"""A1.4 contra PostgreSQL real: persistencia de hashes, expiración, revocación y `GET /me`."""
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import COOKIE_NAME
from app.main import app
from app.models import User
from app.services import sessions

pytestmark = pytest.mark.integration


async def _user(db: AsyncSession) -> User:
    user = User(google_sub=f"sub-{datetime.now(UTC).timestamp()}", email="a@example.test")
    db.add(user)
    await db.flush()
    return user


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    async def override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://localhost") as c:
        yield c
    app.dependency_overrides.clear()


async def test_only_hashes_reach_the_database(db_session: AsyncSession) -> None:
    issued = await sessions.create_session(db_session, (await _user(db_session)).id)

    row = (
        await db_session.execute(
            text("SELECT token_hash, csrf_hash FROM sessions WHERE id = :id"),
            {"id": issued.session.id},
        )
    ).one()

    assert issued.cookie_token not in row.token_hash
    assert issued.csrf_token not in row.csrf_hash
    assert len(row.token_hash) == len(row.csrf_hash) == 64


async def test_resolve_rejects_unknown_expired_and_revoked(db_session: AsyncSession) -> None:
    user = await _user(db_session)
    issued = await sessions.create_session(db_session, user.id)

    assert await sessions.resolve_session(db_session, issued.cookie_token) is issued.session
    assert await sessions.resolve_session(db_session, "unknown") is None
    assert await sessions.resolve_session(db_session, None) is None

    issued.session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.flush()
    assert await sessions.resolve_session(db_session, issued.cookie_token) is None

    other = await sessions.create_session(db_session, user.id)
    await sessions.revoke_session(db_session, other.session)
    assert await sessions.resolve_session(db_session, other.cookie_token) is None


async def test_rotation_invalidates_previous_cookie_and_csrf(db_session: AsyncSession) -> None:
    first = await sessions.create_session(db_session, (await _user(db_session)).id)
    second = await sessions.rotate_session(db_session, first.session)

    assert await sessions.resolve_session(db_session, first.cookie_token) is None
    assert await sessions.resolve_session(db_session, second.cookie_token) is second.session
    assert second.csrf_token != first.csrf_token
    assert not sessions.csrf_matches(second.session, second.cookie_token, first.csrf_token)


async def test_get_me_returns_csrf_and_is_not_cacheable(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    issued = await sessions.create_session(db_session, (await _user(db_session)).id)

    first = await client.get("/me", cookies={COOKIE_NAME: issued.cookie_token})
    second = await client.get("/me", cookies={COOKIE_NAME: issued.cookie_token})

    assert first.status_code == 200
    assert first.headers["cache-control"] == "no-store"
    assert first.json()["csrf_token"] == issued.csrf_token
    assert second.json()["csrf_token"] == issued.csrf_token  # no rota por lectura


async def test_get_me_without_valid_session_is_401(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    issued = await sessions.create_session(db_session, (await _user(db_session)).id)
    await sessions.revoke_session(db_session, issued.session)

    for cookies in ({}, {COOKIE_NAME: "forged"}, {COOKIE_NAME: issued.cookie_token}):
        response = await client.get("/me", cookies=cookies)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "auth_required"

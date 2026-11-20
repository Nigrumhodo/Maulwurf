"""Unit tests for GET /integrations/google/connect|reconnect navigation CSRF guard."""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.core import db, deps, redis
from app.core.config import settings
from app.core.errors import install_error_handlers
from app.models import Session, User
from app.routers import integrations
from app.services import oauth_google, sessions


class _FakeDb:
    def __init__(self, user: User) -> None:
        self._user = user

    async def get(self, model: type[object], key: object) -> object | None:
        if model is User and key == self._user.id:
            return self._user
        return None


def _build_app(user: User, session: Session) -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(integrations.router)

    fake_db = _FakeDb(user)

    async def fake_session() -> Session:
        return session

    async def fake_db_dep() -> _FakeDb:
        return fake_db

    async def fake_redis_dep() -> object:
        return object()

    app.dependency_overrides[deps.current_session] = fake_session
    app.dependency_overrides[db.get_session] = fake_db_dep
    app.dependency_overrides[redis.get_redis] = fake_redis_dep
    return app


@pytest.mark.parametrize("path", ["/integrations/google/connect", "/integrations/google/reconnect"])
@pytest.mark.parametrize("fetch_site", ["cross-site", "Cross-Site", "same-site"])
async def test_navigation_rejects_cross_site_sec_fetch_site(
    monkeypatch: pytest.MonkeyPatch, path: str, fetch_site: str
) -> None:
    user_id = uuid.uuid4()
    user = User(id=user_id, email="student@example.test", google_sub="sub-1", display_name="A")
    session = Session(
        id=uuid.uuid4(),
        user_id=user_id,
        token_hash=sessions._sha256("tok"),
        csrf_hash=sessions._sha256("csrf"),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    limit_spy = AsyncMock()
    monkeypatch.setattr(integrations, "enforce_integration_limit", limit_spy)
    monkeypatch.setattr(settings, "google_client_id", "client-id")
    monkeypatch.setattr(settings, "google_client_secret", SecretStr("client-secret"))

    app = _build_app(user, session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as client:
        response = await client.get(
            path, headers={"Sec-Fetch-Site": fetch_site}, follow_redirects=False
        )

    assert response.status_code == 302
    assert response.headers["location"] == "/settings?calendar=error&reason=csrf_invalid"
    assert deps.OAUTH_COOKIE_NAME not in response.cookies
    limit_spy.assert_not_awaited()


async def test_connect_navigation_allows_same_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = uuid.uuid4()
    user = User(id=user_id, email="student@example.test", google_sub="sub-1", display_name="A")
    session = Session(
        id=uuid.uuid4(),
        user_id=user_id,
        token_hash=sessions._sha256("tok"),
        csrf_hash=sessions._sha256("csrf"),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    limit_spy = AsyncMock()
    monkeypatch.setattr(integrations, "enforce_integration_limit", limit_spy)
    monkeypatch.setattr(settings, "google_client_id", "client-id")
    monkeypatch.setattr(settings, "google_client_secret", SecretStr("client-secret"))
    monkeypatch.setattr(
        oauth_google,
        "build_incremental_authorization_request",
        lambda *, scopes, login_hint: oauth_google.AuthorizationRequest(
            url="https://accounts.google.com/o/oauth2/v2/auth?state=s1",
            state="s1",
            nonce="n1",
            code_verifier="cv1",
        ),
    )

    app = _build_app(user, session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as client:
        response = await client.get(
            "/integrations/google/connect",
            headers={"Sec-Fetch-Site": "same-origin"},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"].startswith("https://accounts.google.com/o/oauth2/v2/auth")
    assert deps.OAUTH_COOKIE_NAME in response.cookies
    limit_spy.assert_awaited_once()

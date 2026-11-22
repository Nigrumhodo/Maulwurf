"""Integration regressions for the G2 review fixes on the Google login path.

Google is replaced by local doubles; what is exercised is the real router, RLS session and
`upsert_google_identity` writes against PostgreSQL.
"""

from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import ASGITransport, AsyncClient, Response
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.config import settings
from app.core.db import get_session
from app.core.deps import COOKIE_NAME, OAUTH_COOKIE_NAME
from app.main import app
from app.services import oauth_google

pytestmark = pytest.mark.integration

SUB = "sub-thermos-g2"


@pytest.fixture
async def client(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    monkeypatch.setattr(settings, "google_client_id", "test-client-id")
    monkeypatch.setattr(settings, "google_client_secret", SecretStr("test-client-secret"))

    async def override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://localhost") as c:
        yield c
    app.dependency_overrides.clear()


def _fake_google(monkeypatch: pytest.MonkeyPatch, *, refresh_token: str | None) -> None:
    async def fake_exchange(code: str, code_verifier: str) -> oauth_google.GoogleTokenResponse:
        tokens: dict[str, Any] = {
            "access_token": f"access-{code}",
            "id_token": "fake-id-token",
            "expires_in": 3600,
        }
        if refresh_token is not None:
            tokens["refresh_token"] = refresh_token
        return oauth_google.GoogleTokenResponse.model_validate(tokens)

    async def fake_verify(id_token: str, expected_nonce: str) -> oauth_google.GoogleIdClaims:
        return oauth_google.GoogleIdClaims(
            sub=SUB, email="student@example.test", email_verified=True, nonce=expected_nonce
        )

    monkeypatch.setattr(oauth_google, "exchange_code", fake_exchange)
    monkeypatch.setattr(oauth_google, "verify_id_token", fake_verify)


def _cookie(response: Response, name: str) -> str:
    for header in response.headers.get_list("set-cookie"):
        if header.startswith(f"{name}="):
            return header.split(";", 1)[0].split("=", 1)[1]
    raise AssertionError(f"cookie {name} missing")


async def _login(client: AsyncClient, code: str) -> Response:
    start = await client.get("/auth/google/start", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    response = await client.get(
        "/auth/google/callback",
        params={"code": code, "state": state},
        cookies={OAUTH_COOKIE_NAME: _cookie(start, OAUTH_COOKIE_NAME)},
        follow_redirects=False,
    )
    assert response.headers["location"] == "/"
    return response


async def _credential_row(db_session: AsyncSession) -> Any:
    return (
        await db_session.execute(
            text(
                "SELECT user_id, status, key_version, access_token_enc, refresh_token_enc "
                "FROM google_credentials"
            )
        )
    ).one()


async def test_login_without_refresh_token_keeps_a_disconnected_integration(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_google(monkeypatch, refresh_token="1//first")
    await _login(client, "c1")
    await db_session.execute(text("UPDATE google_credentials SET status = 'disconnected'"))

    _fake_google(monkeypatch, refresh_token=None)
    await _login(client, "c2")

    assert (await _credential_row(db_session)).status == "disconnected"


async def test_login_with_a_new_refresh_token_reconnects(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_google(monkeypatch, refresh_token="1//first")
    await _login(client, "c1")
    await db_session.execute(text("UPDATE google_credentials SET status = 'disconnected'"))

    _fake_google(monkeypatch, refresh_token="1//second")
    await _login(client, "c2")

    assert (await _credential_row(db_session)).status == "connected"


async def test_login_after_key_rotation_never_relabels_the_old_refresh_blob(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_google(monkeypatch, refresh_token="1//first")
    await _login(client, "c1")
    old_version = (await _credential_row(db_session)).key_version

    new_version = old_version + 1
    monkeypatch.setattr(settings, "encryption_key_version", new_version)
    _fake_google(monkeypatch, refresh_token=None)
    await _login(client, "c2")

    row = await _credential_row(db_session)
    assert row.key_version == new_version
    assert (
        crypto.decrypt(
            row.access_token_enc,
            key_version=row.key_version,
            user_id=row.user_id,
            field="google_credentials.access_token",
        )
        == "access-c2"
    )
    # Only the current key exists, so the old blob cannot be re-encrypted: it is dropped
    # (the worker then asks for a reconnect) instead of being stamped with the new version.
    assert row.refresh_token_enc is None


@pytest.mark.parametrize(
    "path",
    [
        "/integrations/google/connect",
        "/integrations/google/reconnect",
        "/integrations/google/gmail/connect",
    ],
)
async def test_navigation_with_an_unknown_session_redirects_to_login(
    client: AsyncClient, path: str
) -> None:
    response = await client.get(
        path,
        headers={"Sec-Fetch-Site": "same-origin"},
        cookies={COOKIE_NAME: "expired-or-unknown-session"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/login?error=session_expired"

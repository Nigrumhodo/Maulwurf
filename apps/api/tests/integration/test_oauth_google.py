"""I-S1-AN-06 (A1.3): OAuth Google con doble local — state, PKCE, nonce, sub y cifrado.

Sin red ni Google real: `exchange_code` y `verify_id_token` se sustituyen por dobles que
devuelven un token/claims sintéticos. Lo que se prueba de verdad es el flujo del router:
firma/validación de `state`, emisión de sesión, identidad por `sub` y cifrado en reposo.
"""
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import ASGITransport, AsyncClient, Response
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session
from app.core.deps import COOKIE_NAME, OAUTH_COOKIE_NAME
from app.main import app
from app.services import oauth_google

pytestmark = pytest.mark.integration


@pytest.fixture
async def client(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    # Credenciales de prueba: `build_authorization_request` las exige.
    monkeypatch.setattr(settings, "google_client_id", "test-client-id")
    monkeypatch.setattr(settings, "google_client_secret", SecretStr("test-client-secret"))

    async def override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://localhost") as c:
        yield c
    app.dependency_overrides.clear()


def _cookie_value(response: Response, name: str) -> str:
    for header in response.headers.get_list("set-cookie"):
        if header.startswith(f"{name}="):
            return header.split(";", 1)[0].split("=", 1)[1]
    raise AssertionError(f"cookie {name} no presente en la respuesta")


async def _start(client: AsyncClient) -> tuple[str, str]:
    response = await client.get("/auth/google/start", follow_redirects=False)
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("https://accounts.google.com/o/oauth2/auth")
    state = parse_qs(urlparse(location).query)["state"][0]
    return state, _cookie_value(response, OAUTH_COOKIE_NAME)


async def test_start_redirects_to_google_and_sets_state_cookie(client: AsyncClient) -> None:
    state, cookie = await _start(client)

    assert state
    assert cookie


async def test_callback_rejects_foreign_state(client: AsyncClient) -> None:
    _, cookie = await _start(client)

    response = await client.get(
        "/auth/google/callback",
        params={"code": "c", "state": "forged"},
        cookies={OAUTH_COOKIE_NAME: cookie},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/login?error=invalid_state"


async def test_callback_requires_state_cookie(client: AsyncClient) -> None:
    # Sin cookie `mw_oauth` (client recién creado), el state no puede validarse.
    response = await client.get(
        "/auth/google/callback",
        params={"code": "c", "state": "anything"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/login?error=invalid_state"


async def test_callback_maps_google_error(client: AsyncClient) -> None:
    response = await client.get(
        "/auth/google/callback",
        params={"error": "access_denied"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/login?error=access_denied"


async def test_full_flow_creates_user_session_and_encrypts(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, cookie = await _start(client)

    async def fake_exchange(code: str, code_verifier: str) -> dict[str, Any]:
        assert code == "test-code"
        assert code_verifier  # PKCE: el verifier firma el intercambio
        return {
            "access_token": "fake-access",
            "refresh_token": "fake-refresh",
            "id_token": "fake-id-token",
            "expires_in": 3600,
        }

    async def fake_verify_id_token(id_token: str, expected_nonce: str) -> dict[str, Any]:
        assert id_token == "fake-id-token"
        assert expected_nonce  # el nonce viaja firmado desde /start
        return {
            "sub": "google-sub-1",
            "email": "estudiante@example.test",
            "email_verified": True,
            "name": "Estudiante",
            "nonce": expected_nonce,
        }

    monkeypatch.setattr(oauth_google, "exchange_code", fake_exchange)
    monkeypatch.setattr(oauth_google, "verify_id_token", fake_verify_id_token)

    response = await client.get(
        "/auth/google/callback",
        params={"code": "test-code", "state": state},
        cookies={OAUTH_COOKIE_NAME: cookie},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/"
    assert _cookie_value(response, COOKIE_NAME)  # sesión opaca emitida

    user = (
        await db_session.execute(
            text("SELECT email, google_sub FROM users WHERE google_sub = 'google-sub-1'")
        )
    ).one()
    assert user.email == "estudiante@example.test"

    credential = (
        await db_session.execute(
            text("SELECT access_token_enc, refresh_token_enc, status FROM google_credentials")
        )
    ).one()
    assert credential.status == "connected"
    assert b"fake-access" not in credential.access_token_enc
    assert b"fake-refresh" not in credential.refresh_token_enc

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


def _fake_google(
    monkeypatch: pytest.MonkeyPatch,
    *,
    sub: str,
    refresh_token: str | None,
    email_verified: object = True,
) -> None:
    async def fake_exchange(code: str, code_verifier: str) -> dict[str, Any]:
        tokens: dict[str, Any] = {
            "access_token": f"access-{code}",
            "id_token": "fake-id-token",
            "expires_in": 3600,
        }
        if refresh_token is not None:
            tokens["refresh_token"] = refresh_token
        return tokens

    async def fake_verify_id_token(id_token: str, expected_nonce: str) -> dict[str, Any]:
        return {
            "sub": sub,
            "email": "student@example.test",
            "email_verified": email_verified,
            "nonce": expected_nonce,
        }

    monkeypatch.setattr(oauth_google, "exchange_code", fake_exchange)
    monkeypatch.setattr(oauth_google, "verify_id_token", fake_verify_id_token)


async def _login(
    client: AsyncClient, code: str = "c1", cookies: dict[str, str] | None = None
) -> Response:
    state, oauth_cookie = await _start(client)
    return await client.get(
        "/auth/google/callback",
        params={"code": code, "state": state},
        cookies={OAUTH_COOKIE_NAME: oauth_cookie, **(cookies or {})},
        follow_redirects=False,
    )


async def test_start_does_not_request_offline_access(client: AsyncClient) -> None:
    response = await client.get("/auth/google/start", follow_redirects=False)
    query = parse_qs(urlparse(response.headers["location"]).query)

    # Offline access belongs to the incremental Calendar connect (S3), not to login.
    assert "access_type" not in query
    assert "prompt" not in query
    assert query["scope"] == ["openid email profile"]
    assert query["code_challenge_method"] == ["S256"]
    # Initial consent includes the previously granted scopes in the token response only;
    # J3.3 relies on it so a login never returns an access token stripped of Calendar.
    assert query["include_granted_scopes"] == ["true"]


async def test_relogin_without_refresh_token_keeps_the_previous_one(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_google(monkeypatch, sub="sub-refresh", refresh_token="first-refresh")
    assert (await _login(client, "c1")).headers["location"] == "/"
    before = (
        await db_session.execute(text("SELECT refresh_token_enc FROM google_credentials"))
    ).scalar_one()

    _fake_google(monkeypatch, sub="sub-refresh", refresh_token=None)
    assert (await _login(client, "c2")).headers["location"] == "/"
    after = (
        await db_session.execute(
            text("SELECT refresh_token_enc, access_token_enc FROM google_credentials")
        )
    ).one()

    assert before is not None
    assert after.refresh_token_enc == before
    assert b"access-c2" not in after.access_token_enc


async def test_relogin_keeps_an_incremental_calendar_grant(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """J3.3: a later login must not erase the Calendar scopes of the incremental grant."""
    _fake_google(monkeypatch, sub="sub-calendar", refresh_token="first-refresh")
    assert (await _login(client, "c1")).headers["location"] == "/"
    await db_session.execute(
        text(
            "UPDATE google_credentials SET scopes = :scopes WHERE user_id = "
            "(SELECT id FROM users WHERE google_sub = 'sub-calendar')"
        ),
        {
            "scopes": [
                "openid",
                "email",
                "profile",
                "https://www.googleapis.com/auth/calendar.app.created",
            ]
        },
    )
    await db_session.commit()

    # The token response omits `scope`: the previous grant is kept (OAuth 2.0 §5.1 only
    # requires `scope` when it differs from the request), never silently reset.
    _fake_google(monkeypatch, sub="sub-calendar", refresh_token=None)
    assert (await _login(client, "c2")).headers["location"] == "/"

    scopes = (
        await db_session.execute(
            text(
                "SELECT scopes FROM google_credentials WHERE user_id = "
                "(SELECT id FROM users WHERE google_sub = 'sub-calendar')"
            )
        )
    ).scalar_one()
    assert "https://www.googleapis.com/auth/calendar.app.created" in scopes


async def test_account_being_deleted_gets_no_session(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await db_session.execute(
        text(
            "INSERT INTO users (google_sub, email, status) "
            "VALUES ('sub-deleting', 'gone@example.test', 'deleting')"
        )
    )
    await db_session.commit()
    _fake_google(monkeypatch, sub="sub-deleting", refresh_token=None)

    response = await _login(client)

    assert response.headers["location"] == "/login?error=oauth_failed"
    assert COOKIE_NAME not in response.headers.get("set-cookie", "")
    sessions_count = (
        await db_session.execute(
            text(
                "SELECT count(*) FROM sessions s JOIN users u ON u.id = s.user_id "
                "WHERE u.google_sub = 'sub-deleting'"
            )
        )
    ).scalar_one()
    assert sessions_count == 0


async def test_login_revokes_the_session_the_browser_already_had(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_google(monkeypatch, sub="sub-fixation", refresh_token=None)
    first = _cookie_value(await _login(client, "c1"), COOKIE_NAME)

    second = await _login(client, "c2", cookies={COOKIE_NAME: first})

    assert _cookie_value(second, COOKIE_NAME) != first
    assert (await client.get("/me", cookies={COOKIE_NAME: first})).status_code == 401


@pytest.mark.parametrize("email_verified", [False, None, "true"])
async def test_unverified_email_gets_no_account(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    email_verified: object,
) -> None:
    _fake_google(
        monkeypatch, sub="sub-unverified", refresh_token=None, email_verified=email_verified
    )

    response = await _login(client)

    assert response.headers["location"] == "/login?error=oauth_failed"
    users = (
        await db_session.execute(
            text("SELECT count(*) FROM users WHERE google_sub = 'sub-unverified'")
        )
    ).scalar_one()
    assert users == 0

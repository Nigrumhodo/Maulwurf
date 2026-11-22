"""Regression tests for the G2 review fixes: OAuth logging, state TTL, navigation auth,
refresh-token key versions and the shared Google client credentials."""

import io
import logging
import time
import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.core import crypto, db, redis
from app.core.config import settings
from app.core.errors import auth_required, install_error_handlers
from app.core.logging import REDACTED, RedactionFilter, scrub_query
from app.core.security import sign_state, verify_state
from app.models import GoogleCredential, User
from app.routers import integrations
from app.services import google_tokens, oauth_google

CODE = "4/0AQSTgQ-authorization-code-g2"
STATE = "signed-state-value-g2"


# --- Finding 1: OAuth callback query values never reach the access log ----------------


def _emit_access_line(path: str) -> str:
    from uvicorn.logging import AccessFormatter

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s'))
    logger = logging.getLogger("uvicorn.access")
    old_handlers, old_filters = logger.handlers[:], logger.filters[:]
    old_propagate = logger.propagate
    logger.handlers = [handler]
    logger.filters = [RedactionFilter()]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    try:
        logger.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:5000", "GET", path, "1.1", 302)
    finally:
        logger.handlers = old_handlers
        logger.filters = old_filters
        logger.propagate = old_propagate
    return stream.getvalue()


def test_access_log_redacts_oauth_code_and_state() -> None:
    output = _emit_access_line(f"/auth/google/callback?code={CODE}&state={STATE}")

    assert CODE not in output
    assert STATE not in output
    assert "/auth/google/callback?" in output
    assert "302" in output


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/healthz", "/healthz"),
        ("/search?q=ohm&page=2", "/search?q=ohm&page=2"),
        (f"/cb?state={STATE}&code={CODE}", f"/cb?state={REDACTED}&code={REDACTED}"),
        (f"/cb?error=access_denied&Code={CODE}", f"/cb?error=access_denied&Code={REDACTED}"),
        ("/cb?api_key=k1&x=1", f"/cb?api_key={REDACTED}&x=1"),
        ("/cb?code=", f"/cb?code={REDACTED}"),
    ],
)
def test_scrub_query_masks_only_sensitive_parameters(path: str, expected: str) -> None:
    assert scrub_query(path) == expected


# --- Finding 5: verify_state enforces max_age_seconds ---------------------------------


def test_verify_state_accepts_a_token_within_max_age() -> None:
    payload = {"state": "s", "exp": int(time.time()) + 600}
    assert verify_state(sign_state(payload), 600) == payload


def test_verify_state_rejects_a_token_minted_for_a_longer_lifetime() -> None:
    token = sign_state({"state": "s", "exp": int(time.time()) + 3600})

    assert verify_state(token, 600) is None
    assert verify_state(token, 3600) is not None


# --- Finding 4: GET navigation without a session redirects to /login ------------------


def _navigation_app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(integrations.router)

    async def fake_db_dep() -> object:
        return object()

    async def fake_redis_dep() -> object:
        return object()

    app.dependency_overrides[db.get_session] = fake_db_dep
    app.dependency_overrides[redis.get_redis] = fake_redis_dep
    return app


@pytest.mark.parametrize(
    "path",
    [
        "/integrations/google/connect",
        "/integrations/google/reconnect",
        "/integrations/google/gmail/connect",
    ],
)
async def test_navigation_without_session_redirects_to_login(
    monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    async def expired(db: object, request: object) -> object:
        raise auth_required()

    monkeypatch.setattr(integrations, "resolve_authenticated_session", expired)

    app = _navigation_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as client:
        response = await client.get(
            path, headers={"Sec-Fetch-Site": "same-origin"}, follow_redirects=False
        )

    assert response.status_code == 302
    assert response.headers["location"] == "/login?error=session_expired"
    assert response.headers["cache-control"] == "no-store"


async def test_status_route_still_answers_json_401_without_session() -> None:
    app = _navigation_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as client:
        response = await client.get("/integrations/status")

    assert response.status_code == 401


# --- Finding 3: a kept refresh token is never relabeled with another key version -------


def _credential(user: User, *, refresh: str | None) -> GoogleCredential:
    access, version = crypto.encrypt(
        "ya29.old", user_id=user.id, field="google_credentials.access_token"
    )
    blob = None
    if refresh is not None:
        blob, _ = crypto.encrypt(
            refresh, user_id=user.id, field="google_credentials.refresh_token"
        )
    return GoogleCredential(
        user_id=user.id,
        access_token_enc=access,
        refresh_token_enc=blob,
        key_version=version,
        scopes=[],
        status="connected",
    )


def test_kept_refresh_token_is_unchanged_when_the_key_version_matches() -> None:
    user = User(id=uuid.uuid4(), google_sub="s", email="a@example.test")
    credential = _credential(user, refresh="1//keep")
    before = credential.refresh_token_enc

    issued = oauth_google._set_refresh_token(
        credential, user, None, settings.encryption_key_version
    )

    assert issued is False
    assert credential.refresh_token_enc == before


def test_kept_refresh_token_is_reencrypted_under_the_new_key_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = User(id=uuid.uuid4(), google_sub="s", email="a@example.test")
    credential = _credential(user, refresh="1//keep")
    old_version = credential.key_version
    seen: list[int] = []

    def decrypt_old(blob: bytes, *, key_version: int, user_id: uuid.UUID, field: str) -> str:
        seen.append(key_version)
        return "1//keep"

    monkeypatch.setattr(oauth_google, "decrypt", decrypt_old)
    new_version = old_version + 1
    monkeypatch.setattr(settings, "encryption_key_version", new_version)

    oauth_google._set_refresh_token(credential, user, None, new_version)
    credential.key_version = new_version

    assert seen == [old_version]
    assert credential.refresh_token_enc is not None
    assert (
        crypto.decrypt(
            credential.refresh_token_enc,
            key_version=new_version,
            user_id=user.id,
            field="google_credentials.refresh_token",
        )
        == "1//keep"
    )


def test_undecryptable_old_refresh_token_is_dropped_not_relabeled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = User(id=uuid.uuid4(), google_sub="s", email="a@example.test")
    credential = _credential(user, refresh="1//keep")
    new_version = credential.key_version + 1
    monkeypatch.setattr(settings, "encryption_key_version", new_version)

    oauth_google._set_refresh_token(credential, user, None, new_version)

    assert credential.refresh_token_enc is None


def test_new_refresh_token_is_always_stored() -> None:
    user = User(id=uuid.uuid4(), google_sub="s", email="a@example.test")
    credential = _credential(user, refresh=None)

    issued = oauth_google._set_refresh_token(
        credential, user, "1//new", settings.encryption_key_version
    )

    assert issued is True
    assert credential.refresh_token_enc is not None


# --- Finding 6: one owner for the Google token endpoint and client credentials ---------


def test_google_tokens_reuses_the_oauth_token_endpoint() -> None:
    assert vars(google_tokens)["GOOGLE_TOKEN_URL"] is oauth_google.GOOGLE_TOKEN_URL


def test_google_tokens_translates_missing_client_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "google_client_id", None)
    monkeypatch.setattr(settings, "google_client_secret", None)

    with pytest.raises(google_tokens.GoogleTokenUnavailable) as raised:
        google_tokens._client_credentials()

    assert raised.value.code == "oauth_not_configured"


def test_google_tokens_returns_the_shared_client_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "google_client_id", "client-id")
    monkeypatch.setattr(settings, "google_client_secret", SecretStr("client-secret"))

    assert google_tokens._client_credentials() == ("client-id", "client-secret")

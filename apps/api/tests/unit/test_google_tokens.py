"""J3.4 fix: the worker renews an expired Google access token before using it.

The job cannot ask the user to log in again, so an expired access token must be exchanged for
a new one with the encrypted refresh token (under a row lock) instead of failing the task.
Every case here is deterministic: `httpx.MockTransport` plays Google's token endpoint and no
real credential or network call is involved.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import decrypt, encrypt
from app.models import GoogleCredential
from app.services.google_tokens import (
    GoogleTokenRejected,
    GoogleTokenTransient,
    GoogleTokenUnavailable,
    fresh_access_token,
    token_needs_refresh,
)

NOW = datetime(2026, 11, 2, 14, 0, tzinfo=UTC)


class FakeSession:
    """Only the two coroutines `fresh_access_token` uses."""

    def __init__(self, credential: GoogleCredential | None = None) -> None:
        self.credential = credential
        self.flushes = 0

    async def flush(self) -> None:
        self.flushes += 1

    async def get(self, _model: Any, _key: Any) -> GoogleCredential | None:
        return self.credential


class TokenEndpoint:
    def __init__(self, response: httpx.Response | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.response = response or httpx.Response(
            200, json={"access_token": "new-access", "expires_in": 3599}
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.response


def _credential(
    *,
    access_token: str = "old-access",  # noqa: S107 - fixture, not a credential
    refresh_token: str | None = "refresh-1",  # noqa: S107 - fixture, not a credential
    expires_at: datetime | None,
    status: str = "connected",
) -> GoogleCredential:
    user_id = uuid.uuid4()
    access_blob, version = encrypt(
        access_token, user_id=user_id, field="google_credentials.access_token"
    )
    refresh_blob = None
    if refresh_token is not None:
        refresh_blob, _ = encrypt(
            refresh_token, user_id=user_id, field="google_credentials.refresh_token"
        )
    return GoogleCredential(
        user_id=user_id,
        access_token_enc=access_blob,
        refresh_token_enc=refresh_blob,
        key_version=version,
        status=status,
        token_expires_at=expires_at,
    )


def _session(fake: FakeSession) -> AsyncSession:
    return cast(AsyncSession, fake)


@pytest.fixture(autouse=True)
def oauth_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "google_client_id", "test-client-id")
    monkeypatch.setattr(settings, "google_client_secret", SecretStr("test-client-secret"))


def _stored_access(credential: GoogleCredential) -> str:
    return decrypt(
        credential.access_token_enc,
        key_version=credential.key_version,
        user_id=credential.user_id,
        field="google_credentials.access_token",
    )


def _stored_refresh(credential: GoogleCredential) -> str:
    assert credential.refresh_token_enc is not None
    return decrypt(
        credential.refresh_token_enc,
        key_version=credential.key_version,
        user_id=credential.user_id,
        field="google_credentials.refresh_token",
    )


@pytest.mark.parametrize(
    ("offset_seconds", "expected"),
    [(-1, True), (0, True), (30, True), (59, True), (61, False), (1800, False)],
)
def test_the_refresh_window_has_a_one_minute_skew(
    offset_seconds: int, expected: bool
) -> None:
    expires_at = NOW + timedelta(seconds=offset_seconds)

    assert token_needs_refresh(expires_at, now=NOW) is expected


def test_an_unknown_expiry_never_forces_a_refresh() -> None:
    assert token_needs_refresh(None, now=NOW) is False


async def test_a_fresh_token_is_used_without_calling_google() -> None:
    credential = _credential(expires_at=NOW + timedelta(hours=1))
    endpoint = TokenEndpoint()
    session = FakeSession(credential)

    token = await fresh_access_token(
        _session(session),
        credential,
        now=NOW,
        transport=httpx.MockTransport(endpoint),
    )

    assert token == "old-access"
    assert endpoint.requests == []
    assert session.flushes == 0


async def test_an_expired_token_is_refreshed_and_the_row_rewritten() -> None:
    credential = _credential(expires_at=NOW - timedelta(seconds=1))
    endpoint = TokenEndpoint()
    session = FakeSession(credential)

    token = await fresh_access_token(
        _session(session),
        credential,
        now=NOW,
        transport=httpx.MockTransport(endpoint),
    )

    assert token == "new-access"
    assert _stored_access(credential) == "new-access"
    assert credential.token_expires_at == NOW + timedelta(seconds=3599)
    assert credential.last_refresh_at == NOW
    assert session.flushes == 1
    assert len(endpoint.requests) == 1
    sent = endpoint.requests[0]
    assert sent.url == httpx.URL("https://oauth2.googleapis.com/token")
    assert sent.headers["content-type"] == "application/x-www-form-urlencoded"
    form = dict(httpx.QueryParams(sent.content.decode()))
    assert form == {
        "client_id": "test-client-id",
        "client_secret": "test-client-secret",
        "refresh_token": "refresh-1",
        "grant_type": "refresh_token",
    }


async def test_an_imminent_expiry_is_refreshed_before_the_call() -> None:
    credential = _credential(expires_at=NOW + timedelta(seconds=30))
    endpoint = TokenEndpoint()

    token = await fresh_access_token(
        _session(FakeSession(credential)),
        credential,
        now=NOW,
        transport=httpx.MockTransport(endpoint),
    )

    assert token == "new-access"
    assert len(endpoint.requests) == 1


async def test_without_a_refresh_token_the_integration_needs_a_reconnect() -> None:
    credential = _credential(refresh_token=None, expires_at=NOW - timedelta(minutes=5))
    endpoint = TokenEndpoint()

    with pytest.raises(GoogleTokenUnavailable) as raised:
        await fresh_access_token(
            _session(FakeSession(credential)),
            credential,
            now=NOW,
            transport=httpx.MockTransport(endpoint),
        )

    assert raised.value.code == "refresh_token_missing"
    assert endpoint.requests == []


async def test_a_revoked_refresh_token_disconnects_the_integration() -> None:
    credential = _credential(expires_at=NOW - timedelta(minutes=5))
    endpoint = TokenEndpoint(
        httpx.Response(400, json={"error": "invalid_grant", "error_description": "revoked"})
    )
    session = FakeSession(credential)

    with pytest.raises(GoogleTokenRejected):
        await fresh_access_token(
            _session(session),
            credential,
            now=NOW,
            transport=httpx.MockTransport(endpoint),
        )

    assert credential.status == "disconnected"
    assert session.flushes == 1
    assert _stored_access(credential) == "old-access"


async def test_a_transient_token_endpoint_failure_is_retried_by_arq() -> None:
    credential = _credential(expires_at=NOW - timedelta(minutes=5))
    endpoint = TokenEndpoint(httpx.Response(503, json={"error": "backendError"}))

    with pytest.raises(GoogleTokenTransient):
        await fresh_access_token(
            _session(FakeSession(credential)),
            credential,
            now=NOW,
            transport=httpx.MockTransport(endpoint),
        )

    assert credential.status == "connected"


async def test_a_rejected_oauth_client_does_not_disconnect_the_user() -> None:
    credential = _credential(expires_at=NOW - timedelta(minutes=5))
    endpoint = TokenEndpoint(
        httpx.Response(401, json={"error": "invalid_client"})
    )

    with pytest.raises(GoogleTokenUnavailable):
        await fresh_access_token(
            _session(FakeSession(credential)),
            credential,
            now=NOW,
            transport=httpx.MockTransport(endpoint),
        )

    assert credential.status == "connected"


async def test_a_rotated_refresh_token_replaces_the_stored_one() -> None:
    credential = _credential(expires_at=NOW - timedelta(minutes=5))
    endpoint = TokenEndpoint(
        httpx.Response(
            200,
            json={
                "access_token": "new-access",
                "expires_in": 3599,
                "refresh_token": "refresh-2",
            },
        )
    )

    await fresh_access_token(
        _session(FakeSession(credential)),
        credential,
        now=NOW,
        transport=httpx.MockTransport(endpoint),
    )

    assert _stored_refresh(credential) == "refresh-2"


async def test_a_response_without_a_new_refresh_token_keeps_the_previous_one() -> None:
    credential = _credential(expires_at=NOW - timedelta(minutes=5))
    endpoint = TokenEndpoint()

    await fresh_access_token(
        _session(FakeSession(credential)),
        credential,
        now=NOW,
        transport=httpx.MockTransport(endpoint),
    )

    assert _stored_refresh(credential) == "refresh-1"


async def test_missing_oauth_client_settings_leave_the_task_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "google_client_id", None)
    monkeypatch.setattr(settings, "google_client_secret", None)
    credential = _credential(expires_at=NOW - timedelta(minutes=5))

    with pytest.raises(GoogleTokenUnavailable) as raised:
        await fresh_access_token(_session(FakeSession(credential)), credential, now=NOW)

    assert raised.value.code == "oauth_not_configured"

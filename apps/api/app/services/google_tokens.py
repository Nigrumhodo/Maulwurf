"""Google access-token renewal for worker jobs (J3.4, seam with J3.3).

The worker has no browser: when the stored Calendar access token is expired it must renew it
from the encrypted refresh token instead of failing the task. Three rules keep this safe:

- **One stored refresh at a time.** The token POST runs with no transaction open. The
  write takes a short ``FOR UPDATE`` and stores the new blobs only when the refresh
  ciphertext is still the one this call decrypted.
- **Re-encrypt both blobs with the row's current key version.** ``google_credentials`` keeps a
  single ``key_version``; keeping the old refresh ciphertext while bumping the version would
  make the next decrypt fail when the key rotates.
- **Never log token material.** Only stable codes and the HTTP status are reported, and
  ``invalid_grant`` disconnects the integration through the same helper J3.2 uses.

Revoked or missing refresh tokens surface as `GoogleTokenUnavailable`/`GoogleTokenRejected`
so the job marks the task `blocked` (J3.3 moves it back to `pending` after a reconnect).
"""

import logging
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import decrypt, encrypt
from app.models import GoogleCredential
from app.services.google_calendar import disconnect_google_credential
from app.services.oauth_google import GoogleTokenResponse

logger = logging.getLogger(__name__)

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - URL pública
HTTP_TIMEOUT_SECONDS = 10.0
# Refresh slightly before the real expiry: a call that starts 30 s before it can still lose
# the race against Google's clock.
REFRESH_SKEW_SECONDS = 60
# Google documents `expires_in` on every refresh; the fallback only feeds the expiry column.
DEFAULT_EXPIRES_IN_SECONDS = 3600.0
_TRANSIENT_STATUS = frozenset({408, 429, 500, 502, 503, 504})


class GoogleTokenError(Exception):
    """Typed token-refresh failure; the job decides `blocked` or ARQ retry from the type."""

    code = "google_token_error"

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class GoogleTokenUnavailable(GoogleTokenError):
    """The integration cannot renew on its own and needs a reconnect (not retryable)."""


class GoogleTokenRejected(GoogleTokenError):
    """Google revoked the refresh token; the integration is already `disconnected`."""


class GoogleTokenTransient(GoogleTokenError):
    """The token endpoint failed in a retryable way (network, 408/429/5xx)."""


def token_needs_refresh(
    expires_at: datetime | None,
    *,
    now: datetime | None = None,
    skew_seconds: float = REFRESH_SKEW_SECONDS,
) -> bool:
    """Whether a stored expiry is inside the refresh window (unknown expiry never does)."""
    if expires_at is None:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    reference = now or datetime.now(UTC)
    return expires_at <= reference + timedelta(seconds=skew_seconds)


def _client_credentials() -> tuple[str, str]:
    client_id = settings.google_client_id
    client_secret = settings.google_client_secret
    if client_id is None or client_secret is None:
        raise GoogleTokenUnavailable("oauth_not_configured")
    return client_id, client_secret.get_secret_value()


def _error_code(payload: Mapping[str, Any]) -> str | None:
    value = payload.get("error")
    return value if isinstance(value, str) else None


def _expires_in(tokens: GoogleTokenResponse) -> float:
    if tokens.expires_in is None or tokens.expires_in <= 0:
        return DEFAULT_EXPIRES_IN_SECONDS
    return float(tokens.expires_in)


async def _request_tokens(
    client_id: str,
    client_secret: str,
    refresh_token: str,
    transport: httpx.AsyncBaseTransport | None,
) -> GoogleTokenResponse:
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    try:
        async with httpx.AsyncClient(
            timeout=HTTP_TIMEOUT_SECONDS, transport=transport
        ) as client:
            response = await client.post(GOOGLE_TOKEN_URL, data=data)
    except httpx.HTTPError as exc:
        raise GoogleTokenTransient("token_endpoint_unreachable") from exc
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    body = payload if isinstance(payload, dict) else {}
    if response.status_code == httpx.codes.OK:
        try:
            tokens = GoogleTokenResponse.model_validate(body)
        except ValidationError as exc:
            raise GoogleTokenTransient("token_response_without_access_token") from exc
        if not tokens.access_token:
            raise GoogleTokenTransient("token_response_without_access_token")
        return tokens
    code = _error_code(body)
    if code == "invalid_grant":
        raise GoogleTokenRejected("invalid_grant")
    if response.status_code in _TRANSIENT_STATUS:
        raise GoogleTokenTransient(f"token_endpoint_status_{response.status_code}")
    # `invalid_client` and friends are a deployment problem, not a revoked user token: the
    # integration stays `connected` and the task waits for an operator (J3.3 reconnect).
    logger.warning(
        "google_tokens.refresh_rejected status=%d code=%s",
        response.status_code,
        code or "unknown",
    )
    raise GoogleTokenUnavailable("token_endpoint_rejected")


async def _end_read(db: AsyncSession) -> None:
    """Release the caller's transaction before a token POST.

    ``commit`` rather than ``rollback``: the caller may have flushed work in this
    transaction that must survive, and a commit is what returns the connection.
    """
    await db.commit()


async def _lock_credential(db: AsyncSession, user_id: uuid.UUID) -> GoogleCredential | None:
    return (
        await db.execute(
            select(GoogleCredential)
            .where(GoogleCredential.user_id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


async def fresh_access_token(
    db: AsyncSession,
    credential: GoogleCredential,
    *,
    now: datetime | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    force: bool = False,
) -> str:
    """Return a usable access token, refreshing it first when it is inside the window.

    ``force`` is used after a 401: Google can invalidate an access token before its advertised
    expiry, so the reactive path asks for a new one regardless of the stored timestamp.
    The refresh POST runs with no transaction open. A later short lock stores the new blobs
    only when the refresh-token ciphertext is still the one this call decrypted.
    """
    reference = now or datetime.now(UTC)
    user_id: uuid.UUID = credential.user_id
    expires_at = credential.token_expires_at
    access_blob = credential.access_token_enc
    access_version = credential.key_version
    refresh_blob = credential.refresh_token_enc
    refresh_version = credential.key_version
    if not force and not token_needs_refresh(expires_at, now=reference):
        await _end_read(db)
        return decrypt(
            access_blob,
            key_version=access_version,
            user_id=user_id,
            field="google_credentials.access_token",
        )
    if refresh_blob is None:
        await _end_read(db)
        raise GoogleTokenUnavailable("refresh_token_missing")
    client_id, client_secret = _client_credentials()
    seen_refresh = bytes(refresh_blob)
    refresh_token = decrypt(
        seen_refresh,
        key_version=refresh_version,
        user_id=user_id,
        field="google_credentials.refresh_token",
    )
    await _end_read(db)
    try:
        payload = await _request_tokens(client_id, client_secret, refresh_token, transport)
    except GoogleTokenRejected:
        await disconnect_google_credential(db, user_id)
        await db.commit()
        logger.warning("google_tokens.invalid_grant user_id=%s", user_id)
        raise
    access_token = payload.access_token

    current = await _lock_credential(db, user_id)
    if (
        current is None
        or current.status != "connected"
        or current.refresh_token_enc is None
        or bytes(current.refresh_token_enc) != seen_refresh
    ):
        if current is None or current.status != "connected" or current.refresh_token_enc is None:
            await db.rollback()
            raise GoogleTokenUnavailable("google_not_connected")
        # Another refresh stored a new ciphertext first. Use that access token.
        stored = decrypt(
            current.access_token_enc,
            key_version=current.key_version,
            user_id=user_id,
            field="google_credentials.access_token",
        )
        await db.rollback()
        return stored

    current.access_token_enc, current.key_version = encrypt(
        access_token, user_id=user_id, field="google_credentials.access_token"
    )
    keep = payload.refresh_token if payload.refresh_token else refresh_token
    current.refresh_token_enc, _ = encrypt(
        keep, user_id=user_id, field="google_credentials.refresh_token"
    )
    current.token_expires_at = reference + timedelta(seconds=_expires_in(payload))
    current.last_refresh_at = reference
    await db.flush()
    await db.commit()
    logger.info("google_tokens.refreshed user_id=%s", user_id)
    return access_token

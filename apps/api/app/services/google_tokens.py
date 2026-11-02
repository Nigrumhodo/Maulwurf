"""Google access-token renewal for worker jobs (J3.4, seam with J3.3).

The worker has no browser: when the stored Calendar access token is expired it must renew it
from the encrypted refresh token instead of failing the task. Three rules keep this safe:

- **One refresh at a time.** The caller loads the credential with ``SELECT … FOR UPDATE``, so
  two concurrent deliveries cannot spend the same refresh token or overwrite each other's
  result.
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
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import decrypt, encrypt
from app.models import GoogleCredential
from app.services.google_calendar import disconnect_google_credential

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


def _expires_in(payload: Mapping[str, Any]) -> float:
    value = payload.get("expires_in")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return DEFAULT_EXPIRES_IN_SECONDS
    return float(value)


async def _request_tokens(
    client_id: str,
    client_secret: str,
    refresh_token: str,
    transport: httpx.AsyncBaseTransport | None,
) -> dict[str, Any]:
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
        return body
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
    The caller must hold the row lock (``SELECT … FOR UPDATE``) so renewals do not race.
    """
    reference = now or datetime.now(UTC)
    user_id: uuid.UUID = credential.user_id
    if not force and not token_needs_refresh(credential.token_expires_at, now=reference):
        return decrypt(
            credential.access_token_enc,
            key_version=credential.key_version,
            user_id=user_id,
            field="google_credentials.access_token",
        )
    if credential.refresh_token_enc is None:
        raise GoogleTokenUnavailable("refresh_token_missing")
    client_id, client_secret = _client_credentials()
    refresh_token = decrypt(
        credential.refresh_token_enc,
        key_version=credential.key_version,
        user_id=user_id,
        field="google_credentials.refresh_token",
    )
    try:
        payload = await _request_tokens(client_id, client_secret, refresh_token, transport)
    except GoogleTokenRejected:
        await disconnect_google_credential(db, user_id)
        logger.warning("google_tokens.invalid_grant user_id=%s", user_id)
        raise
    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise GoogleTokenTransient("token_response_without_access_token")

    credential.access_token_enc, credential.key_version = encrypt(
        access_token, user_id=user_id, field="google_credentials.access_token"
    )
    rotated = payload.get("refresh_token")
    keep = refresh_token if not (isinstance(rotated, str) and rotated) else rotated
    credential.refresh_token_enc, _ = encrypt(
        keep, user_id=user_id, field="google_credentials.refresh_token"
    )
    credential.token_expires_at = reference + timedelta(seconds=_expires_in(payload))
    credential.last_refresh_at = reference
    await db.flush()
    logger.info("google_tokens.refreshed user_id=%s", user_id)
    return access_token

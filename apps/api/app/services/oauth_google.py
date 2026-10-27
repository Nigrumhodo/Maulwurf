"""OAuth Google (A1.3): Authorization Code + PKCE con identidad por `sub`.

- `build_authorization_request()` arma la URL de Google y el material efímero del login.
- `exchange_code()` intercambia el código por tokens con el `code_verifier` (PKCE).
- `verify_id_token()` valida firma (JWKS) y reclama `nonce`/`iss`/`aud`/`exp`.
- `upsert_google_identity()` crea/actualiza `User` y cifra los tokens en `GoogleCredential`.

Reglas: los tokens de Google jamás salen del backend ni se loguean; solo se persisten
cifrados con AES-GCM (`core/crypto.py`). Toda llamada HTTP es async (httpx) para no bloquear
el event loop.
"""
import asyncio
import base64
import hashlib
import secrets
import time
import urllib.parse
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
from jwt import PyJWKSet
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import encrypt
from app.models import GoogleCredential, User

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - URL pública, no secreto
GOOGLE_CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
# Google ID tokens carry either issuer form (Google's OIDC discovery document).
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
HTTP_TIMEOUT_SECONDS = 10.0
JWKS_CACHE_SECONDS = 3600
JWKS_REFRESH_COOLDOWN_SECONDS = 60

# OIDC: identidad + perfil para login; Calendar/Gmail se conectan en S3 (J3.3).
SCOPES = ("openid", "email", "profile")

# Cookie HttpOnly firmada que custodia state/nonce/code_verifier entre start y callback.
OAUTH_STATE_TTL_SECONDS = 600


class OAuthError(Exception):
    """Fallo del flujo OAuth: se traduce a una redirección con `?error=` (nunca un 500)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class AuthorizationRequest:
    url: str
    state: str
    nonce: str
    code_verifier: str


def redirect_uri() -> str:
    return f"{settings.allowed_origin}/auth/google/callback"


def _client_credentials() -> tuple[str, str]:
    client_id = settings.google_client_id
    client_secret = settings.google_client_secret
    if client_id is None or client_secret is None:
        raise OAuthError("oauth_not_configured")
    return client_id, client_secret.get_secret_value()


def _code_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def build_authorization_request() -> AuthorizationRequest:
    client_id, _ = _client_credentials()
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(64)
    query = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri(),
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "state": state,
            "nonce": nonce,
            "code_challenge": _code_challenge(code_verifier),
            "code_challenge_method": "S256",
        }
    )
    return AuthorizationRequest(
        url=f"{GOOGLE_AUTH_URL}?{query}",
        state=state,
        nonce=nonce,
        code_verifier=code_verifier,
    )


async def exchange_code(code: str, code_verifier: str) -> dict[str, Any]:
    """Intercambia el código por tokens. `OAuthError` si Google lo rechaza."""
    client_id, client_secret = _client_credentials()
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            response = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": redirect_uri(),
                    "grant_type": "authorization_code",
                    "code_verifier": code_verifier,
                },
            )
        payload = response.json() if response.status_code == 200 else None
    except (httpx.HTTPError, ValueError) as exc:
        raise OAuthError("token_exchange_failed") from exc
    if payload is None:
        raise OAuthError("token_exchange_failed")
    if not isinstance(payload, dict):
        raise OAuthError("token_exchange_invalid")
    return payload


_jwks_cache: tuple[float, PyJWKSet] | None = None
_jwks_lock = asyncio.Lock()
_jwks_fetched_at = float("-inf")


async def _fetch_jwks() -> PyJWKSet:
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            response = await client.get(GOOGLE_CERTS_URL)
        if response.status_code != 200:
            raise OAuthError("jwks_unavailable")
        return PyJWKSet.from_dict(response.json())
    except (httpx.HTTPError, ValueError, jwt.PyJWTError) as exc:
        raise OAuthError("jwks_unavailable") from exc


async def _google_keys(*, refresh: bool = False) -> PyJWKSet:
    # Google rotates its keys every few days: the cache avoids one external call per login, and
    # an unknown `kid` forces a reload before the token is rejected. The lock and the cooldown
    # keep a burst of callbacks with a forged `kid` from turning into a burst of downloads.
    global _jwks_cache, _jwks_fetched_at
    async with _jwks_lock:
        now = time.monotonic()
        expired = _jwks_cache is None or _jwks_cache[0] < now
        cooled_down = now - _jwks_fetched_at >= JWKS_REFRESH_COOLDOWN_SECONDS
        if expired or (refresh and cooled_down):
            _jwks_fetched_at = now
            _jwks_cache = (now + JWKS_CACHE_SECONDS, await _fetch_jwks())
        if _jwks_cache is None:
            raise OAuthError("jwks_unavailable")
        return _jwks_cache[1]


async def verify_id_token(id_token: str, expected_nonce: str) -> dict[str, Any]:
    """Verifica firma (JWKS) y reclama `nonce`/`iss`/`aud`/`exp` del ID token."""
    client_id, _ = _client_credentials()
    key_set = await _google_keys()
    try:
        kid = jwt.get_unverified_header(id_token).get("kid")
    except jwt.PyJWTError as exc:
        raise OAuthError("id_token_invalid") from exc
    if not isinstance(kid, str):
        raise OAuthError("id_token_invalid")
    if kid not in {key.key_id for key in key_set.keys}:
        key_set = await _google_keys(refresh=True)
    try:
        claims = jwt.decode(
            id_token,
            key_set[kid],
            algorithms=["RS256"],
            audience=client_id,
            issuer=GOOGLE_ISSUERS,
            # PyJWT only checks `exp` when it is present: a token without it must not pass.
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except (jwt.PyJWTError, KeyError) as exc:
        raise OAuthError("id_token_invalid") from exc
    if claims.get("nonce") != expected_nonce:
        raise OAuthError("nonce_mismatch")
    return claims


def _access_expiry(tokens: dict[str, Any]) -> datetime | None:
    expires_in = tokens.get("expires_in")
    if not isinstance(expires_in, int) or expires_in <= 0:
        return None
    return datetime.now(UTC) + timedelta(seconds=expires_in)


async def upsert_google_identity(
    db: AsyncSession, claims: dict[str, Any], tokens: dict[str, Any]
) -> User:
    """Crea/actualiza `User` por `sub` y persiste los tokens cifrados en `GoogleCredential`."""
    sub = claims.get("sub")
    email = claims.get("email")
    access_token = tokens.get("access_token")
    if not isinstance(sub, str) or not isinstance(email, str) or not isinstance(access_token, str):
        raise OAuthError("token_exchange_incomplete")
    # Identity is the `sub`, but the email feeds notifications (S4): an address Google has not
    # verified is never stored nor trusted, so the login fails closed.
    if claims.get("email_verified") is not True:
        raise OAuthError("email_unverified")

    user = await db.scalar(select(User).where(User.google_sub == sub))
    if user is None:
        user = User(google_sub=sub, email=email)
        db.add(user)
        try:
            await db.flush()
        except IntegrityError as exc:
            # Two concurrent first logins for the same `sub`: one wins, the other logs in again.
            raise OAuthError("identity_conflict") from exc
    elif user.status != "active":
        # Account being deleted: no new session. The reason never reaches the browser.
        raise OAuthError("account_unavailable")
    user.email = email
    user.email_verified = True
    name = claims.get("name")
    if isinstance(name, str) and name:
        user.display_name = name

    access_blob, key_version = encrypt(
        access_token, user_id=user.id, field="google_credentials.access_token"
    )
    credential = await db.get(GoogleCredential, user.id)
    if credential is None:
        credential = GoogleCredential(user_id=user.id)
        db.add(credential)
    credential.access_token_enc = access_blob
    refresh_token = tokens.get("refresh_token")
    if isinstance(refresh_token, str) and refresh_token:
        credential.refresh_token_enc, _ = encrypt(
            refresh_token, user_id=user.id, field="google_credentials.refresh_token"
        )
    # Without a new refresh token the previous one is kept (ESPECIFICACION, Google tokens).
    credential.key_version = key_version
    credential.scopes = list(SCOPES)
    credential.status = "connected"
    credential.token_expires_at = _access_expiry(tokens)
    await db.flush()
    return user

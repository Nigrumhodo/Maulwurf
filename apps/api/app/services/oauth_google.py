"""OAuth Google (A1.3): Authorization Code + PKCE con identidad por `sub`.

- `build_authorization_request()` arma la URL de Google y el material efímero del login.
- `exchange_code()` intercambia el código por tokens con el `code_verifier` (PKCE).
- `verify_id_token()` valida firma (JWKS) y reclama `nonce`/`iss`/`aud`/`exp`.
- `upsert_google_identity()` crea/actualiza `User` y cifra los tokens en `GoogleCredential`.

Reglas: los tokens de Google jamás salen del backend ni se loguean; solo se persisten
cifrados con AES-GCM (`core/crypto.py`). Toda llamada HTTP es async (httpx) para no bloquear
el event loop.
"""
import base64
import hashlib
import secrets
import urllib.parse
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
from jwt import PyJWKSet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import encrypt
from app.models import GoogleCredential, User

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - URL pública, no secreto
GOOGLE_CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUER = "https://accounts.google.com"

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
            "access_type": "offline",
            "prompt": "consent",
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
    async with httpx.AsyncClient() as client:
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
    if response.status_code != 200:
        raise OAuthError("token_exchange_failed")
    payload = response.json()
    if not isinstance(payload, dict):
        raise OAuthError("token_exchange_invalid")
    return payload


async def verify_id_token(id_token: str, expected_nonce: str) -> dict[str, Any]:
    """Verifica firma (JWKS) y reclama `nonce`/`iss`/`aud`/`exp` del ID token."""
    client_id, _ = _client_credentials()
    async with httpx.AsyncClient() as client:
        jwks_response = await client.get(GOOGLE_CERTS_URL)
    if jwks_response.status_code != 200:
        raise OAuthError("jwks_unavailable")
    key_set = PyJWKSet.from_dict(jwks_response.json())
    try:
        kid = jwt.get_unverified_header(id_token).get("kid")
        if not isinstance(kid, str):
            raise OAuthError("id_token_invalid")
        claims = jwt.decode(
            id_token,
            key_set[kid],
            algorithms=["RS256"],
            audience=client_id,
            issuer=GOOGLE_ISSUER,
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

    user = await db.scalar(select(User).where(User.google_sub == sub))
    if user is None:
        user = User(google_sub=sub, email=email)
        db.add(user)
        await db.flush()
    user.email = email
    user.email_verified = bool(claims.get("email_verified"))
    name = claims.get("name")
    if isinstance(name, str) and name:
        user.display_name = name

    access_blob, key_version = encrypt(
        access_token, user_id=user.id, field="google_credentials.access_token"
    )
    refresh_token = tokens.get("refresh_token")
    refresh_blob: bytes | None = None
    if isinstance(refresh_token, str) and refresh_token:
        refresh_blob, _ = encrypt(
            refresh_token, user_id=user.id, field="google_credentials.refresh_token"
        )

    credential = await db.get(GoogleCredential, user.id)
    if credential is None:
        credential = GoogleCredential(user_id=user.id)
        db.add(credential)
    credential.access_token_enc = access_blob
    credential.refresh_token_enc = refresh_blob
    credential.key_version = key_version
    credential.scopes = list(SCOPES)
    credential.status = "connected"
    credential.token_expires_at = _access_expiry(tokens)
    await db.flush()
    return user

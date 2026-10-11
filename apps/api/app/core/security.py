"""Firma HMAC de tokens de estado (OAuth): `sign_state` / `verify_state`.

El contenido efímero del login (`state`, `nonce`, `code_verifier`) viaja en una cookie
`mw_oauth` firmada con `oauth_state_secret`. El callback la verifica sin guardar nada en el
servidor; un token ajeno, alterado o expirado falla en tiempo constante y devuelve `None`.
"""
import base64
import hashlib
import hmac
import json
import time
from typing import Any

from app.core.config import settings


def _key() -> bytes:
    return settings.oauth_state_secret.get_secret_value().encode()


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64d(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def sign_state(payload: dict[str, Any]) -> str:
    """Devuelve `body.signature` en base64url, firmado con HMAC-SHA256."""
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode())
    signature = hmac.new(_key(), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64e(signature)}"


def verify_state(token: str, max_age_seconds: int) -> dict[str, Any] | None:
    """Payload si la firma es válida y no expiró; `None` en cualquier otro caso."""
    try:
        body, presented = token.split(".", 1)
        expected = hmac.new(_key(), body.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64d(presented), expected):
            return None
        payload = json.loads(_b64d(body).decode("utf-8"))
    except (ValueError, UnicodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    exp = payload.get("exp", 0)
    if not isinstance(exp, int) or exp < int(time.time()):
        return None
    return payload

"""Internal credential for api → ingest (ADR-0008 §2, A2.13).

``verify`` is what the reception endpoints run. ``mint`` exists so the fixed test
vectors can be reproduced; the running service never mints a token. The API keeps
its own copy of these functions and the two packages do not import each other.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

Op = Literal["receive", "admit", "discard"]
_OPS = frozenset({"receive", "admit", "discard"})
_KEYS = frozenset({"op", "att", "aud", "uid", "ft", "ins", "exp"})
_PREFIX = b"maulwurf-ingest/v1."


class CredentialError(Exception):
    """Any failed check. Callers answer 401 and do not touch the attempt row."""


class WrongInstance(CredentialError):
    """Phase-2 token minted for another ingest process. The endpoint answers 409."""


@dataclass(frozen=True)
class CredentialClaims:
    op: Op
    attempt_id: uuid.UUID
    audio_id: uuid.UUID
    user_id: uuid.UUID
    fencing_token: int
    instance: str | None
    expires_at: int


def mint(claims: CredentialClaims, *, kid: str, secret: bytes) -> str:
    payload = _canonical(claims)
    payload_b64 = _b64encode(payload)
    mac = hmac.new(secret, _PREFIX + kid.encode("ascii") + b"." + payload_b64, hashlib.sha256)
    return "v1." + kid + "." + payload_b64.decode("ascii") + "." + _b64encode(mac.digest()).decode(
        "ascii"
    )


def verify(
    token: str,
    *,
    keys: Mapping[str, bytes],
    expected_op: Op,
    now: int,
    instance: str | None,
) -> CredentialClaims:
    """Check the MAC, the canonical payload, ``op``, ``exp``, and ``ins``.

    ``att`` is compared with the path by the caller. A wrong ``ins`` is still a
    ``CredentialError`` here; the endpoint turns that one case into ``409
    wrong_instance`` before any other mismatch becomes ``401``.
    """
    parts = token.split(".")
    if len(parts) != 4 or parts[0] != "v1" or not parts[1] or not parts[2] or not parts[3]:
        raise CredentialError("malformed")
    kid = parts[1]
    secret = keys.get(kid)
    if secret is None:
        raise CredentialError("kid")
    try:
        payload = _b64decode(parts[2])
        mac = _b64decode(parts[3])
    except ValueError:
        raise CredentialError("encoding") from None
    signed = _PREFIX + kid.encode("ascii") + b"." + parts[2].encode("ascii")
    expected = hmac.new(secret, signed, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expected):
        raise CredentialError("mac")
    claims = _parse(payload)
    if claims.op != expected_op:
        raise CredentialError("op")
    if claims.expires_at < now:
        raise CredentialError("exp")
    if expected_op == "receive":
        if claims.instance is not None:
            raise CredentialError("ins")
    elif claims.instance != instance:
        raise WrongInstance("ins")
    return claims


def _canonical(claims: CredentialClaims) -> bytes:
    document = {
        "att": str(claims.attempt_id),
        "aud": str(claims.audio_id),
        "exp": claims.expires_at,
        "ft": claims.fencing_token,
        "ins": claims.instance,
        "op": claims.op,
        "uid": str(claims.user_id),
    }
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


def _parse(payload: bytes) -> CredentialClaims:
    try:
        parsed: object = json.loads(payload)
    except json.JSONDecodeError:
        raise CredentialError("payload") from None
    if not isinstance(parsed, dict):
        raise CredentialError("payload")
    again = json.dumps(parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )
    if again != payload or set(parsed) != _KEYS:
        raise CredentialError("canonical")
    op = parsed["op"]
    if op not in _OPS:
        raise CredentialError("op")
    operation: Op = "receive"
    if op == "admit":
        operation = "admit"
    elif op == "discard":
        operation = "discard"
    fencing = parsed["ft"]
    expires = parsed["exp"]
    if (
        isinstance(fencing, bool) or not isinstance(fencing, int) or fencing < 0
        or isinstance(expires, bool) or not isinstance(expires, int)
    ):
        raise CredentialError("payload")
    instance = parsed["ins"]
    if instance is not None and not isinstance(instance, str):
        raise CredentialError("ins")
    try:
        attempt_id = uuid.UUID(str(parsed["att"]))
        audio_id = uuid.UUID(str(parsed["aud"]))
        user_id = uuid.UUID(str(parsed["uid"]))
    except ValueError:
        raise CredentialError("payload") from None
    return CredentialClaims(
        operation,
        attempt_id,
        audio_id,
        user_id,
        fencing,
        instance if isinstance(instance, str) else None,
        expires,
    )


def decode_secret(raw: str) -> bytes:
    """Hex secret of at least 32 bytes. ``change-me`` is refused in every environment."""
    if "change-me" in raw.lower():
        raise ValueError("credential")
    try:
        secret = bytes.fromhex(raw.strip())
    except ValueError:
        raise ValueError("credential") from None
    if len(secret) < 32:
        raise ValueError("credential")
    return secret


def _b64encode(data: bytes) -> bytes:
    return base64.urlsafe_b64encode(data).rstrip(b"=")


def _b64decode(text: str) -> bytes:
    if not text or any(char in text for char in "+/="):
        raise ValueError("padding")
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)

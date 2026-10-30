"""Internal credential for api → ingest (ADR-0008 §2, A2.13).

`mint` is what the API sends with every internal request; `verify` is what the
reception endpoints of `ingest` run. The two packages keep independent copies and
share the fixed vectors in `tests/fixtures/ingest_credential_vectors.json`, so a
drift on either side fails a test instead of a production request.
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
    """Any failed check. The endpoint answers 401 and leaves the attempt row untouched."""


class WrongInstance(CredentialError):
    """Phase-2 token minted for another ingest process."""


@dataclass(frozen=True)
class CredentialClaims:
    """Claims bound to one internal operation (ADR-0008 §2 payload table)."""

    op: Op
    attempt_id: uuid.UUID
    audio_id: uuid.UUID
    user_id: uuid.UUID
    fencing_token: int
    instance: str | None
    expires_at: int


def mint(claims: CredentialClaims, *, kid: str, secret: bytes) -> str:
    """Return `v1.<kid>.<payload>.<mac>` for ``claims`` signed with ``secret``."""
    payload_b64 = _b64encode(_canonical(claims))
    mac = hmac.new(secret, _signed_input(kid, payload_b64), hashlib.sha256).digest()
    return f"v1.{kid}.{payload_b64.decode('ascii')}.{_b64encode(mac).decode('ascii')}"


def verify(
    token: str,
    *,
    keys: Mapping[str, bytes],
    expected_op: Op,
    now: int,
    instance: str | None,
) -> CredentialClaims:
    """Check the MAC, the canonical payload, ``op``, ``exp`` and ``ins``.

    ``att`` and the database binding are checked by the caller. A wrong ``ins`` raises
    `WrongInstance` so the endpoint can answer ``409 wrong_instance``.
    """
    parts = token.split(".")
    if len(parts) != 4 or parts[0] != "v1" or not all(parts[1:]):
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
    expected = hmac.new(secret, _signed_input(kid, parts[2].encode("ascii")), hashlib.sha256)
    if not hmac.compare_digest(mac, expected.digest()):
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


def _signed_input(kid: str, payload_b64: bytes) -> bytes:
    return _PREFIX + kid.encode("ascii") + b"." + payload_b64


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
    fencing = parsed["ft"]
    expires = parsed["exp"]
    if (
        isinstance(fencing, bool)
        or not isinstance(fencing, int)
        or fencing < 0
        or isinstance(expires, bool)
        or not isinstance(expires, int)
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
        op=op,
        attempt_id=attempt_id,
        audio_id=audio_id,
        user_id=user_id,
        fencing_token=fencing,
        instance=instance,
        expires_at=expires,
    )


def _b64encode(data: bytes) -> bytes:
    return base64.urlsafe_b64encode(data).rstrip(b"=")


def _b64decode(text: str) -> bytes:
    if not text or any(char in text for char in "+/="):
        raise ValueError("padding")
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)

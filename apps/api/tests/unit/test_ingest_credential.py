"""ADR-0008 §2 credential: the shared vectors, and the rejections the reception endpoints rely on.

The fixture is the same file `apps/ingest` runs (`tests/fixtures/ingest_credential_vectors.json`),
so a drift of either copy of `verify`/`mint` fails here, not in production.
"""

import base64
import hashlib
import hmac
import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from app.services.ingest_credential import (
    CredentialClaims,
    CredentialError,
    Op,
    WrongInstance,
    mint,
    verify,
)

VECTORS = Path(__file__).resolve().parents[1] / "fixtures" / "ingest_credential_vectors.json"
SECRET = bytes.fromhex("00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff")
OTHER = bytes.fromhex("ffeeddccbbaa99887766554433221100ffeeddccbbaa99887766554433221100")
NOW = 1_893_456_000


def _claims(**overrides: Any) -> CredentialClaims:
    values: dict[str, Any] = {
        "op": "receive",
        "attempt_id": uuid.UUID("11111111-1111-1111-1111-111111111111"),
        "audio_id": uuid.UUID("22222222-2222-2222-2222-222222222222"),
        "user_id": uuid.UUID("33333333-3333-3333-3333-333333333333"),
        "fencing_token": 7,
        "instance": None,
        "expires_at": NOW,
    }
    values.update(overrides)
    return CredentialClaims(**values)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _sign(payload: bytes, *, kid: str = "k1", secret: bytes = SECRET) -> str:
    payload_b64 = _b64(payload)
    mac = hmac.new(
        secret, b"maulwurf-ingest/v1." + kid.encode() + b"." + payload_b64.encode(), hashlib.sha256
    ).digest()
    return f"v1.{kid}.{payload_b64}.{_b64(mac)}"


def _payload(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "att": "11111111-1111-1111-1111-111111111111",
        "aud": "22222222-2222-2222-2222-222222222222",
        "exp": NOW,
        "ft": 7,
        "ins": None,
        "op": "receive",
        "uid": "33333333-3333-3333-3333-333333333333",
        **overrides,
    }
    return document


def _canonical(document: dict[str, Any]) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode()


def test_minted_token_matches_the_shared_vector() -> None:
    document = json.loads(VECTORS.read_text(encoding="utf-8"))
    token = mint(_claims(), kid=document["kid"], secret=bytes.fromhex(document["secret_hex"]))

    assert token == document["token"]
    assert verify(
        token, keys={"k1": SECRET}, expected_op="receive", now=NOW, instance=None
    ) == _claims()


def test_wrong_secret_and_unknown_kid_are_rejected() -> None:
    token = mint(_claims(), kid="k1", secret=SECRET)

    with pytest.raises(CredentialError, match="mac"):
        verify(token, keys={"k1": OTHER}, expected_op="receive", now=1, instance=None)
    with pytest.raises(CredentialError, match="kid"):
        verify(token, keys={"k2": SECRET}, expected_op="receive", now=1, instance=None)


def test_previous_kid_is_accepted_only_when_configured() -> None:
    previous = mint(_claims(), kid="k0", secret=OTHER)

    with pytest.raises(CredentialError):
        verify(previous, keys={"k1": SECRET}, expected_op="receive", now=1, instance=None)
    verify(previous, keys={"k1": SECRET, "k0": OTHER}, expected_op="receive", now=1, instance=None)


def test_expired_token_is_rejected_at_the_boundary() -> None:
    token = mint(_claims(), kid="k1", secret=SECRET)

    verify(token, keys={"k1": SECRET}, expected_op="receive", now=NOW, instance=None)
    with pytest.raises(CredentialError, match="exp"):
        verify(token, keys={"k1": SECRET}, expected_op="receive", now=NOW + 1, instance=None)


@pytest.mark.parametrize(
    ("minted", "expected"),
    [("admit", "discard"), ("discard", "admit"), ("receive", "admit"), ("admit", "receive")],
)
def test_every_operation_swap_is_rejected(minted: Op, expected: Op) -> None:
    instance = None if minted == "receive" else "box"
    token = mint(_claims(op=minted, instance=instance), kid="k1", secret=SECRET)

    with pytest.raises(CredentialError, match="op"):
        verify(token, keys={"k1": SECRET}, expected_op=expected, now=1, instance="box")


def test_wrong_instance_is_a_distinct_error() -> None:
    token = mint(_claims(op="admit", instance="box"), kid="k1", secret=SECRET)

    with pytest.raises(WrongInstance):
        verify(token, keys={"k1": SECRET}, expected_op="admit", now=1, instance="other")
    verify(token, keys={"k1": SECRET}, expected_op="admit", now=1, instance="box")


def test_altered_payload_and_altered_mac_are_rejected() -> None:
    token = mint(_claims(), kid="k1", secret=SECRET)
    head, kid, payload, mac = token.split(".")
    flipped_payload = _b64(_canonical(_payload(ft=8)))

    with pytest.raises(CredentialError):
        verify(f"{head}.{kid}.{flipped_payload}.{mac}", keys={"k1": SECRET},
               expected_op="receive", now=1, instance=None)
    swapped = mac[:-1] + ("A" if mac[-1] != "A" else "B")
    with pytest.raises(CredentialError):
        verify(f"{head}.{kid}.{payload}.{swapped}", keys={"k1": SECRET},
               expected_op="receive", now=1, instance=None)


@pytest.mark.parametrize(
    "document",
    [
        _payload(op="delete"),
        {key: value for key, value in _payload().items() if key != "exp"},
        _payload(extra=1),
    ],
    ids=["unknown-op", "missing-field", "extra-field"],
)
def test_unknown_op_missing_field_and_extra_field_are_rejected(document: dict[str, Any]) -> None:
    with pytest.raises(CredentialError):
        verify(_sign(_canonical(document)), keys={"k1": SECRET},
               expected_op="receive", now=1, instance=None)


def test_non_canonical_json_with_the_same_keys_is_rejected() -> None:
    spaced = json.dumps(_payload(), indent=2, sort_keys=True).encode()

    with pytest.raises(CredentialError, match="canonical"):
        verify(_sign(spaced), keys={"k1": SECRET}, expected_op="receive", now=1, instance=None)


def test_a_correctly_signed_canonical_payload_is_accepted() -> None:
    token = _sign(_canonical(_payload()))

    claims = verify(token, keys={"k1": SECRET}, expected_op="receive", now=1, instance=None)
    assert claims == _claims()

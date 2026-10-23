"""U-S2-SG-16 and the ADR-0008 credential vectors (S2.11, S2.13)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from maulwurf_ingest.credential import (
    CredentialClaims,
    CredentialError,
    WrongInstance,
    mint,
    verify,
)
from maulwurf_ingest.outcomes import CATALOG, DISCARD_REASONS, PROBE_REASONS

_VECTORS = Path(__file__).parent / "fixtures" / "ingest_credential_vectors.json"
_SECRET = bytes.fromhex("00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff")
_OTHER = bytes.fromhex("ffeeddccbbaa99887766554433221100ffeeddccbbaa99887766554433221100")


def _claims(**overrides: object) -> CredentialClaims:
    values: dict[str, object] = {
        "op": "receive",
        "attempt_id": uuid.UUID("11111111-1111-1111-1111-111111111111"),
        "audio_id": uuid.UUID("22222222-2222-2222-2222-222222222222"),
        "user_id": uuid.UUID("33333333-3333-3333-3333-333333333333"),
        "fencing_token": 7,
        "instance": None,
        "expires_at": 1_893_456_000,
    }
    values.update(overrides)
    return CredentialClaims(**values)  # type: ignore[arg-type]


def test_frozen_vector_round_trip() -> None:
    document = json.loads(_VECTORS.read_text(encoding="utf-8"))
    claims = _claims()
    token = mint(claims, kid="k1", secret=_SECRET)
    assert token == document["token"]
    verified = verify(
        token, keys={"k1": _SECRET}, expected_op="receive", now=1_893_456_000, instance=None,
    )
    assert verified == claims


def test_mac_kid_expiry_and_operation_are_rejected() -> None:
    token = mint(_claims(), kid="k1", secret=_SECRET)
    with pytest.raises(CredentialError):
        verify(token, keys={"k1": _OTHER}, expected_op="receive", now=1, instance=None)
    with pytest.raises(CredentialError):
        verify(token, keys={"k2": _SECRET}, expected_op="receive", now=1, instance=None)
    previous = mint(_claims(), kid="k0", secret=_OTHER)
    with pytest.raises(CredentialError):
        verify(previous, keys={"k1": _SECRET}, expected_op="receive", now=1, instance=None)
    verify(
        previous, keys={"k1": _SECRET, "k0": _OTHER}, expected_op="receive", now=1, instance=None,
    )
    with pytest.raises(CredentialError):
        verify(token, keys={"k1": _SECRET}, expected_op="receive", now=1_893_456_001, instance=None)
    admit = mint(_claims(op="admit", instance="box"), kid="k1", secret=_SECRET)
    with pytest.raises(CredentialError):
        verify(admit, keys={"k1": _SECRET}, expected_op="discard", now=1, instance="box")
    with pytest.raises(WrongInstance):
        verify(admit, keys={"k1": _SECRET}, expected_op="admit", now=1, instance="other")
    verify(admit, keys={"k1": _SECRET}, expected_op="admit", now=1, instance="box")


def test_payload_must_be_canonical() -> None:
    import hashlib
    import hmac

    token = mint(_claims(), kid="k1", secret=_SECRET)
    head, kid, payload, mac = token.split(".")
    raw = _pad_bytes(payload)
    parsed = json.loads(raw)
    parsed["extra"] = 1
    extra = json.dumps(parsed, sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(CredentialError):
        verify(_signed(extra), keys={"k1": _SECRET}, expected_op="receive", now=1, instance=None)
    spaced = json.dumps(json.loads(raw), indent=2).encode()
    with pytest.raises(CredentialError):
        verify(_signed(spaced), keys={"k1": _SECRET}, expected_op="receive", now=1, instance=None)
    missing = dict(json.loads(raw))
    del missing["exp"]
    with pytest.raises(CredentialError):
        verify(
            _signed(json.dumps(missing, sort_keys=True, separators=(",", ":")).encode()),
            keys={"k1": _SECRET}, expected_op="receive", now=1, instance=None,
        )
    swapped = mac[:-1] + ("A" if mac[-1] != "A" else "B")
    with pytest.raises(CredentialError):
        verify(
            f"{head}.{kid}.{payload}.{swapped}",
            keys={"k1": _SECRET}, expected_op="receive", now=1, instance=None,
        )
    del hashlib, hmac


def _signed(payload: bytes) -> str:
    import hashlib
    import hmac

    payload_b64 = _encode(payload)
    mac = hmac.new(
        _SECRET, b"maulwurf-ingest/v1.k1." + payload_b64.encode("ascii"), hashlib.sha256,
    ).digest()
    return "v1.k1." + payload_b64 + "." + _encode(mac)


def test_catalog_covers_every_persisted_code() -> None:
    written = PROBE_REASONS | DISCARD_REASONS | {
        "payload_too_large",
        "no_usable_text",
        "asr_failed",
        "asr_timeout",
        "asr_rejected",
        "supervisor_error",
        "reception_interrupted",
        "reception_abandoned",
    }
    assert written == set(CATALOG)
    for code, spec in CATALOG.items():
        assert spec.status in {"rejected", "requires_reupload", "cancelled"}
        assert spec.http_status in {409, 413, 415, 422, 500, 503}
        assert spec.http_code
        assert spec.message
        assert code
    assert CATALOG["probe_unavailable"].status == "requires_reupload"
    assert CATALOG["malformed"].http_code == "unsupported_format"


def _pad_bytes(payload: str) -> bytes:
    import base64

    padded = payload + ("=" * (-len(payload) % 4))
    return base64.urlsafe_b64decode(padded)


def _encode(data: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

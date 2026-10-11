"""U-S1 (A1.3): firma de estado OAuth — HMAC, expiración y rechazo de tokens ajenos."""
import time

from app.core.security import sign_state, verify_state


def test_roundtrip_recovers_payload() -> None:
    payload = {"state": "s", "nonce": "n", "code_verifier": "v", "exp": int(time.time()) + 600}
    token = sign_state(payload)

    assert verify_state(token, 600) == payload


def test_tampered_signature_is_rejected() -> None:
    token = sign_state({"exp": int(time.time()) + 600})
    body, _ = token.split(".", 1)
    forged = f"{body}.{'A' * 43}"

    assert verify_state(forged, 600) is None


def test_expired_token_is_rejected() -> None:
    token = sign_state({"exp": int(time.time()) - 1})

    assert verify_state(token, 600) is None


def test_malformed_token_is_rejected() -> None:
    assert verify_state("", 600) is None
    assert verify_state("sin-punto-y-firma", 600) is None
    assert verify_state("a.b.c", 600) is None

"""U-S1 (A1.3): ID token checks — accepted issuers, nonce, audience and key rotation.

The JWKS comes from a local RSA key; no network and no real Google.
"""
import json
import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt import PyJWKSet
from jwt.algorithms import RSAAlgorithm
from pydantic import SecretStr

from app.core.config import settings
from app.services import oauth_google
from app.services.oauth_google import OAuthError

CLIENT_ID = "test-client-id"


@pytest.fixture
def key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(autouse=True)
def google(monkeypatch: pytest.MonkeyPatch, key: rsa.RSAPrivateKey) -> list[int]:
    monkeypatch.setattr(settings, "google_client_id", CLIENT_ID)
    monkeypatch.setattr(settings, "google_client_secret", SecretStr("test-secret"))
    monkeypatch.setattr(oauth_google, "_jwks_cache", None)
    jwk = json.loads(RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid="k1", alg="RS256", use="sig")
    fetches: list[int] = []

    async def fake_fetch() -> PyJWKSet:
        fetches.append(1)
        return PyJWKSet.from_dict({"keys": [jwk]})

    monkeypatch.setattr(oauth_google, "_fetch_jwks", fake_fetch)
    return fetches


def _token(key: rsa.RSAPrivateKey, kid: str = "k1", **overrides: Any) -> str:
    claims = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": "sub-1",
        "nonce": "n-1",
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
        **overrides,
    }
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


@pytest.mark.parametrize("issuer", ["https://accounts.google.com", "accounts.google.com"])
async def test_both_google_issuers_are_accepted(key: rsa.RSAPrivateKey, issuer: str) -> None:
    claims = await oauth_google.verify_id_token(_token(key, iss=issuer), "n-1")

    assert claims["sub"] == "sub-1"


@pytest.mark.parametrize(
    "overrides",
    [
        {"iss": "https://evil.example"},
        {"aud": "another-client"},
        {"exp": int(time.time()) - 60},
    ],
)
async def test_foreign_or_expired_token_is_rejected(
    key: rsa.RSAPrivateKey, overrides: dict[str, Any]
) -> None:
    with pytest.raises(OAuthError):
        await oauth_google.verify_id_token(_token(key, **overrides), "n-1")


async def test_nonce_mismatch_is_rejected(key: rsa.RSAPrivateKey) -> None:
    with pytest.raises(OAuthError, match="nonce_mismatch"):
        await oauth_google.verify_id_token(_token(key), "another-nonce")


async def test_token_signed_by_another_key_is_rejected() -> None:
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    with pytest.raises(OAuthError):
        await oauth_google.verify_id_token(_token(other), "n-1")


async def test_keys_are_cached_and_unknown_kid_forces_one_reload(
    key: rsa.RSAPrivateKey, google: list[int]
) -> None:
    await oauth_google.verify_id_token(_token(key), "n-1")
    await oauth_google.verify_id_token(_token(key), "n-1")
    assert len(google) == 1

    with pytest.raises(OAuthError):
        await oauth_google.verify_id_token(_token(key, kid="rotated"), "n-1")
    assert len(google) == 2

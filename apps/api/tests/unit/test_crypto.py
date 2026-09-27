"""U-S1-AN-03 (A1.5): AES-GCM con nonce único, versión de clave y AAD por usuario/campo."""
import uuid

import pytest

from app.core import crypto
from app.core.config import settings

USER_A = uuid.uuid4()
USER_B = uuid.uuid4()
TOKEN = "ya29.fake-access-token-for-tests"
FIELD: crypto.CryptoField = "google_credentials.access_token"


def test_roundtrip_returns_plaintext_and_current_key_version() -> None:
    blob, version = crypto.encrypt(TOKEN, user_id=USER_A, field=FIELD)

    assert version == settings.encryption_key_version
    assert crypto.decrypt(blob, key_version=version, user_id=USER_A, field=FIELD) == TOKEN


def test_blob_never_contains_the_plaintext() -> None:
    blob, _ = crypto.encrypt(TOKEN, user_id=USER_A, field=FIELD)

    assert TOKEN.encode() not in blob


def test_each_encryption_uses_a_fresh_nonce() -> None:
    nonces = {
        crypto.encrypt(TOKEN, user_id=USER_A, field=FIELD)[0][: crypto.NONCE_BYTES]
        for _ in range(200)
    }

    assert len(nonces) == 200


@pytest.mark.parametrize(
    ("user_id", "field"),
    [(USER_B, FIELD), (USER_A, "google_credentials.refresh_token")],
    ids=["other-user", "other-field"],
)
def test_blob_is_bound_to_its_user_and_field(
    user_id: uuid.UUID, field: crypto.CryptoField
) -> None:
    blob, version = crypto.encrypt(TOKEN, user_id=USER_A, field=FIELD)

    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(blob, key_version=version, user_id=user_id, field=field)


def test_tampered_blob_is_rejected() -> None:
    blob, version = crypto.encrypt(TOKEN, user_id=USER_A, field=FIELD)
    tampered = blob[:-1] + bytes([blob[-1] ^ 0x01])

    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(tampered, key_version=version, user_id=USER_A, field=FIELD)


def test_unknown_key_version_is_rejected() -> None:
    blob, version = crypto.encrypt(TOKEN, user_id=USER_A, field=FIELD)

    with pytest.raises(crypto.DecryptionError, match="key_version"):
        crypto.decrypt(blob, key_version=version + 1, user_id=USER_A, field=FIELD)


def test_truncated_blob_is_rejected() -> None:
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(b"short", key_version=1, user_id=USER_A, field=FIELD)


def test_key_depends_on_version() -> None:
    secret = "same-secret"
    nonce = b"\x00" * crypto.NONCE_BYTES

    v1 = crypto._cipher(secret, 1).encrypt(nonce, b"x", None)
    v2 = crypto._cipher(secret, 2).encrypt(nonce, b"x", None)

    assert v1 != v2

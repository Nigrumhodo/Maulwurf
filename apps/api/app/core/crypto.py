"""Cifrado AES-GCM de credenciales en reposo (A1.5, U-S1-AN-03).

Formato de cada blob: `nonce (12 bytes) || ciphertext+tag`. Cada cifrado usa un nonce
aleatorio nuevo, así dos tokens del mismo usuario nunca comparten nonce. El AAD ata el blob
a su usuario y a su campo: copiar el token de A a la fila de B, o el refresh al access,
hace fallar el descifrado. `key_version` viaja en la fila; S1 solo conoce la clave actual,
la rotación con claves anteriores llega cuando exista una segunda versión.
"""
import os
import uuid
from functools import lru_cache

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import settings

NONCE_BYTES = 12
_HKDF_INFO = b"maulwurf/credentials/aes-256-gcm"


class DecryptionError(Exception):
    """El blob no corresponde a esta clave, usuario o campo, o fue alterado."""


@lru_cache(maxsize=4)
def _cipher(secret: str, version: int) -> AESGCM:
    # HKDF: la variable de entorno puede ser cualquier secreto de alta entropía; la clave
    # AES-256 se deriva por versión, así cambiar de versión cambia la clave efectiva.
    key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=f"key-version:{version}".encode(),
        info=_HKDF_INFO,
    ).derive(secret.encode())
    return AESGCM(key)


def _current() -> tuple[AESGCM, int]:
    version = settings.encryption_key_version
    return _cipher(settings.encryption_key.get_secret_value(), version), version


def _aad(user_id: uuid.UUID, field: str) -> bytes:
    return f"{user_id}:{field}".encode()


def encrypt(plaintext: str, *, user_id: uuid.UUID, field: str) -> tuple[bytes, int]:
    """Cifra y devuelve `(blob, key_version)` para guardar ambos en la fila."""
    cipher, version = _current()
    nonce = os.urandom(NONCE_BYTES)
    return nonce + cipher.encrypt(nonce, plaintext.encode(), _aad(user_id, field)), version


def decrypt(blob: bytes, *, key_version: int, user_id: uuid.UUID, field: str) -> str:
    cipher, version = _current()
    if key_version != version:
        raise DecryptionError(f"key_version {key_version} no disponible")
    if len(blob) <= NONCE_BYTES:
        raise DecryptionError("blob truncado")
    try:
        plaintext = cipher.decrypt(blob[:NONCE_BYTES], blob[NONCE_BYTES:], _aad(user_id, field))
    except Exception as exc:  # InvalidTag: no revelar detalle del fallo
        raise DecryptionError("descifrado rechazado") from exc
    return plaintext.decode()

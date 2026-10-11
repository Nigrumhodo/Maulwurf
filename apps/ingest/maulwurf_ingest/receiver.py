"""Recepción streaming a un fichero privado del intento (S2.1).

El hash es SHA-256 de los bytes escritos, calculado por chunk. No hay parámetro
de `Content-Length`: ni el hash ni el corte salen de un tamaño declarado.
Si el acumulado supera el tope, se borra el fichero de ese intento.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

_CHUNK_BYTES = 65_536


class PayloadTooLarge(Exception):
    """El stream superó el tope de bytes. El temporal de este intento ya se borró."""

    def __init__(self) -> None:
        super().__init__("payload_too_large")


@dataclass(frozen=True)
class ReceiveResult:
    path: Path
    sha256: str
    received_bytes: int


async def iter_bytes(data: bytes, chunk_size: int = _CHUNK_BYTES) -> AsyncIterator[bytes]:
    """Parte un buffer ya en memoria para reutilizar el mismo camino que el stream."""
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    if not data:
        yield b""
        return
    for offset in range(0, len(data), chunk_size):
        yield data[offset : offset + chunk_size]


async def receive(
    chunks: AsyncIterator[bytes],
    dest: Path,
    *,
    max_bytes: int,
) -> ReceiveResult:
    """Escribe `dest` con O_EXCL y 0600. Corta y borra si `max_bytes` se supera."""
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    digest = hashlib.sha256()
    total = 0
    handle = await asyncio.to_thread(_open_private, dest)
    try:
        async for chunk in chunks:
            if not isinstance(chunk, bytes):
                raise TypeError("chunk must be bytes")
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                raise PayloadTooLarge()
            digest.update(chunk)
            await asyncio.to_thread(handle.write, chunk)
        await asyncio.to_thread(handle.flush)
    except BaseException:
        handle.close()
        await asyncio.to_thread(_unlink, dest)
        raise
    handle.close()
    return ReceiveResult(path=dest, sha256=digest.hexdigest(), received_bytes=total)


def _open_private(path: Path) -> BinaryIO:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    return os.fdopen(fd, "wb")


def _unlink(path: Path) -> None:
    path.unlink(missing_ok=True)

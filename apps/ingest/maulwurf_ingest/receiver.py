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


class ReceptionTimeout(Exception):
    """The client stopped delivering bytes before the reception budget ended."""

    def __init__(self) -> None:
        super().__init__("reception_timeout")


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


def _read_budget(
    loop: asyncio.AbstractEventLoop,
    deadline: float | None,
    chunk_timeout_s: float | None,
) -> float | None:
    """Seconds allowed for the next chunk, or None when neither budget is set."""
    remaining: float | None = None
    if deadline is not None:
        remaining = deadline - loop.time()
        if remaining <= 0:
            return 0.0
    if chunk_timeout_s is None:
        return remaining
    if remaining is None:
        return chunk_timeout_s
    return min(remaining, chunk_timeout_s)


async def receive(
    chunks: AsyncIterator[bytes],
    dest: Path,
    *,
    max_bytes: int,
    chunk_timeout_s: float | None = None,
    wall_timeout_s: float | None = None,
) -> ReceiveResult:
    """Escribe `dest` con O_EXCL y 0600. Corta y borra si `max_bytes` se supera.

    `chunk_timeout_s` bounds the idle gap between body chunks. `wall_timeout_s`
    bounds the whole read. Either one raises `ReceptionTimeout` and unlinks `dest`,
    so a slow client cannot keep the attempt directory or the admission slot.
    """
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    if chunk_timeout_s is not None and chunk_timeout_s <= 0:
        raise ValueError("chunk_timeout_s must be positive")
    if wall_timeout_s is not None and wall_timeout_s <= 0:
        raise ValueError("wall_timeout_s must be positive")
    digest = hashlib.sha256()
    total = 0
    handle = await asyncio.to_thread(_open_private, dest)
    loop = asyncio.get_running_loop()
    deadline = None if wall_timeout_s is None else loop.time() + wall_timeout_s
    iterator = chunks.__aiter__()
    try:
        while True:
            budget = _read_budget(loop, deadline, chunk_timeout_s)
            if budget is not None and budget <= 0:
                raise ReceptionTimeout()
            try:
                if budget is None:
                    chunk = await anext(iterator)
                else:
                    # One deadline per read: the idle gap and the remaining wall budget.
                    async with asyncio.timeout(budget):
                        chunk = await anext(iterator)
            except StopAsyncIteration:
                break
            except TimeoutError:
                raise ReceptionTimeout() from None
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

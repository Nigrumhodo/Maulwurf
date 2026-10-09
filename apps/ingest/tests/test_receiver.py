"""S2.1: recepción a fichero privado, SHA-256 incremental y corte por bytes."""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from maulwurf_ingest.receiver import PayloadTooLarge, receive


async def _chunks(*parts: bytes) -> AsyncIterator[bytes]:
    for part in parts:
        yield part


async def test_hash_is_incremental_and_ignores_any_declared_length(tmp_path: Path) -> None:
    body = b"abc" * 100
    declared_length = 999_999
    dest = tmp_path / "original"
    result = await receive(_chunks(body[:10], body[10:]), dest, max_bytes=10_000)

    assert result.sha256 == hashlib.sha256(body).hexdigest()
    assert result.received_bytes == len(body)
    assert result.received_bytes != declared_length
    assert dest.read_bytes() == body
    if os.name == "posix":
        assert stat.S_IMODE(dest.stat().st_mode) == 0o600


async def test_over_limit_removes_the_partial_file(tmp_path: Path) -> None:
    dest = tmp_path / "original"
    with pytest.raises(PayloadTooLarge):
        await receive(_chunks(b"abcd", b"efgh"), dest, max_bytes=6)
    assert not dest.exists()

"""I-S2-SG-14 and I-S2-SG-15.

Peak RSS is sampled for two upload sizes. The absolute limit stays NO VERIFICADO.
"""

from __future__ import annotations

import os
import threading
import uuid
from collections.abc import Iterator
from pathlib import Path

import asyncpg
import pytest
from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect
from test_supervisor import Attempt, _seed  # noqa: E402

from maulwurf_ingest import admission, hardening
from maulwurf_ingest.config import settings
from maulwurf_ingest.credential import CredentialClaims, mint
from maulwurf_ingest.main import app
from maulwurf_ingest.receptions import reset_state
from maulwurf_ingest.supervisor import sweep_pass

pytestmark = pytest.mark.integration

_VECTOR_HEX = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"


async def _skip_reconcile() -> None:
    return None


def _rss() -> int:
    if os.name == "posix":
        with open("/proc/self/status", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
        raise RuntimeError("rss")
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    windll = ctypes.windll  # type: ignore[attr-defined]
    ok = windll.psapi.GetProcessMemoryInfo(
        windll.kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb,
    )
    if not ok:
        raise RuntimeError("rss")
    return int(counters.WorkingSetSize)


def _chunks(total: int) -> Iterator[bytes]:
    block = b"\x00" * 65_536
    left = total
    while left:
        take = block[:left]
        left -= len(take)
        yield take


def _peak_while(call: object) -> int:
    peak = _rss()
    stop = threading.Event()

    def sample() -> None:
        nonlocal peak
        while not stop.is_set():
            peak = max(peak, _rss())

    thread = threading.Thread(target=sample)
    thread.start()
    try:
        call()  # type: ignore[operator]
    finally:
        stop.set()
        thread.join()
    return peak


async def _ids(pool: asyncpg.Pool, attempt: Attempt) -> tuple[uuid.UUID, int]:
    row = await pool.fetchrow(
        "SELECT audio_id, fencing_token FROM ingestion_attempts WHERE id = $1",
        attempt.attempt_id,
    )
    assert row is not None
    audio_id = row["audio_id"]
    token = row["fencing_token"]
    assert isinstance(audio_id, uuid.UUID)
    assert isinstance(token, int)
    return audio_id, token


def _bearer(
    attempt: Attempt, audio_id: uuid.UUID, token: int, *, op: str, instance: str | None,
) -> str:
    claims = CredentialClaims(
        op,  # type: ignore[arg-type]
        attempt.attempt_id,
        audio_id,
        attempt.user_id,
        token,
        instance,
        4_102_444_800,
    )
    return "Bearer " + mint(claims, kid="k1", secret=bytes.fromhex(_VECTOR_HEX))


@pytest.fixture
def client(
    monkeypatch: pytest.MonkeyPatch, migrated_database_url: str,
) -> Iterator[TestClient]:
    monkeypatch.setattr("maulwurf_ingest.main.reconcile_on_startup", _skip_reconcile)
    monkeypatch.setattr(settings, "ingest_hardening", "report")
    monkeypatch.setattr(hardening, "check", lambda _facts: [])
    monkeypatch.setattr(settings, "credential_secret", _VECTOR_HEX)
    monkeypatch.setattr(settings, "credential_kid", "k1")
    monkeypatch.setattr(settings, "database_url", migrated_database_url)
    admission.reset()
    reset_state()
    with TestClient(app) as started:
        yield started
    admission.reset()
    reset_state()


async def test_peak_rss_does_not_track_the_upload(
    client: TestClient, pool: asyncpg.Pool,
) -> None:
    """Two sizes. The absolute ceiling stays NO VERIFICADO until D4."""

    async def once(size: int) -> int:
        attempt = await _seed(pool)
        audio_id, token = await _ids(pool, attempt)
        header = _bearer(attempt, audio_id, token, op="receive", instance=None)

        def upload() -> None:
            client.put(
                f"/internal/v1/receptions/{attempt.attempt_id}",
                content=_chunks(size),
                headers={"Authorization": header},
            )

        return _peak_while(upload)

    small = await once(2 * 1024 * 1024)
    large = await once(16 * 1024 * 1024)
    # A retained copy of the larger body would add about 14 MiB. Streaming does not.
    assert large - small < 8 * 1024 * 1024


async def test_over_the_limit_is_cut_and_cleaned(
    client: TestClient, pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "max_upload_bytes", 1024)
    attempt = await _seed(pool)
    audio_id, token = await _ids(pool, attempt)
    response = client.put(
        f"/internal/v1/receptions/{attempt.attempt_id}",
        content=_chunks(4096),
        headers={"Authorization": _bearer(attempt, audio_id, token, op="receive", instance=None)},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"
    row = await pool.fetchrow(
        "SELECT status, error_code FROM ingestion_attempts WHERE id = $1", attempt.attempt_id,
    )
    assert row is not None
    assert (row["status"], row["error_code"]) == ("rejected", "payload_too_large")


async def test_a_dropped_body_cancels_the_attempt(
    client: TestClient, pool: asyncpg.Pool,
) -> None:
    attempt = await _seed(pool)
    audio_id, token = await _ids(pool, attempt)

    def body() -> Iterator[bytes]:
        yield b"RIFF"
        raise ClientDisconnect()

    try:
        client.put(
            f"/internal/v1/receptions/{attempt.attempt_id}",
            content=body(),
            headers={
                "Authorization": _bearer(attempt, audio_id, token, op="receive", instance=None),
            },
        )
    except ClientDisconnect:
        pass
    row = await pool.fetchrow(
        "SELECT status, error_code FROM ingestion_attempts WHERE id = $1", attempt.attempt_id,
    )
    assert row is not None
    assert (row["status"], row["error_code"]) == ("cancelled", "reception_interrupted")


async def test_failed_cleanup_stays_closed_after_restore(pool: asyncpg.Pool) -> None:
    admission.reset()
    attempt = await _seed(pool)
    await pool.execute(
        "UPDATE ingestion_attempts SET cleanup_status = 'failed' WHERE id = $1",
        attempt.attempt_id,
    )
    admission.reset()
    assert admission.uploads_admitted() is True
    await admission.restore_from_database(pool)
    try:
        assert admission.uploads_admitted() is False
    finally:
        admission.reset()


async def test_foreign_failed_cleanup_closes_admission_on_the_next_sweep_pass(
    pool: asyncpg.Pool, tmp_path: Path,
) -> None:
    """S4.2: the periodic pass re-reads the latch, not only the process start."""
    foreign = await _seed(pool)
    await pool.execute(
        "UPDATE ingestion_attempts SET cleanup_status = 'failed' WHERE id = $1",
        foreign.attempt_id,
    )
    admission.reset()
    assert admission.uploads_admitted() is True
    try:
        await sweep_pass(pool, instance="ingest:this", tmp_dir=str(tmp_path))
        assert admission.uploads_admitted() is False
    finally:
        admission.reset()

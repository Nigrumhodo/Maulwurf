"""I-S1-SG-05 (S1.B6, G1): el lease vencido no verifica el cleanup ni cierra la admisión.

Un cleanup persistido como `failed` sí cierra la admisión de la instancia y emite la
alerta. La outbox bloqueada sin evidencia se prueba en la API (`test_cleanup_gate`).
"""
from __future__ import annotations

import logging
import os
import shutil
import uuid
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import asyncpg
import pytest
from test_supervisor import (  # noqa: E402
    _config,
    _row,
    _seed,
    audio,
    tmpfs_dir,
)

from maulwurf_ingest import admission, lease
from maulwurf_ingest.asr_job import ORIGINAL
from maulwurf_ingest.supervisor import Outcome, run_attempt

_ = (audio, tmpfs_dir)


@pytest.fixture(autouse=True)
def _reset_admission() -> Iterator[None]:
    admission.reset()
    yield
    admission.reset()


def test_halt_closes_admission_until_reset() -> None:
    attempt_id = uuid.uuid4()
    admission.halt(attempt_id)

    assert admission.uploads_admitted() is False
    assert admission.alerts() == (admission.Alert("cleanup_failed", attempt_id),)


@pytest.mark.integration
async def test_expired_lease_stays_pending_and_admits(pool: asyncpg.Pool) -> None:
    """I-S1-SG-05: vencer el lease no marca cleanup verificado ni detiene la admisión."""
    attempt = await _seed(pool)
    owned = await lease.acquire(
        pool, user_id=attempt.user_id, attempt_id=attempt.attempt_id, instance="ingest:a",
        ttl=timedelta(seconds=30),
    )
    assert owned is not None
    await pool.execute(
        "UPDATE ingestion_attempts SET lease_expires_at = now() - interval '1 second'"
        " WHERE id = $1",
        attempt.attempt_id,
    )

    assert await lease.heartbeat(pool, owned) is False
    assert await lease.transition(
        pool, owned, from_status="awaiting_upload", to_status="receiving"
    ) is False
    row = await _row(pool, attempt)

    assert row["cleanup_status"] == "pending"
    assert row["cleanup_verified_at"] is None and row["audio_deleted_at"] is None
    assert admission.uploads_admitted() is True
    assert admission.alerts() == ()


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg no disponible")
async def test_cleanup_failure_halts_admission_and_alerts(
    pool: asyncpg.Pool, audio: bytes, tmpfs_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """I-S1-SG-05: cleanup fallido cierra la admisión y dispara la alerta, sin rutas."""
    attempt = await _seed(pool)
    held: list[int] = []

    def _hold(_pid: int, workdir: Path) -> None:
        held.append(os.open(workdir / ORIGINAL, os.O_RDONLY))

    try:
        with caplog.at_level(logging.ERROR, logger="maulwurf_ingest.admission"):
            result = await run_attempt(
                pool, user_id=attempt.user_id, attempt_id=attempt.attempt_id, audio=audio,
                config=_config(tmpfs_dir), on_child=_hold,
            )
    finally:
        for fd in held:
            os.close(fd)
    row = await _row(pool, attempt)

    assert result.outcome is Outcome.CLEANUP_FAILED
    assert row["cleanup_status"] == "failed"
    assert row["cleanup_verified_at"] is None
    assert admission.uploads_admitted() is False
    assert admission.alerts() == (admission.Alert("cleanup_failed", attempt.attempt_id),)
    messages = [record.getMessage() for record in caplog.records]
    assert any("code=cleanup_failed" in message for message in messages)
    assert all(str(tmpfs_dir) not in message for message in messages)
    assert all("RIFF" not in message for message in messages)

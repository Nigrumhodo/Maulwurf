"""I-S1-SG-04 (S1.B4): nine failure scenarios on the S1.B3 supervisor.

Audio is synthetic and stays in RAM except for the attempt directory on tmpfs, which
each scenario must leave empty. Scenarios 8 and 9 do not reboot the container or the
host: a killed supervisor loses its tmpfs, and the durable row stays unverified.
"""

from __future__ import annotations

import asyncio
import multiprocessing
import os
import shutil
import signal
import uuid
from datetime import timedelta
from pathlib import Path

import asyncpg
import pytest
from test_supervisor import (  # noqa: E402
    _config,
    _row,
    _seed,
    _wait_for,
    audio,
    tmpfs_dir,
)

from maulwurf_ingest import lease
from maulwurf_ingest.asr_job import ACTIVE_FRAGMENT
from maulwurf_ingest.supervisor import Outcome, SupervisorConfig, run_attempt

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg no disponible"),
]

# Re-export fixtures so this module collects them.
_ = (audio, tmpfs_dir)


def _gone(tmpfs_dir: Path, workdir: Path | None) -> None:
    assert workdir is None or not workdir.exists()
    assert list(tmpfs_dir.iterdir()) == []


async def test_scenario_1_success_commits_then_cleans(
    pool: asyncpg.Pool, audio: bytes, tmpfs_dir: Path
) -> None:
    """After the commit, cleanup is verified and the attempt directory is gone."""
    attempt = await _seed(pool)
    result = await run_attempt(
        pool, user_id=attempt.user_id, attempt_id=attempt.attempt_id, audio=audio,
        config=_config(tmpfs_dir),
    )
    row = await _row(pool, attempt)
    assert result.outcome is Outcome.SUCCEEDED
    assert row["status"] == "succeeded"
    assert row["fragments_done"] == result.fragments
    assert row["cleanup_status"] == "verified"
    assert result.evidence["verified"] is True
    _gone(tmpfs_dir, None)


async def test_scenario_2_truncated_upload_is_rejected(
    pool: asyncpg.Pool, audio: bytes, tmpfs_dir: Path
) -> None:
    attempt = await _seed(pool)
    result = await run_attempt(
        pool, user_id=attempt.user_id, attempt_id=attempt.attempt_id,
        audio=audio[:32], config=_config(tmpfs_dir),
    )
    row = await _row(pool, attempt)
    assert result.outcome is Outcome.REJECTED
    assert (row["status"], row["error_code"]) == ("rejected", "malformed")
    assert row["cleanup_status"] == "verified"
    _gone(tmpfs_dir, None)


async def test_scenario_3_rejected_container(
    pool: asyncpg.Pool, tmpfs_dir: Path
) -> None:
    attempt = await _seed(pool)
    result = await run_attempt(
        pool, user_id=attempt.user_id, attempt_id=attempt.attempt_id,
        audio=b"this-is-not-a-media-container", config=_config(tmpfs_dir),
    )
    row = await _row(pool, attempt)
    assert result.outcome is Outcome.REJECTED
    assert (row["status"], row["error_code"]) == ("rejected", "malformed")
    assert row["cleanup_status"] == "verified"
    _gone(tmpfs_dir, None)


async def test_scenario_4_timeout(
    pool: asyncpg.Pool, audio: bytes, tmpfs_dir: Path
) -> None:
    attempt = await _seed(pool)
    result = await run_attempt(
        pool, user_id=attempt.user_id, attempt_id=attempt.attempt_id, audio=audio,
        config=_config(tmpfs_dir, realtime=True, asr_timeout_s=0.4),
    )
    row = await _row(pool, attempt)
    assert result.outcome is Outcome.TIMEOUT
    assert (row["status"], row["error_code"]) == ("requires_reupload", "asr_timeout")
    assert row["cleanup_status"] == "verified"
    _gone(tmpfs_dir, None)


async def test_scenario_5_explicit_cancel(
    pool: asyncpg.Pool, audio: bytes, tmpfs_dir: Path
) -> None:
    attempt = await _seed(pool)
    started: asyncio.Queue[tuple[int, Path]] = asyncio.Queue()
    run = asyncio.create_task(run_attempt(
        pool, user_id=attempt.user_id, attempt_id=attempt.attempt_id, audio=audio,
        config=_config(tmpfs_dir, realtime=True),
        on_child=lambda pid, workdir: started.put_nowait((pid, workdir)),
    ))
    _pid, workdir = await started.get()
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run
    row = await _row(pool, attempt)
    assert row["status"] != "succeeded"
    assert row["cleanup_status"] == "verified"
    _gone(tmpfs_dir, workdir)


async def test_scenario_6_ffmpeg_and_invalid_lease(
    pool: asyncpg.Pool, audio: bytes, tmpfs_dir: Path
) -> None:
    """Un contenedor inválido queda rejected y limpio. Un lease vencido no publica el commit."""
    broken = await _seed(pool)
    failed = await run_attempt(
        pool, user_id=broken.user_id, attempt_id=broken.attempt_id,
        audio=b"not-ffmpeg-input", config=_config(tmpfs_dir),
    )
    failed_row = await _row(pool, broken)
    assert failed.outcome is Outcome.REJECTED
    assert (failed_row["status"], failed_row["error_code"]) == ("rejected", "malformed")
    assert failed_row["cleanup_status"] == "verified"
    _gone(tmpfs_dir, None)

    attempt = await _seed(pool)
    started: asyncio.Queue[Path] = asyncio.Queue()
    run = asyncio.create_task(run_attempt(
        pool, user_id=attempt.user_id, attempt_id=attempt.attempt_id, audio=audio,
        config=_config(tmpfs_dir, realtime=True, heartbeat_interval_s=0.2),
        on_child=lambda _pid, workdir: started.put_nowait(workdir),
    ))
    workdir = await started.get()
    await _wait_for(workdir / ACTIVE_FRAGMENT)
    await pool.execute(
        "UPDATE ingestion_attempts SET lease_expires_at = now() - interval '1 second'"
        " WHERE id = $1",
        attempt.attempt_id,
    )
    taken = await lease.acquire(
        pool, user_id=attempt.user_id, attempt_id=attempt.attempt_id,
        instance="ingest:b", ttl=timedelta(seconds=30),
    )
    assert taken is not None
    result = await run
    row = await _row(pool, attempt)
    assert result.outcome is Outcome.LEASE_LOST
    assert row["status"] != "succeeded"
    assert row["cleanup_status"] == "pending"
    _gone(tmpfs_dir, workdir)


async def test_scenario_7_sigkill_during_transcription(
    pool: asyncpg.Pool, audio: bytes, tmpfs_dir: Path
) -> None:
    attempt = await _seed(pool)
    started: asyncio.Queue[tuple[int, Path]] = asyncio.Queue()
    run = asyncio.create_task(run_attempt(
        pool, user_id=attempt.user_id, attempt_id=attempt.attempt_id, audio=audio,
        config=_config(tmpfs_dir, realtime=True),
        on_child=lambda pid, workdir: started.put_nowait((pid, workdir)),
    ))
    pid, workdir = await started.get()
    await _wait_for(workdir / ACTIVE_FRAGMENT)
    os.kill(pid, signal.SIGKILL)
    result = await run
    row = await _row(pool, attempt)
    assert result.outcome is Outcome.ASR_FAILED
    assert row["status"] == "requires_reupload"
    assert row["cleanup_status"] == "verified"
    _gone(tmpfs_dir, workdir)


async def test_scenario_7_after_commit_open_descriptor_is_not_verified(
    pool: asyncpg.Pool, audio: bytes, tmpfs_dir: Path
) -> None:
    """The child has exited by commit time. An open descriptor keeps the audio reachable."""
    attempt = await _seed(pool)
    held: list[int] = []
    try:
        result = await run_attempt(
            pool, user_id=attempt.user_id, attempt_id=attempt.attempt_id, audio=audio,
            config=_config(tmpfs_dir),
            on_child=lambda _pid, workdir: held.append(os.open(workdir / "original", os.O_RDONLY)),
        )
    finally:
        for fd in held:
            os.close(fd)
    row = await _row(pool, attempt)
    assert result.outcome is Outcome.CLEANUP_FAILED
    assert row["status"] == "transcript_committed_cleanup_pending"
    assert row["cleanup_status"] == "failed"
    assert row["audio_deleted_at"] is None
    _gone(tmpfs_dir, None)


def _killed_supervisor(
    dsn: str, user_id: str, attempt_id: str, audio: bytes, tmp: str
) -> None:
    async def go() -> None:
        created = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
        assert created is not None
        try:
            await run_attempt(
                created,
                user_id=uuid.UUID(user_id),
                attempt_id=uuid.UUID(attempt_id),
                audio=audio,
                config=SupervisorConfig(
                    instance="ingest:killed",
                    lease_ttl=timedelta(seconds=30),
                    heartbeat_interval_s=5.0,
                    asr_timeout_s=60.0,
                    tmp_dir=tmp,
                    realtime=True,
                ),
            )
        finally:
            await created.close()

    asyncio.run(go())


async def test_scenario_8_killed_supervisor_does_not_verify_cleanup(
    pool: asyncpg.Pool, migrated_database_url: str, audio: bytes, tmpfs_dir: Path
) -> None:
    if os.name != "posix":
        pytest.skip("SIGKILL al proceso supervisor es POSIX")
    attempt = await _seed(pool)
    proc = multiprocessing.get_context("spawn").Process(
        target=_killed_supervisor,
        args=(
            migrated_database_url.replace("postgresql+asyncpg://", "postgresql://", 1),
            str(attempt.user_id),
            str(attempt.attempt_id),
            audio,
            str(tmpfs_dir),
        ),
    )
    proc.start()
    deadline = asyncio.get_running_loop().time() + 20
    while not any(tmpfs_dir.iterdir()):
        assert asyncio.get_running_loop().time() < deadline, "el supervisor no creó el directorio"
        await asyncio.sleep(0.05)
    assert proc.pid is not None
    os.kill(proc.pid, signal.SIGKILL)
    proc.join(timeout=5)
    for child in tmpfs_dir.iterdir():
        shutil.rmtree(child, ignore_errors=True)
    row = await _row(pool, attempt)
    assert row["status"] != "succeeded"
    assert row["cleanup_status"] != "verified"
    assert row["audio_deleted_at"] is None
    _gone(tmpfs_dir, None)


async def test_scenario_9_expired_lease_without_reboot(
    pool: asyncpg.Pool,
) -> None:
    """Host reboot is not executed. The durable contract matches scenario 8."""
    attempt = await _seed(pool)
    owned = await lease.acquire(
        pool, user_id=attempt.user_id, attempt_id=attempt.attempt_id,
        instance="ingest:a", ttl=timedelta(seconds=30),
    )
    assert owned is not None
    assert await lease.transition(
        pool, owned, from_status="awaiting_upload", to_status="receiving"
    )
    await pool.execute(
        "UPDATE ingestion_attempts SET lease_expires_at = now() - interval '1 second'"
        " WHERE id = $1",
        attempt.attempt_id,
    )
    row = await _row(pool, attempt)
    assert row["status"] == "receiving"
    assert row["cleanup_status"] == "pending"
    assert row["cleanup_verified_at"] is None and row["audio_deleted_at"] is None

"""I-S1-SG-04/05 (parte S1.B3): supervisor, lease, fencing y limpieza con ffmpeg real.

El audio es sintético y se genera en RAM (espeak-ng; si no está, un tono de ffmpeg). Solo
existe como fichero dentro del directorio del intento en un tmpfs, y cada test comprueba
que ese directorio desaparece. La matriz completa de fallos es S1.B4.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import signal
import subprocess
import tempfile
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import asyncpg
import pytest

from maulwurf_ingest import hardening, lease, workspace
from maulwurf_ingest.asr_job import CONVERTED, ORIGINAL
from maulwurf_ingest.supervisor import Outcome, SupervisorConfig, run_attempt

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg no disponible"),
]

SYNTHETIC_TEXT = (
    "Hoy repasamos derivadas. La tarea es para el viernes y el examen es el lunes siguiente."
)


def _synthetic_audio() -> bytes:
    if shutil.which("espeak-ng") or shutil.which("espeak"):
        from maulwurf_ingest.audio.synthetic import generate_synthetic_utterance

        return generate_synthetic_utterance(text=SYNTHETIC_TEXT, seed=7).wav_bytes
    tone = subprocess.run(  # noqa: S603 — argv fija, sin shell
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi",  # noqa: S607
         "-i", "sine=frequency=440:duration=6", "-f", "wav", "pipe:1"],
        capture_output=True, timeout=30, check=True,
    )
    return tone.stdout


@pytest.fixture(scope="module")
def audio() -> bytes:
    return _synthetic_audio()


@pytest.fixture
def tmpfs_dir() -> Iterator[Path]:
    mounts = hardening.parse_mountinfo(hardening.MOUNTINFO.read_text())
    for candidate in (tempfile.gettempdir(), "/dev/shm"):  # noqa: S108
        mount = hardening.mount_for(mounts, candidate)
        if mount is not None and mount.fstype == "tmpfs" and os.access(candidate, os.W_OK):
            root = Path(tempfile.mkdtemp(prefix="mw-b3-", dir=candidate))
            break
    else:
        pytest.skip("no hay un tmpfs escribible")
    try:
        yield root
        assert list(root.iterdir()) == [], "quedaron temporales del intento"
    finally:
        shutil.rmtree(root, ignore_errors=True)


@dataclass(frozen=True)
class Attempt:
    user_id: uuid.UUID
    attempt_id: uuid.UUID


async def _seed(pool: asyncpg.Pool) -> Attempt:
    """Usuario, materia, audio e intento `awaiting_upload` como los deja POST /audios."""
    async with pool.acquire() as conn, conn.transaction():
        user_id = await conn.fetchval(
            "INSERT INTO users (google_sub, email) VALUES ($1, $2) RETURNING id",
            f"sub-{uuid.uuid4().hex}", "alumno@example.test",
        )
        subject_id = await conn.fetchval(
            "INSERT INTO subjects (user_id, name) VALUES ($1, 'Cálculo') RETURNING id", user_id
        )
        audio_id = await conn.fetchval(
            """INSERT INTO audios (user_id, subject_id, class_date, class_timezone, language_code)
               VALUES ($1, $2, DATE '2026-09-21', 'America/Bogota', 'es') RETURNING id""",
            user_id, subject_id,
        )
        attempt_id = await conn.fetchval(
            """INSERT INTO ingestion_attempts
                 (user_id, audio_id, privacy_notice_version, cloud_processing_accepted_at,
                  third_party_voice_acknowledged_at, declared_providers, status,
                  owner_instance, fencing_token, upload_expires_at)
               VALUES ($1, $2, 'v1', now(), now(), '{"asr": "nvidia-riva"}',
                       'awaiting_upload', 'api:unassigned', 1, now() + interval '15 minutes')
               RETURNING id""",
            user_id, audio_id,
        )
    return Attempt(user_id, attempt_id)


async def _row(pool: asyncpg.Pool, attempt: Attempt) -> asyncpg.Record:
    row = await pool.fetchrow(
        """SELECT status, error_code, cleanup_status, cleanup_verified_at, audio_deleted_at,
                  cleanup_evidence, fencing_token, owner_instance, fragments_done
             FROM ingestion_attempts WHERE id = $1""",
        attempt.attempt_id,
    )
    assert row is not None
    return row


def _config(tmpfs_dir: Path, **overrides: object) -> SupervisorConfig:
    values: dict[str, object] = {
        "instance": "ingest:a", "lease_ttl": timedelta(seconds=5),
        "heartbeat_interval_s": 0.5, "asr_timeout_s": 60, "tmp_dir": str(tmpfs_dir),
    }
    values.update(overrides)
    return SupervisorConfig(**values)  # type: ignore[arg-type]


async def _wait_for(path: Path, timeout_s: float = 20) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while not path.exists():
        assert asyncio.get_running_loop().time() < deadline, "ffmpeg no empezó a escribir"
        await asyncio.sleep(0.02)


async def test_success_commits_then_verifies_cleanup(
    pool: asyncpg.Pool, audio: bytes, tmpfs_dir: Path
) -> None:
    attempt = await _seed(pool)
    result = await run_attempt(
        pool, user_id=attempt.user_id, attempt_id=attempt.attempt_id, audio=audio,
        config=_config(tmpfs_dir),
    )
    row = await _row(pool, attempt)

    assert result.outcome is Outcome.SUCCEEDED
    assert result.fragments and result.fragments >= 3  # ffmpeg partió el audio en fragmentos
    assert row["status"] == "succeeded"
    assert row["cleanup_status"] == "verified"
    assert row["cleanup_verified_at"] is not None and row["audio_deleted_at"] is not None
    assert row["fencing_token"] == 2  # 1 al crear (A1.8) + 1 al tomar el lease
    assert row["fragments_done"] == result.fragments
    assert result.evidence == {
        "workdir_absent": True, "tmp_is_tmpfs": True, "live_processes": 0,
        "open_descriptors": 0, "mounts_under_workdir": 0,
        "unreadable_processes": result.evidence["unreadable_processes"], "verified": True,
    }


async def test_sigkill_to_asr_process_still_cleans_up(
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
    await _wait_for(workdir / CONVERTED)
    # Hijo y nieto (ffmpeg) vivos con ficheros abiertos; se mata SOLO al hijo: ffmpeg queda
    # huérfano y el supervisor tiene que encontrarlo por su grupo.
    assert workspace.live_group_members(pid) >= 2
    os.kill(pid, signal.SIGKILL)
    result = await run
    row = await _row(pool, attempt)

    assert result.outcome is Outcome.ASR_FAILED
    assert (row["status"], row["error_code"]) == ("requires_reupload", "asr_failed")
    assert row["cleanup_status"] == "verified"
    assert workspace.live_group_members(pid) == 0
    assert not workdir.exists()


async def test_timeout_kills_the_job_and_cleans_up(
    pool: asyncpg.Pool, audio: bytes, tmpfs_dir: Path
) -> None:
    attempt = await _seed(pool)
    result = await run_attempt(
        pool, user_id=attempt.user_id, attempt_id=attempt.attempt_id, audio=audio,
        config=_config(tmpfs_dir, realtime=True, asr_timeout_s=0.5),
    )
    row = await _row(pool, attempt)

    assert result.outcome is Outcome.TIMEOUT
    assert (row["status"], row["error_code"]) == ("requires_reupload", "asr_timeout")
    assert row["cleanup_status"] == "verified"


async def test_open_descriptor_to_deleted_audio_is_not_verified(
    pool: asyncpg.Pool, audio: bytes, tmpfs_dir: Path
) -> None:
    # El directorio desaparece, pero un descriptor abierto mantiene el audio en RAM y
    # legible: la verificación tiene que verlo en /proc y no marcar `verified`.
    attempt = await _seed(pool)
    held: list[int] = []
    try:
        result = await run_attempt(
            pool, user_id=attempt.user_id, attempt_id=attempt.attempt_id, audio=audio,
            config=_config(tmpfs_dir),
            on_child=lambda _pid, workdir: held.append(os.open(workdir / ORIGINAL, os.O_RDONLY)),
        )
    finally:
        for fd in held:
            os.close(fd)
    row = await _row(pool, attempt)

    assert result.outcome is Outcome.CLEANUP_FAILED
    assert result.evidence["workdir_absent"] is True
    assert result.evidence["open_descriptors"] == 1
    assert row["cleanup_status"] == "failed"
    assert row["cleanup_verified_at"] is None and row["audio_deleted_at"] is None
    assert row["status"] == "transcript_committed_cleanup_pending"  # nunca `succeeded`


async def test_fenced_owner_cannot_publish_and_expiry_does_not_verify(
    pool: asyncpg.Pool, audio: bytes, tmpfs_dir: Path
) -> None:
    attempt = await _seed(pool)
    started: asyncio.Queue[tuple[int, Path]] = asyncio.Queue()
    run = asyncio.create_task(run_attempt(
        pool, user_id=attempt.user_id, attempt_id=attempt.attempt_id, audio=audio,
        config=_config(tmpfs_dir, realtime=True, heartbeat_interval_s=0.2),
        on_child=lambda pid, workdir: started.put_nowait((pid, workdir)),
    ))
    _, workdir = await started.get()
    await _wait_for(workdir / CONVERTED)

    # El lease de A vence (p. ej. BD lenta) y la instancia B toma el intento.
    await pool.execute(
        "UPDATE ingestion_attempts SET lease_expires_at = now() - interval '1 second'"
        " WHERE id = $1", attempt.attempt_id,
    )
    taken = await lease.acquire(
        pool, user_id=attempt.user_id, attempt_id=attempt.attempt_id, instance="ingest:b",
        ttl=timedelta(seconds=30),
    )
    assert taken is not None
    result = await run
    row = await _row(pool, attempt)

    assert result.outcome is Outcome.LEASE_LOST
    assert taken.fencing_token == 3 and row["fencing_token"] == 3
    assert row["owner_instance"] == "ingest:b"
    assert row["status"] == "transcribing"  # A no publicó nada tras perder el lease
    # A limpió su tmpfs, pero no puede escribir la evidencia de un intento que ya no es
    # suyo; y que el lease venciera no marca nada: sigue `pending`.
    assert result.evidence["verified"] is True
    assert row["cleanup_status"] == "pending"
    assert not workdir.exists()


async def test_expired_lease_blocks_publication_but_not_cleanup_evidence(
    pool: asyncpg.Pool,
) -> None:
    attempt = await _seed(pool)
    owned = await lease.acquire(
        pool, user_id=attempt.user_id, attempt_id=attempt.attempt_id, instance="ingest:a",
        ttl=timedelta(seconds=30),
    )
    assert owned is not None
    await pool.execute(
        "UPDATE ingestion_attempts SET lease_expires_at = now() - interval '1 second'"
        " WHERE id = $1", attempt.attempt_id,
    )

    assert await lease.heartbeat(pool, owned) is False
    assert await lease.transition(
        pool, owned, from_status="awaiting_upload", to_status="receiving"
    ) is False
    assert (await _row(pool, attempt))["cleanup_status"] == "pending"
    # Nadie tomó el intento: la evidencia real de limpieza sí se puede registrar.
    assert await lease.record_cleanup(
        pool, owned, verified=True, evidence={"verified": True},
        checked_at=await pool.fetchval("SELECT now()"),
    )


async def test_only_one_supervisor_acquires_the_lease(pool: asyncpg.Pool) -> None:
    attempt = await _seed(pool)
    results = await asyncio.gather(*(
        lease.acquire(
            pool, user_id=attempt.user_id, attempt_id=attempt.attempt_id, instance=f"ingest:{n}",
            ttl=timedelta(seconds=30),
        )
        for n in range(4)
    ))
    winners = [r for r in results if r is not None]
    assert len(winners) == 1
    assert winners[0].fencing_token == 2


async def test_lease_is_scoped_to_the_owning_user(pool: asyncpg.Pool) -> None:
    attempt = await _seed(pool)
    other = await _seed(pool)
    assert await lease.acquire(
        pool, user_id=other.user_id, attempt_id=attempt.attempt_id, instance="ingest:a",
        ttl=timedelta(seconds=30),
    ) is None

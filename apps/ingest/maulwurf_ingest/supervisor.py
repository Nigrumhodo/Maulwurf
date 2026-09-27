"""Supervisor local por intento (S1.B3): lease, trabajo ASR aislado y limpieza propia.

Flujo de un intento (S1.md §1.4):

    acquire -> receiving (original al tmpfs) -> transcribing (proceso ASR con ffmpeg)
            -> transcript_committed_cleanup_pending -> [limpieza verificada] -> succeeded

La limpieza no depende del proceso ASR: la hace el supervisor en `finally`, pase lo que
pase con el hijo (éxito, error, timeout, SIGKILL, lease perdido). Mata el grupo entero,
borra el directorio y verifica en `/proc` descriptores, procesos y mounts; solo con esa
evidencia registra `cleanup_status = verified`. Si la evidencia falla, queda `failed` y el
intento no pasa a `succeeded` (S1.B6 decide la alerta y la admisión).

Si el supervisor muere entero (SIGKILL al contenedor), el tmpfs desaparece con el
contenedor (S1.B1) y el intento queda con lease vencido y `cleanup_status = pending`: la
reconciliación de ese caso es S1.B4/S1.B6.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path

import asyncpg

from maulwurf_ingest import lease as leases
from maulwurf_ingest import workspace
from maulwurf_ingest.asr_job import ORIGINAL

logger = logging.getLogger(__name__)


class Outcome(StrEnum):
    SUCCEEDED = "succeeded"
    NOT_ACQUIRED = "not_acquired"  # otro propietario tiene un lease vigente
    LEASE_LOST = "lease_lost"  # fenced: no se publicó nada después de perderlo
    ASR_FAILED = "asr_failed"
    TIMEOUT = "asr_timeout"
    CLEANUP_FAILED = "cleanup_failed"


@dataclass(frozen=True)
class SupervisorConfig:
    instance: str
    lease_ttl: timedelta = timedelta(seconds=30)
    heartbeat_interval_s: float = 10.0
    asr_timeout_s: float = 600.0
    tmp_dir: str | None = None  # None = TMPDIR (el tmpfs del contenedor)
    realtime: bool = False  # solo pruebas: ffmpeg a velocidad real


@dataclass
class RunResult:
    outcome: Outcome
    fencing_token: int | None = None
    fragments: int | None = None
    evidence: dict[str, object] = field(default_factory=dict)


# Gancho de pruebas: recibe (pid del trabajo ASR, directorio) al arrancar el hijo.
ChildHook = Callable[[int, Path], None]


class _LeaseKeeper:
    """Renueva el lease; si una renovación falla, avisa para cortar el trabajo."""

    def __init__(self, pool: asyncpg.Pool, lease: leases.Lease, interval_s: float) -> None:
        self.lost = asyncio.Event()
        self._task = asyncio.create_task(self._loop(pool, lease, interval_s))

    async def _loop(self, pool: asyncpg.Pool, lease: leases.Lease, interval_s: float) -> None:
        while True:
            await asyncio.sleep(interval_s)
            try:
                alive = await leases.heartbeat(pool, lease)
            except (OSError, asyncpg.PostgresError) as exc:
                logger.warning("supervisor.heartbeat_error error=%s", type(exc).__name__)
                alive = False
            if not alive:
                self.lost.set()
                return

    async def stop(self) -> None:
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task


@dataclass
class _Job:
    pgid: int | None = None  # se fija nada más arrancar: la limpieza lo necesita siempre


async def _run_asr(
    workdir: Path, config: SupervisorConfig, keeper: _LeaseKeeper, on_child: ChildHook | None,
    job: _Job,
) -> tuple[Outcome | None, int | None]:
    """Ejecuta el trabajo ASR; devuelve (fallo o None, fragmentos)."""
    argv = [sys.executable, "-m", "maulwurf_ingest.asr_job", str(workdir)]
    if config.realtime:
        argv.append("--realtime")
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,  # grupo propio: el supervisor puede matar hijo y nietos
    )
    job.pgid = proc.pid  # con start_new_session el pgid es el pid del hijo
    if on_child is not None:
        on_child(proc.pid, workdir)

    output = asyncio.create_task(proc.communicate())
    lost = asyncio.create_task(keeper.lost.wait())
    done, _ = await asyncio.wait(
        {output, lost}, timeout=config.asr_timeout_s, return_when=asyncio.FIRST_COMPLETED
    )
    lost.cancel()
    if output not in done:
        workspace.kill_group(proc.pid)
        await output
        return (Outcome.LEASE_LOST if lost in done else Outcome.TIMEOUT), None

    stdout, _ = output.result()
    if proc.returncode != 0:
        return Outcome.ASR_FAILED, None
    return None, int(json.loads(stdout)["fragments"])


async def run_attempt(
    pool: asyncpg.Pool, *, user_id: uuid.UUID, attempt_id: uuid.UUID, audio: bytes,
    config: SupervisorConfig, on_child: ChildHook | None = None,
) -> RunResult:
    lease = await leases.acquire(
        pool, user_id=user_id, attempt_id=attempt_id, instance=config.instance,
        ttl=config.lease_ttl,
    )
    if lease is None:
        return RunResult(Outcome.NOT_ACQUIRED)

    result = RunResult(Outcome.LEASE_LOST, fencing_token=lease.fencing_token)
    keeper = _LeaseKeeper(pool, lease, config.heartbeat_interval_s)
    workdir = workspace.create_workdir(str(attempt_id), config.tmp_dir)
    job = _Job()
    committed = False
    try:
        if not await leases.transition(
            pool, lease, from_status="awaiting_upload", to_status="receiving"
        ):
            return result
        workspace.write_private(workdir / ORIGINAL, audio)
        if not await leases.transition(
            pool, lease, from_status="receiving", to_status="transcribing"
        ):
            return result

        failure, fragments = await _run_asr(workdir, config, keeper, on_child, job)
        if failure is not None:
            result.outcome = failure
            if failure is not Outcome.LEASE_LOST:
                await leases.transition(
                    pool, lease, from_status="transcribing", to_status="requires_reupload",
                    error_code=failure.value,
                )
            return result

        # "Commit" de S1: aún no hay tabla de transcript (A2.1); se publican los conteos.
        committed = await leases.transition(
            pool, lease, from_status="transcribing",
            to_status="transcript_committed_cleanup_pending",
            fragments_done=fragments, fragments_total=fragments,
        )
        result.fragments = fragments
        result.outcome = Outcome.SUCCEEDED if committed else Outcome.LEASE_LOST
        return result
    finally:
        # Independiente del hijo y del lease: siempre se limpia y se verifica.
        evidence = await asyncio.to_thread(workspace.destroy_and_verify, workdir, job.pgid)
        result.evidence = evidence.as_json()
        try:
            recorded = await leases.record_cleanup(
                pool, lease, verified=evidence.verified, evidence=result.evidence,
                checked_at=datetime.now(UTC),
            )
            if not evidence.verified:
                logger.error("supervisor.cleanup_failed attempt_id=%s", attempt_id)
                if result.outcome is Outcome.SUCCEEDED:
                    result.outcome = Outcome.CLEANUP_FAILED
            elif committed and not (
                recorded
                and await leases.transition(
                    pool, lease, from_status="transcript_committed_cleanup_pending",
                    to_status="succeeded",
                )
            ):
                result.outcome = Outcome.LEASE_LOST
        finally:
            await keeper.stop()

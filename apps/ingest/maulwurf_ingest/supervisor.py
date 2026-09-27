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
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum
from pathlib import Path

import asyncpg

from maulwurf_ingest import cleanup
from maulwurf_ingest import lease as leases
from maulwurf_ingest.asr_job import ORIGINAL

logger = logging.getLogger(__name__)


class Outcome(StrEnum):
    SUCCEEDED = "succeeded"
    NOT_ACQUIRED = "not_acquired"  # otro propietario tiene un lease vigente
    LEASE_LOST = "lease_lost"  # fenced: no se publicó nada después de perderlo
    ASR_FAILED = "asr_failed"
    TIMEOUT = "asr_timeout"
    CLEANUP_FAILED = "cleanup_failed"
    SUPERVISOR_ERROR = "supervisor_error"  # fallo imprevisto del propio supervisor


@dataclass(frozen=True)
class SupervisorConfig:
    instance: str
    lease_ttl: timedelta = timedelta(seconds=30)
    heartbeat_interval_s: float = 10.0
    asr_timeout_s: float = 600.0
    tmp_dir: str | None = None  # None = TMPDIR (el tmpfs del contenedor)
    realtime: bool = False  # solo pruebas: ffmpeg a velocidad real
    fragment_seconds: int = 1  # stub de S1; afinar con límites reales antes del ASR de S2


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
        # Un error de conexión no pierde el lease por sí solo: se tolera dentro del
        # presupuesto del propio TTL (sin números nuevos). Un `False` definitivo
        # (lease perdido o vencido) corta igual.
        grace_deadline = time.monotonic() + lease.ttl.total_seconds()
        while True:
            await asyncio.sleep(interval_s)
            try:
                alive = await leases.heartbeat(pool, lease)
                grace_deadline = time.monotonic() + lease.ttl.total_seconds()
            except (OSError, asyncpg.PostgresError) as exc:
                logger.warning("supervisor.heartbeat_error error=%s", type(exc).__name__)
                alive = time.monotonic() < grace_deadline
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
    starttime: int | None = None  # para descartar el reciclado teórico del pgid


async def _run_asr(
    workdir: Path, config: SupervisorConfig, keeper: _LeaseKeeper, on_child: ChildHook | None,
    job: _Job,
) -> tuple[Outcome | None, int | None]:
    """Ejecuta el trabajo ASR; devuelve (fallo o None, fragmentos)."""
    argv = [sys.executable, "-m", "maulwurf_ingest.asr_job", str(workdir),
            "--fragment-seconds", str(config.fragment_seconds)]
    if config.realtime:
        argv.append("--realtime")
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,  # grupo propio: el supervisor puede matar hijo y nietos
    )
    job.pgid = proc.pid  # con start_new_session el pgid es el pid del hijo
    job.starttime = cleanup.start_time(proc.pid)
    if on_child is not None:
        on_child(proc.pid, workdir)

    output = asyncio.create_task(proc.communicate())
    lost = asyncio.create_task(keeper.lost.wait())
    done, _ = await asyncio.wait(
        {output, lost}, timeout=config.asr_timeout_s, return_when=asyncio.FIRST_COMPLETED
    )
    lost.cancel()
    if output not in done:
        cleanup.kill_group(proc.pid, job.starttime)
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
    # El workdir se crea ANTES del keeper: si mkdtemp falla (tmpfs lleno) no debe quedar
    # una tarea de heartbeat renovando para siempre el lease de un run que ya no existe.
    workdir = cleanup.create_workdir(str(attempt_id), config.tmp_dir)
    keeper = _LeaseKeeper(pool, lease, config.heartbeat_interval_s)
    job = _Job()
    committed = False
    owned_audio = False  # solo el run que escribió el audio puede acreditar su limpieza
    try:
        if not await leases.transition(
            pool, lease, from_status="awaiting_upload", to_status="receiving"
        ):
            return result
        # Hasta upload_max_bytes (200 MiB): fuera del event loop, como destroy_and_verify.
        await asyncio.to_thread(cleanup.write_private, workdir / ORIGINAL, audio)
        owned_audio = True
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
    except asyncio.CancelledError:
        raise
    except Exception:
        # Fallo imprevisto (BD caída, tmpfs lleno, JSON malformado): si el lease sigue
        # vigente, dejar el intento en requires_reupload en vez de depender solo de la
        # reconciliación de B4/B6. transition() ya exige token y lease vigente.
        logger.exception("supervisor.unexpected_error attempt_id=%s", attempt_id)
        result.outcome = Outcome.SUPERVISOR_ERROR
        for prev in ("awaiting_upload", "receiving", "transcribing"):
            if await leases.transition(
                pool, lease, from_status=prev, to_status="requires_reupload",
                error_code=Outcome.SUPERVISOR_ERROR.value,
            ):
                break
        return result
    finally:
        # `to_thread` solo encola el borrado. En CPython 3.12 una sola
        # cancelación no relanza dentro del finally; una segunda puede cortar
        # el await antes de que el hilo arranque y dejar el audio en tmpfs.
        # Una tarea propia con shield termina destroy_and_verify aunque
        # cancelen otra vez.
        cleanup_task = asyncio.create_task(
            asyncio.to_thread(
                cleanup.destroy_and_verify, workdir, job.pgid, starttime=job.starttime
            )
        )
        evidence = None
        try:
            evidence = await asyncio.shield(cleanup_task)
        except asyncio.CancelledError:
            with contextlib.suppress(asyncio.CancelledError):
                evidence = await cleanup_task
        if evidence is None:
            await keeper.stop()
        else:
            result.evidence = evidence.as_json()
            try:
                # La evidencia de ESTE directorio solo acredita audio que este run escribió;
                # en un takeover sin trabajo propio el intento queda `pending` para B4/B6
                # (nadie pudo acreditar el audio real). Que el lease venza nunca marca
                # `verified` por sí mismo: esto lo mantiene así.
                recorded = (
                    await leases.record_cleanup(
                        pool, lease, verified=evidence.verified, evidence=result.evidence,
                    )
                    if owned_audio else False
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

"""Supervisor local por intento (S1.B3): lease, trabajo ASR aislado y limpieza propia.

Flujo de un intento (S1.md §1.4):

    acquire -> receiving (original al tmpfs) -> transcribing (proceso ASR con ffmpeg)
            -> transcript_committed_cleanup_pending -> [limpieza verificada] -> succeeded

La limpieza no depende del proceso ASR: la hace el supervisor en `finally`, pase lo que
pase con el hijo (éxito, error, timeout, SIGKILL, lease perdido). Mata el grupo entero,
borra el directorio y verifica en `/proc` descriptores, procesos y mounts; solo con esa
evidencia registra `cleanup_status = verified`. Si la evidencia falla y este run
persistió `failed`, cierra la admisión de la instancia y dispara la alerta (S1.B6).
Un lease vencido que no llega a escribir esa fila deja `pending` y no cierra la admisión.

Si el supervisor muere entero (SIGKILL al contenedor), el tmpfs desaparece con el
contenedor (S1.B1) y el intento queda con lease vencido y `cleanup_status = pending`: la
reconciliación de ese caso es S1.B4/S1.B6.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import sys
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum
from pathlib import Path

import asyncpg

from maulwurf_ingest import admission, cancel, cleanup, metrics, persistence
from maulwurf_ingest import lease as leases
from maulwurf_ingest.asr_job import ORIGINAL, SCRIPTED_NAME, STUB_TRANSCRIPT_NAME
from maulwurf_ingest.audio.validate import probe_file
from maulwurf_ingest.config import settings
from maulwurf_ingest.outcomes import CATALOG
from maulwurf_ingest.receiver import PayloadTooLarge, receive

logger = logging.getLogger(__name__)


def _default_fragment_seconds() -> int:
    return settings.fragment_seconds


class Outcome(StrEnum):
    SUCCEEDED = "succeeded"
    NOT_ACQUIRED = "not_acquired"  # otro propietario tiene un lease vigente
    LEASE_LOST = "lease_lost"  # fenced: no se publicó nada después de perderlo
    ASR_FAILED = "asr_failed"
    REJECTED = "rejected"
    TIMEOUT = "asr_timeout"
    CLEANUP_FAILED = "cleanup_failed"
    CANCELLED = "cancelled"
    SUPERVISOR_ERROR = "supervisor_error"  # fallo imprevisto del propio supervisor


@dataclass(frozen=True)
class SupervisorConfig:
    instance: str
    lease_ttl: timedelta = timedelta(seconds=30)
    heartbeat_interval_s: float = 10.0
    asr_timeout_s: float = 600.0
    tmp_dir: str | None = None  # None = TMPDIR (el tmpfs del contenedor)
    realtime: bool = False  # solo pruebas: ffmpeg a velocidad real
    # Defecto: INGEST_FRAGMENT_SECONDS (30). effective_fragment_seconds lo recorta al piso.
    fragment_seconds: int = field(default_factory=_default_fragment_seconds)
    # Test double. Production leaves this unset, so the child never reads a fixture.
    stub_transcript: str | None = None
    # JSON for the in-tree transport double. The HTTP route never sets it.
    scripted_transport: str | None = None


@dataclass
class RunResult:
    outcome: Outcome
    fencing_token: int | None = None
    fragments: int | None = None
    sha256: str | None = None
    duration_s: float | None = None
    evidence: dict[str, object] = field(default_factory=dict)
    # Code from the child's stderr. Not an ``error_code`` unless it is in the catalog.
    child_error: str | None = None


# Gancho de pruebas: recibe (pid del trabajo ASR, directorio) al arrancar el hijo.
ChildHook = Callable[[int, Path], None]


class _LeaseKeeper:
    """Renueva el lease; si una renovación falla, avisa para cortar el trabajo."""

    def __init__(self, pool: asyncpg.Pool, lease: leases.Lease, interval_s: float) -> None:
        self.lost = asyncio.Event()
        self._task = asyncio.create_task(self._loop(pool, lease, interval_s))

    async def _loop(self, pool: asyncpg.Pool, lease: leases.Lease, interval_s: float) -> None:
        # Any failure, including InterfaceError when the pool closes, drops the lease.
        # A swallowed driver error used to leave the ASR job running with no owner.
        while True:
            await asyncio.sleep(interval_s)
            try:
                alive = await leases.heartbeat(pool, lease)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — every heartbeat error loses the lease
                logger.warning("supervisor.heartbeat_error error=%s", type(exc).__name__)
                self.lost.set()
                return
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


def _stub_document(text: str) -> bytes:
    """Fixture for the test double. The model name is the provisional contract, not a Riva reply."""
    body = {
        "text": text,
        "precision": "none",
        "words": [],
        "model": "whisper-large-v3",
        "client_version": "2.27.0",
    }
    return json.dumps(body).encode()


def _job_payload(stdout: bytes) -> dict[str, object]:
    # The stdout can carry the transcript. A decode error must not echo it into the log.
    try:
        parsed: object = json.loads(stdout)
    except json.JSONDecodeError:
        raise RuntimeError("asr_result") from None
    if not isinstance(parsed, dict):
        raise RuntimeError("asr_result")
    payload: dict[str, object] = {}
    for key, value in parsed.items():
        if not isinstance(key, str):
            raise RuntimeError("asr_result")
        payload[key] = value
    return payload


def _fragment_count(payload: dict[str, object]) -> int:
    raw = payload.get("fragments")
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise RuntimeError("asr_result")
    return raw


_CHILD_CODES = frozenset({
    "rpc_cancelled", "asr_rejected", "requires_reupload", "asr_failed",
})


def _child_code(stderr: bytes | None) -> str | None:
    """Read one error code. Anything else, including transcript text, is dropped."""
    if not stderr:
        return None
    try:
        parsed: object = json.loads(stderr.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    code = parsed.get("error")
    if isinstance(code, str) and code in _CHILD_CODES:
        return code
    return None


def _signal_group(pid: int, sig: int) -> None:
    if os.name == "posix":
        try:
            os.killpg(pid, sig)
        except OSError:
            return
        return
    try:
        os.kill(pid, sig)
    except OSError:
        return


async def _attempt_language(pool: asyncpg.Pool, lease: leases.Lease) -> str | None:
    value = await pool.fetchval(
        """
        SELECT a.language_code
          FROM ingestion_attempts ia
          JOIN audios a ON a.user_id = ia.user_id AND a.id = ia.audio_id
         WHERE ia.id = $1 AND ia.user_id = $2
        """,
        lease.attempt_id, lease.user_id,
    )
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


async def _run_asr(
    workdir: Path, config: SupervisorConfig, keeper: _LeaseKeeper, on_child: ChildHook | None,
    job: _Job, *, language: str | None,
) -> tuple[Outcome | None, dict[str, object] | None, str | None]:
    """Run the ASR child. A lost lease sends SIGTERM so the child can cancel the RPC."""
    argv = [sys.executable, "-m", "maulwurf_ingest.asr_job", str(workdir),
            "--fragment-seconds", str(config.fragment_seconds)]
    if config.realtime:
        argv.append("--realtime")
    if config.stub_transcript is not None:
        argv.append("--read-stub-transcript")
    else:
        if language is None:
            raise RuntimeError("language")
        argv.extend(["--language", language, "--deadline-s", str(config.asr_timeout_s)])
        if config.scripted_transport is not None:
            argv.append("--scripted-transport")
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        start_new_session=True,  # grupo propio: el supervisor puede matar hijo y nietos
    )
    job.pgid = proc.pid  # con start_new_session el pgid es el pid del hijo
    job.starttime = cleanup.start_time(proc.pid)
    if on_child is not None and proc.pid is not None:
        on_child(proc.pid, workdir)

    output = asyncio.create_task(proc.communicate())
    lost = asyncio.create_task(keeper.lost.wait())
    done: set[asyncio.Task[object]] = set()
    try:
        done, _ = await asyncio.wait(
            {output, lost}, timeout=config.asr_timeout_s, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        # También si cancelan el wait: si no, `lost` queda pendiente para siempre.
        lost.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await lost
    pid = proc.pid
    if output not in done:
        if lost in done and pid is not None:
            _signal_group(pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(asyncio.shield(output), timeout=config.heartbeat_interval_s)
            except TimeoutError:
                cleanup.kill_group(pid, job.starttime)
        elif pid is not None:
            cleanup.kill_group(pid, job.starttime)
        stdout, stderr = await output
        code = _child_code(stderr)
        if lost in done:
            return Outcome.LEASE_LOST, None, code
        return Outcome.TIMEOUT, None, code

    stdout, stderr = output.result()
    if proc.returncode != 0:
        code = _child_code(stderr) or "asr_failed"
        if code == "rpc_cancelled":
            return Outcome.LEASE_LOST, None, code
        return Outcome.ASR_FAILED, None, code
    return None, _job_payload(stdout), None


async def _note_cleanup_failure(
    pool: asyncpg.Pool, lease: leases.Lease, attempt_id: uuid.UUID, *,
    owned_audio: bool, exc: Exception, result: RunResult,
) -> None:
    """A cleanup exception is not verified deletion.

    Admission closes only when this run still owns the row.
    """
    logger.error(
        "supervisor.cleanup_error attempt_id=%s error=%s", attempt_id, type(exc).__name__,
    )
    cleanup.log_cleanup_failed(str(attempt_id), None, error_type=type(exc).__name__)
    metrics.record_failure("cleanup", "cleanup_error")
    result.evidence = {"verified": False, "error": type(exc).__name__}
    recorded = False
    if owned_audio:
        recorded = await leases.record_cleanup(
            pool, lease, verified=False, evidence=result.evidence,
        )
    if recorded:
        admission.halt(attempt_id)
    if result.outcome is Outcome.SUCCEEDED:
        result.outcome = Outcome.CLEANUP_FAILED


async def _note_cleanup_evidence(
    pool: asyncpg.Pool, lease: leases.Lease, attempt_id: uuid.UUID,
    evidence: cleanup.CleanupEvidence, *, owned_audio: bool, committed: bool,
    result: RunResult, audio_id: uuid.UUID | None, version: int | None,
) -> None:
    """Record this run's evidence. Enable the text outbox only after a verified commit."""
    result.evidence = evidence.as_json()
    # La evidencia de ESTE directorio solo acredita audio que este run escribió;
    # en un takeover sin trabajo propio el intento queda `pending` para B4/B6.
    recorded = (
        await leases.record_cleanup(
            pool, lease, verified=evidence.verified, evidence=result.evidence,
        )
        if owned_audio else False
    )
    if not evidence.verified:
        logger.error("supervisor.cleanup_failed attempt_id=%s", attempt_id)
        cleanup.log_cleanup_failed(str(attempt_id), evidence)
        metrics.record_failure("cleanup", "verification_failed")
        # Solo un `failed` persistido con el token de este run. Si el lease
        # ya no es nuestro, la fila sigue `pending` y la admisión no se cierra.
        if recorded:
            admission.halt(attempt_id)
        if result.outcome is Outcome.SUCCEEDED:
            result.outcome = Outcome.CLEANUP_FAILED
        return
    if not committed or audio_id is None or version is None:
        return
    enabled = recorded and await persistence.enable_text_outbox(
        pool, lease, audio_id=audio_id, version=version,
    )
    if not enabled:
        result.outcome = Outcome.LEASE_LOST


def _error_name(exc: Exception | None) -> str | None:
    return None if exc is None else type(exc).__name__


async def _note_cancelled_cleanup(
    pool: asyncpg.Pool, lease: leases.Lease, attempt_id: uuid.UUID, *,
    evidence: cleanup.CleanupEvidence | None, exc: Exception | None, result: RunResult,
) -> None:
    """The attempt is already ``cancelled``. Record cleanup and do not publish."""
    if exc is not None:
        result.evidence = {"verified": False, "error": type(exc).__name__}
        verified = False
    elif evidence is None:
        result.outcome = Outcome.CANCELLED
        return
    else:
        result.evidence = evidence.as_json()
        verified = evidence.verified
    recorded = await cancel.record_cancelled_cleanup(
        pool, lease, verified=verified, evidence=result.evidence,
    )
    if not verified:
        cleanup.log_cleanup_failed(str(attempt_id), evidence, error_type=_error_name(exc))
        metrics.record_failure("cleanup", "verification_failed")
    if recorded and not verified:
        admission.halt(attempt_id)
    result.outcome = Outcome.CANCELLED


async def run_attempt(
    pool: asyncpg.Pool, *, user_id: uuid.UUID, attempt_id: uuid.UUID,
    audio: AsyncIterator[bytes],
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
    published_audio_id: uuid.UUID | None = None
    published_version: int | None = None
    owned_audio = False  # solo el run que escribió el audio puede acreditar su limpieza
    try:
        if not await leases.transition(
            pool, lease, from_status="awaiting_upload", to_status="receiving"
        ):
            return result
        # El tope es el provisional de recepción, no un máximo aprobado. El hash sale
        # de los bytes escritos; ffprobe, no un Content-Length, decide duración y formato.
        # owned_audio antes de leer: un corte a media recepción ya escribió bytes.
        owned_audio = True
        try:
            received = await receive(
                audio, workdir / ORIGINAL, max_bytes=settings.max_upload_bytes
            )
        except PayloadTooLarge:
            result.outcome = Outcome.REJECTED
            if not await leases.transition(
                pool, lease, from_status="receiving", to_status="rejected",
                error_code="payload_too_large",
            ):
                result.outcome = Outcome.LEASE_LOST
            return result
        except cancel.ReceptionInterrupted:
            result.outcome = Outcome.CANCELLED
            if not await cancel.invalidate_reception(pool, lease):
                result.outcome = Outcome.LEASE_LOST
            return result
        result.sha256 = received.sha256
        decision = await asyncio.to_thread(probe_file, workdir / ORIGINAL)
        if decision.decision == "reject":
            code = decision.reason if decision.reason in CATALOG else "supervisor_error"
            spec = CATALOG[code]
            result.outcome = (
                Outcome.REJECTED if spec.status == "rejected" else Outcome.SUPERVISOR_ERROR
            )
            if not await leases.transition(
                pool, lease, from_status="receiving", to_status=spec.status, error_code=code,
            ):
                result.outcome = Outcome.LEASE_LOST
            return result
        result.duration_s = decision.duration_s
        if not await leases.transition(
            pool, lease, from_status="receiving", to_status="transcribing"
        ):
            return result

        committed, published_audio_id, published_version = await _after_transcribing(
            pool, lease, workdir, config, keeper, on_child, job, result,
        )
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
        await _close_attempt(
            pool, lease, attempt_id, workdir, job, keeper, result,
            owned_audio=owned_audio, committed=committed,
            audio_id=published_audio_id, version=published_version,
        )
    return result


async def _after_transcribing(
    pool: asyncpg.Pool, lease: leases.Lease, workdir: Path, config: SupervisorConfig,
    keeper: _LeaseKeeper, on_child: ChildHook | None, job: _Job, result: RunResult,
) -> tuple[bool, uuid.UUID | None, int | None]:
    """ASR and the commit. The caller owns cleanup."""
    if config.stub_transcript is not None:
        cleanup.write_private(
            workdir / STUB_TRANSCRIPT_NAME, _stub_document(config.stub_transcript),
        )
    language: str | None = None
    if config.stub_transcript is None:
        language = await _attempt_language(pool, lease)
        if language is None or any(ord(char) < 33 for char in language):
            result.outcome = Outcome.SUPERVISOR_ERROR
            if not await leases.transition(
                pool, lease, from_status="transcribing", to_status="requires_reupload",
                error_code="supervisor_error",
            ):
                result.outcome = Outcome.LEASE_LOST
            return False, None, None
        if config.scripted_transport is not None:
            cleanup.write_private(
                workdir / SCRIPTED_NAME, config.scripted_transport.encode("utf-8"),
            )
    failure, payload, child = await _run_asr(
        workdir, config, keeper, on_child, job, language=language,
    )
    if child is not None:
        result.child_error = child
    if failure is not None:
        result.outcome = failure
        if failure is not Outcome.LEASE_LOST:
            error_code = child if child in CATALOG else failure.value
            await leases.transition(
                pool, lease, from_status="transcribing", to_status="requires_reupload",
                error_code=error_code,
            )
        return False, None, None
    if payload is None:
        raise RuntimeError("asr_result")
    fragments = _fragment_count(payload)
    draft = persistence.draft_from_job(payload.get("transcript"))
    if draft is None:
        result.outcome = Outcome.SUPERVISOR_ERROR
        if not await leases.transition(
            pool, lease, from_status="transcribing", to_status="requires_reupload",
            error_code=Outcome.SUPERVISOR_ERROR.value,
        ):
            result.outcome = Outcome.LEASE_LOST
        return False, None, None
    published = await persistence.commit_transcript(
        pool, lease, draft, fragments=fragments,
        fragment_seconds=config.fragment_seconds, overlap_s=settings.fragment_overlap_s,
        duration_s=result.duration_s,
    )
    result.fragments = fragments
    if (
        published.outcome is persistence.CommitOutcome.COMMITTED
        and published.audio_id is not None
        and published.version is not None
    ):
        result.outcome = Outcome.SUCCEEDED
        return True, published.audio_id, published.version
    if published.outcome is persistence.CommitOutcome.NO_USABLE_TEXT:
        result.outcome = Outcome.REJECTED
        if not await leases.transition(
            pool, lease, from_status="transcribing", to_status="rejected",
            error_code="no_usable_text",
            fragments_done=fragments, fragments_total=fragments,
        ):
            result.outcome = Outcome.LEASE_LOST
        return False, None, None
    if published.outcome is persistence.CommitOutcome.NOT_OWNER:
        result.outcome = Outcome.LEASE_LOST
        return False, None, None
    result.outcome = Outcome.SUPERVISOR_ERROR
    if not await leases.transition(
        pool, lease, from_status="transcribing", to_status="requires_reupload",
        error_code=Outcome.SUPERVISOR_ERROR.value,
    ):
        result.outcome = Outcome.LEASE_LOST
    return False, None, None


async def _close_attempt(
    pool: asyncpg.Pool, lease: leases.Lease, attempt_id: uuid.UUID, workdir: Path, job: _Job,
    keeper: _LeaseKeeper, result: RunResult, *, owned_audio: bool, committed: bool,
    audio_id: uuid.UUID | None, version: int | None,
) -> None:
    # `to_thread` solo encola el borrado. En CPython 3.12 una sola
    # cancelación no relanza dentro del finally; una segunda puede cortar
    # el await antes de que el hilo arranque y dejar el audio en tmpfs.
    # Una tarea propia con shield termina destroy_and_verify aunque
    # cancelen otra vez. Cualquier otra excepción deja cleanup_status=failed:
    # si no, el heartbeat seguiría renovando el lease.
    cleanup_task = asyncio.create_task(
        asyncio.to_thread(
            cleanup.destroy_and_verify, workdir, job.pgid, starttime=job.starttime
        )
    )
    evidence: cleanup.CleanupEvidence | None = None
    cleanup_error: Exception | None = None
    cancelled = False
    try:
        evidence = await asyncio.shield(cleanup_task)
    except asyncio.CancelledError:
        cancelled = True
        try:
            evidence = await cleanup_task
        except asyncio.CancelledError:
            evidence = None
        except Exception as exc:
            cleanup_error = exc
    except Exception as exc:
        cleanup_error = exc
    try:
        if owned_audio and await cancel.is_cancelled(pool, lease):
            await _note_cancelled_cleanup(
                pool, lease, attempt_id, evidence=evidence, exc=cleanup_error, result=result,
            )
        elif cleanup_error is not None:
            await _note_cleanup_failure(
                pool, lease, attempt_id, owned_audio=owned_audio, exc=cleanup_error,
                result=result,
            )
        elif evidence is not None:
            await _note_cleanup_evidence(
                pool, lease, attempt_id, evidence, owned_audio=owned_audio,
                committed=committed, result=result, audio_id=audio_id, version=version,
            )
    finally:
        await keeper.stop()
    if cancelled:
        raise asyncio.CancelledError


async def finish_transcription(
    pool: asyncpg.Pool, *, lease: leases.Lease, workdir: Path, config: SupervisorConfig,
    keeper: _LeaseKeeper, on_child: ChildHook | None = None,
) -> RunResult:
    """Transcribe a held upload. The attempt is already ``transcribing``."""
    result = RunResult(Outcome.LEASE_LOST, fencing_token=lease.fencing_token)
    job = _Job()
    committed = False
    audio_id: uuid.UUID | None = None
    version: int | None = None
    try:
        committed, audio_id, version = await _after_transcribing(
            pool, lease, workdir, config, keeper, on_child, job, result,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("supervisor.unexpected_error attempt_id=%s", lease.attempt_id)
        result.outcome = Outcome.SUPERVISOR_ERROR
        await leases.transition(
            pool, lease, from_status="transcribing", to_status="requires_reupload",
            error_code=Outcome.SUPERVISOR_ERROR.value,
        )
    finally:
        await _close_attempt(
            pool, lease, lease.attempt_id, workdir, job, keeper, result,
            owned_audio=True, committed=committed, audio_id=audio_id, version=version,
        )
    return result


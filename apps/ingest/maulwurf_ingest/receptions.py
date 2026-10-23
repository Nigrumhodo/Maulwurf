"""Internal reception routes (ADR-0008, S2.11).

``api`` is the only caller. The browser session never reaches this process. Bytes
are written to the attempt tmpfs as they arrive; the route does not assemble them
into one ``bytes`` object.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import shutil
import tempfile
import time
import uuid
from collections import OrderedDict
from datetime import timedelta
from pathlib import Path

import asyncpg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.requests import ClientDisconnect

from maulwurf_ingest import admission, cleanup
from maulwurf_ingest import lease as leases
from maulwurf_ingest.audio.validate import probe_file
from maulwurf_ingest.config import settings
from maulwurf_ingest.credential import (
    CredentialClaims,
    CredentialError,
    WrongInstance,
    decode_secret,
    verify,
)
from maulwurf_ingest.outcomes import CATALOG, DISCARD_REASONS, HTTP_ONLY, ErrorSpec
from maulwurf_ingest.receiver import PayloadTooLarge, receive
from maulwurf_ingest.supervisor import (
    Outcome,
    RunResult,
    SupervisorConfig,
    _close_attempt,
    _Job,
    _LeaseKeeper,
    finish_transcription,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Named apart from the lease TTL so a later change of one does not move the other.
# Both are the S1.B3 value today, and neither may exceed it.
LEASE_TTL_S = 30.0
CREDENTIAL_TTL_S = 30.0
HOLD_TIMEOUT_S = 30.0
ACTIVE_SLOTS = 1
if CREDENTIAL_TTL_S > LEASE_TTL_S or HOLD_TIMEOUT_S > LEASE_TTL_S:
    raise RuntimeError("hold_exceeds_lease")

_TASKS: set[asyncio.Task[object]] = set()


class _Gate:
    def __init__(self, slots: int) -> None:
        self.slots = slots
        self._used = 0
        self._users: set[uuid.UUID] = set()
        self._lock = asyncio.Lock()

    async def take(self, user_id: uuid.UUID) -> bool:
        async with self._lock:
            if self._used >= self.slots or user_id in self._users:
                return False
            self._used += 1
            self._users.add(user_id)
            return True

    async def release(self, user_id: uuid.UUID) -> None:
        async with self._lock:
            if user_id in self._users:
                self._users.remove(user_id)
                if self._used:
                    self._used -= 1

    def reset(self) -> None:
        self._used = 0
        self._users.clear()


class _Hold:
    def __init__(
        self, *, lease: leases.Lease, workdir: Path, keeper: _LeaseKeeper, sha256: str,
        received_bytes: int, duration_s: float, audio_id: uuid.UUID,
    ) -> None:
        self.lease = lease
        self.workdir = workdir
        self.keeper = keeper
        self.sha256 = sha256
        self.received_bytes = received_bytes
        self.duration_s = duration_s
        self.audio_id = audio_id
        self.phase = "holding"
        self.lock = asyncio.Lock()
        self.finished = asyncio.Event()
        self.cleanup_status: str | None = None


_gate = _Gate(ACTIVE_SLOTS)
_holds: dict[uuid.UUID, _Hold] = {}
# A discard retry only needs recent attempts. Older rows are already terminal.
_FINISHED_MAX = 1024
_finished: OrderedDict[uuid.UUID, str] = OrderedDict()


def reset_state() -> None:
    """Tests only. Drops in-memory holds without touching the database."""
    _holds.clear()
    _finished.clear()
    _gate.reset()
    for task in list(_TASKS):
        task.cancel()
    _TASKS.clear()


def configured_keys() -> dict[str, bytes] | None:
    raw = settings.credential_secret
    if raw is None or not raw.strip():
        return None
    try:
        keys = {settings.credential_kid: decode_secret(raw)}
        previous = settings.credential_previous_secret
        previous_kid = settings.credential_previous_kid
        if previous and previous_kid:
            keys[previous_kid] = decode_secret(previous)
    except ValueError:
        return None
    return keys


def _error(status: int, code: str, *, reason: str | None = None) -> JSONResponse:
    spec = CATALOG.get(code) or HTTP_ONLY.get(code)
    message = spec.message if spec is not None else "No se pudo procesar la recepción."
    details: dict[str, object] = {}
    if reason is not None:
        details["reason"] = reason
    logger.info("reception.answer code=%s status=%s", code, status)
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "details": details}},
    )


def _from_spec(spec: ErrorSpec, *, reason: str | None = None) -> JSONResponse:
    return _error(spec.http_status, spec.http_code, reason=reason)


def _read_claims(
    request: Request, *, op: str, attempt_id: uuid.UUID, instance: str | None,
) -> CredentialClaims | JSONResponse:
    keys = configured_keys()
    if keys is None:
        return _error(503, "capacity_unavailable")
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        return _error(401, "internal_credential_invalid")
    token = header.removeprefix("Bearer ").strip()
    if op not in {"receive", "admit", "discard"}:
        return _error(401, "internal_credential_invalid")
    try:
        claims = verify(
            token,
            keys=keys,
            expected_op=op,  # type: ignore[arg-type]
            now=int(time.time()),
            instance=instance,
        )
    except WrongInstance:
        return _error(409, "wrong_instance")
    except CredentialError:
        return _error(401, "internal_credential_invalid")
    if claims.attempt_id != attempt_id:
        return _error(401, "internal_credential_invalid")
    return claims


def _remember(attempt_id: uuid.UUID, status: str) -> None:
    """Keep the latest cleanup result and drop the oldest past the cap."""
    _finished[attempt_id] = status
    _finished.move_to_end(attempt_id)
    while len(_finished) > _FINISHED_MAX:
        _finished.popitem(last=False)


def _track(task: asyncio.Task[object]) -> None:
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


async def open_service_pool(app: object) -> None:
    """Open the process pool once, during startup.

    A request must not call ``create_pool``: two cold requests used to each
    open one, and the loser was never closed.
    """
    state = getattr(app, "state", None)
    current = getattr(state, "pool", None)
    if isinstance(current, asyncpg.Pool):
        return
    if not settings.database_url:
        return
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    try:
        created = await asyncpg.create_pool(dsn, min_size=1, max_size=4, timeout=2.0)
    except Exception as exc:  # noqa: BLE001 — a down database is capacity, and the message may hold the DSN
        logger.warning("reception.pool_failed error=%s", type(exc).__name__)
        return
    if created is None or state is None:
        return
    state.pool = created


async def _service_pool(request: Request) -> asyncpg.Pool | None:
    current = getattr(request.app.state, "pool", None)
    if isinstance(current, asyncpg.Pool):
        return current
    return None


def _writable_tmp() -> bool:
    tmp_dir = tempfile.gettempdir()
    try:
        statvfs = getattr(os, "statvfs", None)
        if callable(statvfs) and statvfs(tmp_dir).f_bavail == 0:
            return False
        with tempfile.TemporaryFile(dir=tmp_dir):
            pass
    except OSError:
        return False
    return True


def _instance_id(request: Request) -> str:
    value = getattr(request.app.state, "instance_id", None)
    if isinstance(value, str) and value:
        return value
    return "ingest:unconfigured"


def _asr_config(request: Request) -> SupervisorConfig:
    return SupervisorConfig(
        instance=_instance_id(request),
        lease_ttl=timedelta(seconds=LEASE_TTL_S),
        heartbeat_interval_s=10.0,
        asr_timeout_s=600.0,
    )


async def _cleanup(pool: asyncpg.Pool, hold: _Hold) -> str:
    result = RunResult(Outcome.LEASE_LOST, fencing_token=hold.lease.fencing_token)
    await _close_attempt(
        pool, hold.lease, hold.lease.attempt_id, hold.workdir, _Job(), hold.keeper, result,
        owned_audio=True, committed=False, audio_id=None, version=None,
    )
    recorded = "verified" if result.evidence.get("verified") is True else "failed"
    hold.cleanup_status = recorded
    _remember(hold.lease.attempt_id, recorded)
    _holds.pop(hold.lease.attempt_id, None)
    await _gate.release(hold.lease.user_id)
    return recorded


async def _terminal(pool: asyncpg.Pool, hold: _Hold, *, code: str, from_status: str) -> str:
    spec = CATALOG[code]
    await leases.transition(
        pool, hold.lease, from_status=from_status, to_status=spec.status, error_code=code,
    )
    return await _cleanup(pool, hold)


async def _force_release(hold: _Hold) -> str:
    """Drop the tmpfs file and the slot when recording cleanup has already failed.

    The keeper must stop here. If it kept renewing the lease, the audio would
    sit in tmpfs until the process restarted.
    """
    if hold.phase == "closed":
        return hold.cleanup_status or "failed"
    hold.phase = "closed"
    hold.finished.set()
    try:
        await hold.keeper.stop()
    except Exception as exc:  # noqa: BLE001 — stopping the keeper must not keep the slot
        logger.warning("reception.keeper_stop_failed error=%s", type(exc).__name__)
    shutil.rmtree(hold.workdir, ignore_errors=True)
    hold.cleanup_status = "failed"
    _remember(hold.lease.attempt_id, "failed")
    _holds.pop(hold.lease.attempt_id, None)
    await _gate.release(hold.lease.user_id)
    return "failed"


async def _safe_cleanup(pool: asyncpg.Pool, hold: _Hold) -> str:
    try:
        return await _cleanup(pool, hold)
    except Exception as exc:  # noqa: BLE001 — the file and the slot still have to go
        logger.warning(
            "reception.cleanup_failed attempt_id=%s error=%s",
            hold.lease.attempt_id, type(exc).__name__,
        )
        return await _force_release(hold)


async def _safe_terminal(
    pool: asyncpg.Pool, hold: _Hold, *, code: str, from_status: str,
) -> str:
    try:
        return await _terminal(pool, hold, code=code, from_status=from_status)
    except Exception as exc:  # noqa: BLE001 — a database error must not keep the audio
        logger.warning(
            "reception.terminal_failed attempt_id=%s error=%s",
            hold.lease.attempt_id, type(exc).__name__,
        )
        return await _force_release(hold)


async def _expire(pool: asyncpg.Pool, hold: _Hold) -> None:
    try:
        await asyncio.wait_for(hold.finished.wait(), HOLD_TIMEOUT_S)
    except TimeoutError:
        pass
    else:
        return
    async with hold.lock:
        if hold.phase != "holding":
            return
        hold.phase = "discarding"
        hold.finished.set()
    await _safe_terminal(pool, hold, code="reception_abandoned", from_status="receiving")
    logger.info("reception.abandoned attempt_id=%s", hold.lease.attempt_id)


def _declared_too_big(request: Request) -> bool:
    raw = request.headers.get("x-maulwurf-declared-bytes")
    if raw is None:
        return False
    try:
        declared = int(raw)
    except ValueError:
        return False
    return declared > settings.max_upload_bytes


async def _open_hold(
    pool: asyncpg.Pool, *, claims: CredentialClaims, instance: str,
) -> _Hold | JSONResponse:
    try:
        lease = await leases.acquire_bound(
            pool, user_id=claims.user_id, audio_id=claims.audio_id,
            attempt_id=claims.attempt_id, expected_token=claims.fencing_token,
            instance=instance, ttl=timedelta(seconds=LEASE_TTL_S),
        )
    except Exception:  # noqa: BLE001 — any acquire failure must return the slot
        await _gate.release(claims.user_id)
        raise
    if lease is None:
        await _gate.release(claims.user_id)
        return _error(409, "attempt_not_active")
    workdir: Path | None = None
    try:
        workdir = cleanup.create_workdir(str(claims.attempt_id), None)
        keeper = _LeaseKeeper(pool, lease, 10.0)
    except Exception as exc:  # noqa: BLE001 — the slot is already taken
        # The slot was taken before the lease. A failure here must give it back
        # even when the status write itself fails.
        logger.warning(
            "reception.open_failed attempt_id=%s error=%s",
            claims.attempt_id, type(exc).__name__,
        )
        await _gate.release(claims.user_id)
        if workdir is not None:
            shutil.rmtree(workdir, ignore_errors=True)
        try:
            await leases.transition(
                pool, lease, from_status="receiving", to_status="requires_reupload",
                error_code="supervisor_error",
            )
        except Exception as write_exc:  # noqa: BLE001 — the slot is already released
            logger.warning("reception.open_status_failed error=%s", type(write_exc).__name__)
        return _error(500, "internal_error")
    return _Hold(
        lease=lease, workdir=workdir, keeper=keeper, sha256="", received_bytes=0,
        duration_s=0.0, audio_id=claims.audio_id,
    )


@router.put("/internal/v1/receptions/{attempt_id}")
async def put_receive(attempt_id: uuid.UUID, request: Request) -> JSONResponse:
    checked = _read_claims(request, op="receive", attempt_id=attempt_id, instance=None)
    if isinstance(checked, JSONResponse):
        return checked
    if not admission.uploads_admitted() or not _writable_tmp():
        return _error(503, "capacity_unavailable")
    if not await _gate.take(checked.user_id):
        return _error(503, "capacity_unavailable")
    pool = await _service_pool(request)
    if pool is None:
        await _gate.release(checked.user_id)
        return _error(503, "capacity_unavailable")
    opened = await _open_hold(pool, claims=checked, instance=_instance_id(request))
    if isinstance(opened, JSONResponse):
        return opened
    hold = opened
    failure = "supervisor_error"
    disconnected: BaseException | None = None
    try:
        if _declared_too_big(request):
            failure = "payload_too_large"
        else:
            received = await receive(
                request.stream(), hold.workdir / "original", max_bytes=settings.max_upload_bytes,
            )
            decision = await asyncio.to_thread(probe_file, hold.workdir / "original")
            if decision.decision == "reject" or decision.duration_s is None:
                failure = decision.reason if decision.reason in CATALOG else "supervisor_error"
            else:
                hold.sha256 = received.sha256
                hold.received_bytes = received.received_bytes
                hold.duration_s = decision.duration_s
                _holds[attempt_id] = hold
                _track(asyncio.create_task(_expire(pool, hold)))
                logger.info(
                    "reception.held attempt_id=%s bytes=%s duration_s=%s",
                    attempt_id, received.received_bytes, decision.duration_s,
                )
                return JSONResponse(
                    status_code=200,
                    content={
                        "fencing_token": hold.lease.fencing_token,
                        "instance": _instance_id(request),
                        "sha256": received.sha256,
                        "received_bytes": received.received_bytes,
                        "duration_s": decision.duration_s,
                    },
                )
    except PayloadTooLarge:
        failure = "payload_too_large"
    except (ClientDisconnect, asyncio.CancelledError) as exc:
        failure = "reception_interrupted"
        disconnected = exc
    except Exception as exc:
        logger.warning("reception.failed attempt_id=%s error=%s", attempt_id, type(exc).__name__)
        failure = "supervisor_error"
    # Probe sits in the same try as the stream. A cancel there still deletes the file.
    await asyncio.shield(
        _safe_terminal(pool, hold, code=failure, from_status="receiving"),
    )
    if disconnected is not None:
        raise disconnected
    spec = CATALOG[failure]
    reason = failure if spec.http_code == "unsupported_format" else None
    return _from_spec(spec, reason=reason)


def _matches(hold: _Hold, claims: CredentialClaims) -> bool:
    return (
        claims.user_id == hold.lease.user_id
        and claims.audio_id == hold.audio_id
        and claims.fencing_token == hold.lease.fencing_token
    )


@router.post("/internal/v1/receptions/{attempt_id}/admit")
async def post_admit(attempt_id: uuid.UUID, request: Request) -> JSONResponse:
    instance = _instance_id(request)
    checked = _read_claims(request, op="admit", attempt_id=attempt_id, instance=instance)
    if isinstance(checked, JSONResponse):
        return checked
    hold = _holds.get(attempt_id)
    if hold is None or not _matches(hold, checked):
        if hold is not None and not _matches(hold, checked):
            return _error(401, "internal_credential_invalid")
        return _error(409, "reception_not_held")
    try:
        body: object = await request.json()
    except Exception:  # noqa: BLE001 — a malformed body is an integrity failure
        body = None
    sha, count = _admit_body(body)
    mismatch = (
        sha is None or count is None or count != hold.received_bytes
        or not hmac.compare_digest(sha, hold.sha256)
    )
    # The expiry timer is the rescue while the phase is still "holding".
    # Marking the hold finished before the pool exists cancels that rescue.
    pool = await _service_pool(request)
    if pool is None:
        return _error(503, "capacity_unavailable")
    async with hold.lock:
        if hold.phase != "holding":
            return _error(409, "reception_not_held")
        hold.phase = "discarding" if mismatch else "admitted"
        hold.finished.set()
    if mismatch:
        await _safe_terminal(
            pool, hold, code="reception_integrity_failed", from_status="receiving",
        )
        return _error(409, "reception_integrity_failed")
    try:
        moved = await leases.transition(
            pool, hold.lease, from_status="receiving", to_status="transcribing",
        )
    except Exception as exc:  # noqa: BLE001 — the phase already consumed the hold timer
        logger.warning(
            "reception.admit_failed attempt_id=%s error=%s", attempt_id, type(exc).__name__,
        )
        await _safe_cleanup(pool, hold)
        return _error(500, "internal_error")
    if not moved:
        await _safe_cleanup(pool, hold)
        return _error(409, "reception_not_held")

    async def _admitted() -> None:
        try:
            await finish_transcription(
                pool, lease=hold.lease, workdir=hold.workdir, config=_asr_config(request),
                keeper=hold.keeper,
            )
        finally:
            _holds.pop(attempt_id, None)
            await _gate.release(hold.lease.user_id)

    _track(asyncio.create_task(_admitted()))
    return JSONResponse(status_code=202, content={"status": "transcribing"})


def _admit_body(body: object) -> tuple[str | None, int | None]:
    if not isinstance(body, dict):
        return None, None
    sha = body.get("sha256")
    count = body.get("received_bytes")
    if not isinstance(sha, str) or len(sha) != 64:
        return None, None
    if any(char not in "0123456789abcdefABCDEF" for char in sha):
        return None, None
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        return None, None
    return sha.lower(), count


@router.post("/internal/v1/receptions/{attempt_id}/discard")
async def post_discard(attempt_id: uuid.UUID, request: Request) -> JSONResponse:
    instance = _instance_id(request)
    checked = _read_claims(request, op="discard", attempt_id=attempt_id, instance=instance)
    if isinstance(checked, JSONResponse):
        return checked
    recorded = _finished.get(attempt_id)
    hold = _holds.get(attempt_id)
    if hold is None:
        if recorded is None:
            return _error(409, "reception_not_held")
        return JSONResponse(status_code=200, content={"cleanup_status": recorded})
    if not _matches(hold, checked):
        return _error(401, "internal_credential_invalid")
    try:
        body: object = await request.json()
    except Exception:  # noqa: BLE001 — not a discard reason; the hold stays until the timer
        return _error(422, "validation_failed")
    reason = body.get("reason") if isinstance(body, dict) else None
    if not isinstance(reason, str) or reason not in DISCARD_REASONS:
        return _error(422, "validation_failed")
    pool = await _service_pool(request)
    if pool is None:
        return _error(503, "capacity_unavailable")
    async with hold.lock:
        if hold.phase != "holding":
            recorded_now = hold.cleanup_status or _finished.get(attempt_id)
            if recorded_now is not None:
                return JSONResponse(status_code=200, content={"cleanup_status": recorded_now})
            return _error(409, "reception_not_held")
        hold.phase = "discarding"
        hold.finished.set()
    status = await _safe_terminal(pool, hold, code=reason, from_status="receiving")
    return JSONResponse(status_code=200, content={"cleanup_status": status})

"""In-memory fake of the ADR-0008 internal reception API of `ingest` (A2.13 tests).

It verifies every credential with the same `verify` the API mints against, keeps per-attempt
state (`awaiting`, `receiving`, `held`, `admitted`, `discarded`) and never stores the body:
bytes are hashed and counted as they arrive. Tests drive it through `faults` to produce the
failures of ADR-0008 §3 (early `413`, `415`, `503`, `409`, a wrong hash, a failed discard,
a dropped connection) and read its recorded state to check what `api` sent.

Run it under uvicorn on a free loopback port (`serve`), so the requests really stream over
TCP and backpressure is real.
"""

import asyncio
import hashlib
import threading
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import uvicorn
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import ClientDisconnect, Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from app.services.ingest_credential import (
    CredentialClaims,
    CredentialError,
    Op,
    WrongInstance,
    verify,
)

State = Literal["awaiting", "receiving", "held", "admitted", "discarded", "failed"]
_DISCARD_REASONS = frozenset({
    "duplicate_exact",
    "duplicate_variant",
    "variant_hash_mismatch",
    "client_disconnected",
    "reception_integrity_failed",
    "api_error",
})


@dataclass
class FakeAttempt:
    """Database-side view of one attempt, as `ingest` would read it from the lease row."""

    user_id: uuid.UUID
    audio_id: uuid.UUID
    fencing_token: int
    state: State = "awaiting"
    held_token: int | None = None
    sha256: str | None = None
    received_bytes: int | None = None
    discard_reason: str | None = None
    cleanup_status: str | None = None
    faults: dict[str, Any] = field(default_factory=dict)


def _error(status: int, code: str, reason: str | None = None) -> JSONResponse:
    details: dict[str, object] = {} if reason is None else {"reason": reason}
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": code, "details": details}},
    )


class FakeIngest:
    """Keeps the attempts, the calls seen and the knobs; `app` is what uvicorn serves."""

    def __init__(self, *, kid: str, secret: bytes, instance: str = "fake-ingest-1") -> None:
        self.keys = {kid: secret}
        self.instance = instance
        self.attempts: dict[uuid.UUID, FakeAttempt] = {}
        # Header names only: values hold the credential and are never recorded.
        self.calls: list[tuple[str, uuid.UUID, frozenset[str]]] = []
        self.asr_starts = 0
        self.receive_disconnects = 0
        self.bytes_read = 0
        self.read_rate_bps = 0.0  # 0 = unthrottled; otherwise bytes per second
        self.app = Starlette(
            routes=[
                Route(
                    "/internal/v1/receptions/{attempt_id}",
                    self._receive,
                    methods=["PUT"],
                ),
                Route(
                    "/internal/v1/receptions/{attempt_id}/admit",
                    self._admit,
                    methods=["POST"],
                ),
                Route(
                    "/internal/v1/receptions/{attempt_id}/discard",
                    self._discard,
                    methods=["POST"],
                ),
            ],
        )

    def register(
        self,
        attempt_id: uuid.UUID,
        *,
        user_id: uuid.UUID,
        audio_id: uuid.UUID,
        fencing_token: int,
    ) -> FakeAttempt:
        attempt = FakeAttempt(user_id=user_id, audio_id=audio_id, fencing_token=fencing_token)
        self.attempts[attempt_id] = attempt
        return attempt

    def fault(self, attempt_id: uuid.UUID, **faults: Any) -> None:
        self.attempts[attempt_id].faults.update(faults)

    def _check(
        self, request: Request, op: Op, attempt_id: uuid.UUID,
    ) -> CredentialClaims | Response:
        self.calls.append((op, attempt_id, frozenset(k.lower() for k in request.headers)))
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer "):
            return _error(401, "internal_credential_invalid")
        instance = None if op == "receive" else self.instance
        try:
            claims = verify(
                header.removeprefix("Bearer ").strip(),
                keys=self.keys,
                expected_op=op,
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

    def _bound(self, claims: CredentialClaims, attempt: FakeAttempt, token: int | None) -> bool:
        return (
            claims.user_id == attempt.user_id
            and claims.audio_id == attempt.audio_id
            and token is not None
            and claims.fencing_token == token
        )

    async def _receive(self, request: Request) -> Response:
        attempt_id = _path_id(request)
        checked = self._check(request, "receive", attempt_id)
        if isinstance(checked, Response):
            return checked
        attempt = self.attempts.get(attempt_id)
        if attempt is None or attempt.state != "awaiting":
            return _error(409, "attempt_not_active")
        if checked.user_id != attempt.user_id or checked.audio_id != attempt.audio_id:
            return _error(401, "internal_credential_invalid")
        if checked.fencing_token != attempt.fencing_token:
            return _error(409, "attempt_not_active")  # replay or stale token: fenced out
        if attempt.faults.get("unavailable"):
            return _error(503, "capacity_unavailable")
        if attempt.faults.get("conflict"):
            return _error(409, "attempt_not_active")
        attempt.state = "receiving"
        attempt.fencing_token += 1
        attempt.held_token = attempt.fencing_token
        return await self._stream_into(request, attempt, attempt_id)

    async def _stream_into(
        self, request: Request, attempt: FakeAttempt, attempt_id: uuid.UUID,
    ) -> Response:
        hasher = hashlib.sha256()
        count = 0
        limit = attempt.faults.get("too_large_at")
        unsupported = attempt.faults.get("unsupported")
        try:
            async for chunk in request.stream():
                if self.read_rate_bps:
                    await asyncio.sleep(len(chunk) / self.read_rate_bps)
                hasher.update(chunk)
                count += len(chunk)
                self.bytes_read += len(chunk)
                if limit is not None and count >= limit:
                    attempt.state = "failed"
                    return _error(413, "payload_too_large")
        except ClientDisconnect:
            self.receive_disconnects += 1
            attempt.state = "failed"
            return Response(status_code=499)
        if unsupported or count == 0:
            attempt.state = "failed"
            return _error(415, "unsupported_format", reason="malformed")
        attempt.sha256 = hasher.hexdigest()
        attempt.received_bytes = count
        attempt.state = "held"
        reported = "0" * 64 if attempt.faults.get("wrong_hash") else attempt.sha256
        return JSONResponse(
            status_code=200,
            content={
                "fencing_token": attempt.fencing_token,
                "instance": self.instance,
                "sha256": reported,
                "received_bytes": count,
                "duration_s": 1.0,
            },
        )

    async def _admit(self, request: Request) -> Response:
        attempt_id = _path_id(request)
        checked = self._check(request, "admit", attempt_id)
        if isinstance(checked, Response):
            return checked
        attempt = self.attempts.get(attempt_id)
        if attempt is None:
            return _error(409, "reception_not_held")
        if attempt.state != "held":
            return _error(409, "reception_not_held")
        if not self._bound(checked, attempt, attempt.held_token):
            return _error(401, "internal_credential_invalid")
        if attempt.faults.get("admit_unavailable"):
            return _error(503, "capacity_unavailable")
        body = await _json_or_none(request)
        if not _matches_held(body, attempt) or attempt.faults.get("admit_mismatch"):
            attempt.state = "discarded"
            attempt.discard_reason = "reception_integrity_failed"
            attempt.cleanup_status = "verified"
            return _error(409, "reception_integrity_failed")
        attempt.state = "admitted"
        self.asr_starts += 1
        return JSONResponse(status_code=202, content={"status": "transcribing"})

    async def _discard(self, request: Request) -> Response:
        attempt_id = _path_id(request)
        checked = self._check(request, "discard", attempt_id)
        if isinstance(checked, Response):
            return checked
        attempt = self.attempts.get(attempt_id)
        if attempt is None:
            return _error(409, "reception_not_held")
        if attempt.state in {"discarded", "failed"}:
            return _ok_cleanup(attempt)
        if attempt.state != "held" or not self._bound(checked, attempt, attempt.held_token):
            return _error(409, "reception_not_held")
        body = await _json_or_none(request)
        reason = body.get("reason") if isinstance(body, dict) else None
        if not isinstance(reason, str) or reason not in _DISCARD_REASONS:
            return _error(422, "validation_failed")
        attempt.state = "discarded"
        attempt.discard_reason = reason
        attempt.cleanup_status = "failed" if attempt.faults.get("discard_fails") else "verified"
        return _ok_cleanup(attempt)


def _ok_cleanup(attempt: FakeAttempt) -> JSONResponse:
    return JSONResponse(status_code=200, content={"cleanup_status": attempt.cleanup_status})


def _path_id(request: Request) -> uuid.UUID:
    try:
        return uuid.UUID(str(request.path_params["attempt_id"]))
    except (KeyError, ValueError):
        raise HTTPException(status_code=404) from None


async def _json_or_none(request: Request) -> object:
    try:
        return await request.json()
    except Exception:  # noqa: BLE001 — a malformed body is the integrity failure, not a crash
        return None


def _matches_held(body: object, attempt: FakeAttempt) -> bool:
    if not isinstance(body, dict):
        return False
    return (
        body.get("sha256") == attempt.sha256
        and body.get("received_bytes") == attempt.received_bytes
    )


class RunningFake:
    """A fake served by uvicorn on a background thread; `stop` joins it."""

    def __init__(self, fake: FakeIngest, port: int) -> None:
        self.fake = fake
        self.port = port
        config = uvicorn.Config(
            fake.app, host="127.0.0.1", port=port, log_level="warning", lifespan="off",
            access_log=False, ws="none",
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started:
            if time.monotonic() > deadline or not self._thread.is_alive():
                raise RuntimeError("fake ingest did not start")
            time.sleep(0.01)

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)


async def wait_until(predicate: Callable[[], bool], *, timeout_s: float = 5.0) -> bool:
    """Polls ``predicate`` until true; the fake records a disconnect a moment after the cut."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return predicate()


async def chunks_of(
    data: bytes, size: int, on_yield: Callable[[], None] | None = None
) -> AsyncIterator[bytes]:
    """Browser-side body generator. ``on_yield`` lets a test count how far it was read."""
    for offset in range(0, len(data), size):
        if on_yield is not None:
            on_yield()
        yield data[offset : offset + size]

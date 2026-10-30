"""Internal client from `api` to `ingest` (ADR-0008, A2.13).

`IngestReceptionSink` implements the `ReceptionSink` port over three internal requests:
a chunked `PUT` that streams the browser body (`receive`), and two JSON `POST`s that end the
hold (`admit`, `discard`). The body moves through a one-item queue, so `write()` waits until
`ingest` has taken the previous chunk: TCP backpressure reaches the browser. Nothing is
written to disk and no whole-file buffer exists.

The client never logs the credential, headers or body (ADR-0008 §10).
"""

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import settings
from app.services.ingest_credential import CredentialClaims, Op, mint
from app.services.uploads import (
    Admission,
    DiscardReason,
    Received,
    ReceptionSink,
    SinkError,
    UnavailableReceptionSink,
)

logger = logging.getLogger(__name__)

# ADR-0008 §0: four separate settings. Each one is its own constant so changing one does not
# silently move the others. `ingest` keeps the same assertion where the lease TTL lives.
CREDENTIAL_TTL_S = 30
HOP_IDLE_TIMEOUT_S = 30.0
HOLD_TIMEOUT_S = 30
HOP_CONNECT_TIMEOUT_S = 2.0
LEASE_TTL_S = 30  # S1.B3 lease TTL, as configured in ingest.
if CREDENTIAL_TTL_S > LEASE_TTL_S or HOLD_TIMEOUT_S > LEASE_TTL_S:
    # Raised, not asserted: `python -O` must not drop the guard.
    raise RuntimeError("hold_exceeds_lease")

DECLARED_BYTES_HEADER = "X-Maulwurf-Declared-Bytes"
_HOP_TIMEOUT = httpx.Timeout(
    connect=HOP_CONNECT_TIMEOUT_S,
    read=HOP_IDLE_TIMEOUT_S,
    write=HOP_IDLE_TIMEOUT_S,
    pool=HOP_IDLE_TIMEOUT_S,
)
# Used when an answer carries no error code: the status alone decides the catalog entry.
_STATUS_CODES = {
    409: "attempt_not_active",
    413: "payload_too_large",
    415: "unsupported_format",
    503: "capacity_unavailable",
}

_shared_client: httpx.AsyncClient | None = None


def get_ingest_client() -> httpx.AsyncClient:
    """Process-wide client; the lifespan closes it on shutdown."""
    global _shared_client
    if _shared_client is None:
        _shared_client = httpx.AsyncClient(timeout=_HOP_TIMEOUT)
    return _shared_client


async def close_ingest_client() -> None:
    global _shared_client
    client = _shared_client
    _shared_client = None
    if client is not None:
        await client.aclose()


@dataclass
class _Reception:
    """Per-request state of one reception, from `begin` to `admit` or `discard`."""

    attempt_id: uuid.UUID
    audio_id: uuid.UUID
    user_id: uuid.UUID
    fencing_token: int
    queue: "asyncio.Queue[bytes | None]"
    task: "asyncio.Task[httpx.Response]"
    next_fencing_token: int | None = None
    instance: str | None = None
    received: Received | None = None


async def _chunks(queue: "asyncio.Queue[bytes | None]") -> AsyncIterator[bytes]:
    """Feeds the request body; `None` ends it. One queued chunk is the only buffer."""
    while True:
        chunk = await queue.get()
        if chunk is None:
            return
        yield chunk


class IngestReceptionSink:
    """`ReceptionSink` that forwards the reception to `ingest` over the internal network."""

    def __init__(
        self, *, base_url: str, secret: bytes, kid: str, client: httpx.AsyncClient
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._secret = secret
        self._kid = kid
        self._client = client
        self._reception: _Reception | None = None

    async def begin(
        self,
        *,
        user_id: uuid.UUID,
        audio_id: uuid.UUID,
        attempt_id: uuid.UUID,
        fencing_token: int,
        declared_bytes: int | None,
    ) -> None:
        token = self._token(
            "receive", attempt_id, audio_id, user_id, fencing_token, instance=None
        )
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/octet-stream"}
        if declared_bytes is not None:
            headers[DECLARED_BYTES_HEADER] = str(declared_bytes)
        queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=1)
        task = asyncio.create_task(
            self._client.put(
                f"{self._base_url}/internal/v1/receptions/{attempt_id}",
                content=_chunks(queue),
                headers=headers,
            )
        )
        self._reception = _Reception(
            attempt_id=attempt_id,
            audio_id=audio_id,
            user_id=user_id,
            fencing_token=fencing_token,
            queue=queue,
            task=task,
        )

    async def write(self, chunk: bytes) -> None:
        await self._hand_over(self._require(), chunk)

    async def finish(self) -> Received:
        reception = self._require()
        await self._hand_over(reception, None)
        response = await self._await_answer(reception)
        if response.status_code != 200:
            raise self._error(response, reception)
        try:
            body: dict[str, Any] = response.json()
            reception.next_fencing_token = int(body["fencing_token"])
            reception.instance = str(body["instance"])
            reception.received = Received(
                sha256=str(body["sha256"]), received_bytes=int(body["received_bytes"])
            )
        except (ValueError, KeyError, TypeError):
            raise SinkError("internal_error") from None
        return reception.received

    async def abort(self) -> None:
        """Cancels the internal request so `ingest` sees the body end. Never raises."""
        if self._reception is None:
            return
        task = self._reception.task
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def discard(self, reason: DiscardReason) -> None:
        reception = self._require_held()
        token = self._token(
            "discard",
            reception.attempt_id,
            reception.audio_id,
            reception.user_id,
            self._held_token(reception),
            instance=reception.instance,
        )
        try:
            response = await self._client.post(
                f"{self._base_url}/internal/v1/receptions/{reception.attempt_id}/discard",
                json={"reason": reason},
                headers=_auth_header(token),
            )
        except httpx.TransportError:
            raise SinkError("internal_error") from None
        if response.status_code != 200 or _cleanup_status(response) != "verified":
            raise SinkError("internal_error")

    async def admit(self) -> Admission:
        reception = self._require_held()
        received = reception.received
        if received is None:
            raise SinkError("internal_error")
        token = self._token(
            "admit",
            reception.attempt_id,
            reception.audio_id,
            reception.user_id,
            self._held_token(reception),
            instance=reception.instance,
        )
        body = {"sha256": received.sha256, "received_bytes": received.received_bytes}
        try:
            response = await self._client.post(
                f"{self._base_url}/internal/v1/receptions/{reception.attempt_id}/admit",
                json=body,
                headers=_auth_header(token),
            )
        except httpx.TransportError:
            raise SinkError("capacity_unavailable") from None
        if response.status_code != 202:
            raise self._error(response, reception)
        return Admission(status=_admitted_status(response))

    async def _hand_over(self, reception: _Reception, chunk: bytes | None) -> None:
        """Queues one chunk or the end marker, unless the request has already ended."""
        if not reception.task.done():
            put = asyncio.ensure_future(reception.queue.put(chunk))
            done, _ = await asyncio.wait(
                {put, reception.task}, return_when=asyncio.FIRST_COMPLETED
            )
            if put in done:
                return
            put.cancel()
        error = await self._early_end(reception)
        raise error

    async def _early_end(self, reception: _Reception) -> SinkError:
        """`ingest` answered, or the connection failed, before the body was complete."""
        response = await self._await_answer(reception)
        if response.status_code == 200:
            return SinkError("internal_error")  # a 200 can only follow the end marker
        return self._error(response, reception)

    async def _await_answer(self, reception: _Reception) -> httpx.Response:
        try:
            return await reception.task
        except httpx.TransportError:
            raise SinkError("capacity_unavailable") from None

    def _error(self, response: httpx.Response, reception: _Reception) -> SinkError:
        if response.status_code == 401:
            logger.error("ingest.credential_rejected attempt_id=%s", reception.attempt_id)
            return SinkError("internal_error")
        return SinkError(_error_code(response))

    def _token(
        self,
        op: Op,
        attempt_id: uuid.UUID,
        audio_id: uuid.UUID,
        user_id: uuid.UUID,
        fencing_token: int,
        *,
        instance: str | None,
    ) -> str:
        claims = CredentialClaims(
            op=op,
            attempt_id=attempt_id,
            audio_id=audio_id,
            user_id=user_id,
            fencing_token=fencing_token,
            instance=instance,
            expires_at=int(time.time()) + CREDENTIAL_TTL_S,
        )
        return mint(claims, kid=self._kid, secret=self._secret)

    def _held_token(self, reception: _Reception) -> int:
        if reception.next_fencing_token is None:
            raise SinkError("internal_error")
        return reception.next_fencing_token

    def _require(self) -> _Reception:
        if self._reception is None:
            raise SinkError("internal_error")
        return self._reception

    def _require_held(self) -> _Reception:
        reception = self._require()
        if reception.instance is None:
            raise SinkError("internal_error")
        return reception


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _error_code(response: httpx.Response) -> str:
    """The catalog code in the body, else the one implied by the status."""
    with suppress(ValueError, KeyError, TypeError):
        code = response.json()["error"]["code"]
        if isinstance(code, str):
            return code
    return _STATUS_CODES.get(response.status_code, "internal_error")


def _json_object(response: httpx.Response) -> dict[str, Any] | None:
    with suppress(ValueError):
        body: object = response.json()
        return body if isinstance(body, dict) else None
    return None


def _cleanup_status(response: httpx.Response) -> str | None:
    body = _json_object(response)
    status = body.get("cleanup_status") if body is not None else None
    return status if isinstance(status, str) else None


def _admitted_status(response: httpx.Response) -> str:
    body = _json_object(response)
    status = body.get("status") if body is not None else None
    if not isinstance(status, str):
        raise SinkError("internal_error")
    return status


def get_reception_sink() -> ReceptionSink:
    """Dependency: the ingest sink when both settings are present, else the 503 sink."""
    url = settings.ingest_url
    secret = settings.ingest_credential_secret
    if url is None or secret is None:
        return UnavailableReceptionSink()
    return IngestReceptionSink(
        base_url=str(url),
        secret=bytes.fromhex(secret.get_secret_value().strip()),
        kid=settings.ingest_credential_kid,
        client=get_ingest_client(),
    )

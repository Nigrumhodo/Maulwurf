"""Cliente Riva bloqueante, aislado del event loop (S2.4).

Los reintentos caben en el deadline del intento: como máximo tres llamadas, con
backoff y jitter. Auth, idioma y formato inválido no se reintentan. Agotar el
plazo pide reupload. El transporte real no se construye en las pruebas; la clave
no se registra.
"""

from __future__ import annotations

import asyncio
import math
import os
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from maulwurf_ingest.audio.reconcile import LocalWord

MAX_ATTEMPTS = 3
# Presupuesto entre llamadas. No es una cuota medida de Riva.
_BACKOFF_BASE_S = 0.2
_MS_TO_S = 0.001
_TRANSIENT = frozenset({"UNAVAILABLE", "DEADLINE_EXCEEDED", "RESOURCE_EXHAUSTED"})
_AUTH = frozenset({"UNAUTHENTICATED", "PERMISSION_DENIED"})
_INVALID = frozenset({"INVALID_ARGUMENT"})
_DEFAULT_SERVER = "grpc.nvcf.nvidia.com:443"
_DEFAULT_FUNCTION_ID = "b702f636-f60c-4a3d-a6f4-f3568c13bd7d"
_CLIENT_MAX_MESSAGE_LENGTH = 1_073_741_824


class AsrReupload(RuntimeError):
    """El plazo o los reintentos se agotaron. Hay que volver a subir el audio."""


class AsrRejected(RuntimeError):
    """Auth, idioma o formato. No es transitorio."""


class TransportError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Transcript:
    text: str
    words: tuple[LocalWord, ...]
    precision: str


class RecognizeCall(Protocol):
    def result(self, timeout: float) -> Transcript: ...

    def cancel(self) -> None: ...


class RivaTransport(Protocol):
    def start(self, wav: bytes, *, language_code: str, deadline_s: float) -> RecognizeCall: ...


class TranscriptionService(Protocol):
    def transcribe(self, wav: bytes, *, language_code: str, deadline_s: float) -> Transcript: ...


def _unit_interval() -> float:
    return secrets.randbelow(1_000_000) / 1_000_000


class NvidiaRivaTranscriptionService:
    """Adaptador. `transcribe` bloquea; `transcribe_async` lo saca del event loop."""

    def __init__(
        self,
        transport: RivaTransport,
        *,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        jitter: Callable[[], float] = _unit_interval,
        cancel: threading.Event | None = None,
    ) -> None:
        self._transport = transport
        self._sleep = sleeper
        self._clock = clock
        self._jitter = jitter
        self._cancel = threading.Event() if cancel is None else cancel

    def transcribe(self, wav: bytes, *, language_code: str, deadline_s: float) -> Transcript:
        _reject_language(language_code)
        if not math.isfinite(deadline_s) or deadline_s <= 0:
            raise AsrReupload("requires_reupload")
        deadline = self._clock() + deadline_s
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._ensure_running(deadline)
            remaining = deadline - self._clock()
            call = self._transport.start(wav, language_code=language_code, deadline_s=remaining)
            if self._cancel.is_set():
                call.cancel()
                raise AsrReupload("requires_reupload")
            try:
                return call.result(timeout=remaining)
            except TransportError as exc:
                self._raise_terminal(exc)
                if attempt >= MAX_ATTEMPTS or not self._pause(attempt, deadline):
                    raise AsrReupload("requires_reupload") from exc
        raise AsrReupload("requires_reupload")

    async def transcribe_async(
        self, wav: bytes, *, language_code: str, deadline_s: float
    ) -> Transcript:
        return await asyncio.to_thread(
            self.transcribe, wav, language_code=language_code, deadline_s=deadline_s
        )

    def _ensure_running(self, deadline: float) -> None:
        if self._cancel.is_set() or self._clock() >= deadline:
            raise AsrReupload("requires_reupload")

    def _raise_terminal(self, exc: TransportError) -> None:
        if exc.code == "CANCELLED":
            raise AsrReupload("requires_reupload") from exc
        if exc.code in _AUTH or exc.code in _INVALID or exc.code not in _TRANSIENT:
            raise AsrRejected(exc.code) from exc

    def _pause(self, attempt: int, deadline: float) -> bool:
        delay = _backoff(attempt, self._jitter)
        if self._cancel.is_set() or self._clock() + delay >= deadline:
            return False
        self._sleep(delay)
        return True


def _reject_language(language_code: str) -> None:
    code = language_code.strip()
    folded = code.casefold()
    if not code or folded == "multi" or "translate" in folded:
        raise AsrRejected("invalid_language")


def _backoff(attempt: int, jitter: Callable[[], float]) -> float:
    base = _BACKOFF_BASE_S * float(1 << (attempt - 1))
    drawn = jitter()
    if drawn < 0.0:
        drawn = 0.0
    elif drawn > 1.0:
        drawn = 1.0
    return base + (drawn * base)


class GrpcRivaTransport:
    """Canal TLS real. Las pruebas no lo construyen."""

    def __init__(self, *, api_key: str, server: str, function_id: str) -> None:
        self._api_key = api_key
        self._server = server
        self._function_id = function_id

    def __repr__(self) -> str:
        return "GrpcRivaTransport()"

    def start(self, wav: bytes, *, language_code: str, deadline_s: float) -> RecognizeCall:
        import riva.client

        auth = riva.client.Auth(
            use_ssl=True,
            uri=self._server,
            metadata_args=[
                ["function-id", self._function_id],
                ["authorization", "Bearer " + self._api_key],
            ],
            options=[
                ("grpc.max_send_message_length", _CLIENT_MAX_MESSAGE_LENGTH),
                ("grpc.max_receive_message_length", _CLIENT_MAX_MESSAGE_LENGTH),
            ],
        )
        try:
            config = riva.client.RecognitionConfig(
                language_code=language_code,
                max_alternatives=1,
                profanity_filter=False,
                enable_automatic_punctuation=False,
                verbatim_transcripts=True,
                enable_word_time_offsets=True,
            )
            call = riva.client.ASRService(auth).offline_recognize(wav, config, future=True)
        except Exception:
            _close_channel(auth)
            raise
        return _GrpcCall(call, auth, deadline_s)


class _GrpcCall:
    def __init__(self, call: object, auth: object, deadline_s: float) -> None:
        self._call = call
        self._auth = auth
        self._deadline_s = deadline_s
        self._closed = False

    def result(self, timeout: float) -> Transcript:
        import grpc

        wait_s = min(timeout, self._deadline_s)
        try:
            response = self._call.result(timeout=wait_s)  # type: ignore[attr-defined]
        except grpc.FutureTimeoutError:
            self.cancel()
            raise TransportError("DEADLINE_EXCEEDED") from None
        except grpc.FutureCancelledError:
            raise TransportError("CANCELLED") from None
        except grpc.RpcError as exc:
            raise TransportError(_status_name(exc)) from None
        except TimeoutError:
            raise TransportError("DEADLINE_EXCEEDED") from None
        else:
            return _transcript_from(response)
        finally:
            self._close()

    def cancel(self) -> None:
        cancel = getattr(self._call, "cancel", None)
        if callable(cancel):
            cancel()
        self._close()

    def _close(self) -> None:
        if self._closed:
            return
        self._closed = True
        _close_channel(self._auth)


def riva_transport_from_env() -> GrpcRivaTransport:
    key = os.environ.get("NVIDIA_API_KEY", "").strip()
    if not key:
        raise RuntimeError("riva_unconfigured")
    if os.environ.get("RIVA_USE_SSL", "true").strip().lower() != "true":
        raise RuntimeError("riva_tls_required")
    server = os.environ.get("RIVA_SERVER", _DEFAULT_SERVER).strip() or _DEFAULT_SERVER
    function_id = os.environ.get("RIVA_FUNCTION_ID", _DEFAULT_FUNCTION_ID).strip()
    if not function_id:
        function_id = _DEFAULT_FUNCTION_ID
    return GrpcRivaTransport(api_key=key, server=server, function_id=function_id)


def _close_channel(auth: object) -> None:
    channel = getattr(auth, "channel", None)
    close = getattr(channel, "close", None)
    if callable(close):
        close()


def _status_name(exc: BaseException) -> str:
    code = getattr(exc, "code", None)
    if callable(code):
        try:
            status = code()
        except Exception:
            status = None
        name = getattr(status, "name", None)
        if isinstance(name, str) and name:
            return name
    return "UNKNOWN"


def _transcript_from(response: object) -> Transcript:
    text_parts: list[str] = []
    words: list[LocalWord] = []
    results = getattr(response, "results", None) or []
    for result in results:
        alternatives = getattr(result, "alternatives", None) or []
        if not alternatives:
            continue
        alternative = alternatives[0]
        text_parts.append(str(getattr(alternative, "transcript", "") or ""))
        for word in getattr(alternative, "words", None) or []:
            parsed = _word_from(word)
            if parsed is not None:
                words.append(parsed)
    if not words:
        return Transcript("".join(text_parts), (), "none")
    return Transcript("".join(text_parts), tuple(words), "word")


def _word_from(word: object) -> LocalWord | None:
    text = str(getattr(word, "word", "") or "").strip()
    start = getattr(word, "start_time", None)
    end = getattr(word, "end_time", None)
    if not text or isinstance(start, bool) or isinstance(end, bool):
        return None
    if not isinstance(start, int | float) or not isinstance(end, int | float):
        return None
    start_s = float(start) * _MS_TO_S
    end_s = float(end) * _MS_TO_S
    if end_s <= start_s:
        return None
    return LocalWord(text, start_s, end_s)

"""Blocking Riva client, kept off the event loop (S2.4).

Retries stay inside the attempt deadline, with exponential backoff and jitter capped
at ``max_backoff_s``. The call budget is ``max_attempts`` per fragment: the class
default is ``MAX_ATTEMPTS`` (3), while the production ASR job
(``asr_job._PROD_RIVA_MAX_ATTEMPTS``) passes 6 calls with a 1 s backoff base. Only
transient codes are retried; auth, language and invalid format are not. Running out
of deadline, attempts or a cancel asks for a reupload. Tests never build the real
transport, and the key is never logged.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import secrets
import ssl
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from maulwurf_ingest.audio.reconcile import Word, parse_confidence
from maulwurf_ingest.config import settings
from maulwurf_ingest.logs import RedactionFilter

MAX_ATTEMPTS = 3
# Presupuesto entre llamadas. No es una cuota medida de Riva.
_BACKOFF_BASE_S = 0.2
_DEFAULT_MAX_BACKOFF_S = 15.0
_DEFAULT_PER_CALL_TIMEOUT_S = 90.0
_MS_TO_S = 0.001
_TRANSIENT = frozenset({"UNAVAILABLE", "DEADLINE_EXCEEDED", "RESOURCE_EXHAUSTED"})
_AUTH = frozenset({"UNAUTHENTICATED", "PERMISSION_DENIED"})
_INVALID = frozenset({"INVALID_ARGUMENT"})
_DEFAULT_SERVER = "grpc.nvcf.nvidia.com:443"
_DEFAULT_FUNCTION_ID = "b702f636-f60c-4a3d-a6f4-f3568c13bd7d"
_SERVER_RE = re.compile(
    r"\A(?P<host>[A-Za-z0-9._-]+|\[[0-9A-Fa-f:.]+\])\:(?P<port>[0-9]{1,5})\Z"
)
_SSL_TRUE = frozenset({"true", "1", "yes"})
_SSL_FALSE = frozenset({"false", "0", "no"})
_CLIENT_MAX_MESSAGE_LENGTH = 1_073_741_824
_HTTP2_RESET_MARKERS = (
    "rst_stream",
    "goaway",
    "socket closed",
    "broken pipe",
    "connection reset",
    "transport closing",
    "unexpected eof",
    "recvmsg",
)


def validate_riva_server(server: str) -> str:
    """Accept only ``host:port``. A scheme or path is a different endpoint."""
    candidate = server.strip()
    if not candidate or any(char.isspace() for char in candidate):
        raise RuntimeError("riva_server_invalid")
    match = _SERVER_RE.fullmatch(candidate)
    if match is None:
        raise RuntimeError("riva_server_invalid")
    port = int(match.group("port"))
    if port < 1 or port > 65535:
        raise RuntimeError("riva_server_invalid")
    host = match.group("host")
    if host.startswith(".") or host.endswith(".") or ".." in host:
        raise RuntimeError("riva_server_invalid")
    return candidate


def _environment_name() -> str:
    raw = os.environ.get("INGEST_ENV")
    if raw is None:
        raw = settings.environment
    return raw.strip().casefold()


def _system_store_verified() -> bool:
    try:
        context = ssl.create_default_context()
    except ssl.SSLError:
        return False
    count = context.cert_store_stats().get("x509", 0)
    return isinstance(count, int) and count > 0


def resolve_riva_tls(*, use_ssl: bool, ca_file: str | None) -> str | None:
    """Return an explicit CA path, or None for the verified system store.

    Plaintext is refused outside ``INGEST_ENV=local``. A ``GRPC_DEFAULT_SSL_ROOTS_FILE_PATH``
    that is not the explicit CA is a silent trust downgrade and is refused.
    """
    if not use_ssl:
        if _environment_name() != "local":
            raise RuntimeError("riva_tls_required")
        return None
    explicit = (ca_file or "").strip() or os.environ.get("INGEST_RIVA_CA_FILE", "").strip()
    if not explicit:
        configured = settings.riva_ca_file
        explicit = configured.strip() if configured else ""
    override = os.environ.get("GRPC_DEFAULT_SSL_ROOTS_FILE_PATH", "").strip()
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise RuntimeError("riva_ca_unreadable")
        if override:
            try:
                same = Path(override).resolve() == path.resolve()
            except OSError:
                same = False
            if not same:
                raise RuntimeError("riva_roots_overridden")
        return str(path)
    if override:
        raise RuntimeError("riva_roots_overridden")
    if not _system_store_verified():
        raise RuntimeError("riva_system_store_missing")
    return None


def _use_ssl_from_env() -> bool:
    raw = os.environ.get("RIVA_USE_SSL")
    if raw is None:
        return True
    value = raw.strip().casefold()
    if value in _SSL_TRUE:
        return True
    if value in _SSL_FALSE:
        return False
    # Anything else is not an explicit opt-in to TLS. Fail closed.
    raise RuntimeError("riva_tls_required")


def silence_grpc_metadata_trace() -> None:
    """Keep the bearer out of gRPC traces.

    The C-core tracer writes call metadata to stderr, outside the Python redacting
    handler, when ``GRPC_TRACE`` or a debug verbosity is set. Pin both off before the
    channel exists, and scrub any ``grpc`` logger record that still carries a bearer.
    """
    os.environ["GRPC_VERBOSITY"] = "ERROR"
    os.environ["GRPC_TRACE"] = ""
    grpc_logger = logging.getLogger("grpc")
    if not any(isinstance(item, RedactionFilter) for item in grpc_logger.filters):
        grpc_logger.addFilter(RedactionFilter())


class AsrReupload(RuntimeError):
    """El plazo o los reintentos se agotaron. Hay que volver a subir el audio."""

    def __init__(
        self, message: str = "requires_reupload", *, cause_code: str | None = None
    ) -> None:
        super().__init__(message)
        self.cause_code = cause_code


class AsrRejected(RuntimeError):
    """Auth, idioma o formato. No es transitorio."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.cause_code = message


class TransportError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Transcript:
    text: str
    words: tuple[Word, ...]
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
        max_attempts: int = MAX_ATTEMPTS,
        backoff_base_s: float = _BACKOFF_BASE_S,
        max_backoff_s: float = _DEFAULT_MAX_BACKOFF_S,
    ) -> None:
        self._transport = transport
        self._sleep = sleeper
        self._clock = clock
        self._jitter = jitter
        self._cancel = threading.Event() if cancel is None else cancel
        self._max_attempts = max(1, max_attempts)
        self._backoff_base_s = backoff_base_s
        self._max_backoff_s = max_backoff_s
        self._call_lock = threading.Lock()
        self._inflight: RecognizeCall | None = None

    def transcribe(self, wav: bytes, *, language_code: str, deadline_s: float) -> Transcript:
        _reject_language(language_code)
        if not math.isfinite(deadline_s) or deadline_s <= 0:
            raise AsrReupload("requires_reupload", cause_code="DEADLINE_EXCEEDED")
        deadline = self._clock() + deadline_s
        for attempt in range(1, self._max_attempts + 1):
            self._ensure_running(deadline)
            remaining = deadline - self._clock()
            call = self._transport.start(wav, language_code=language_code, deadline_s=remaining)
            with self._call_lock:
                if self._cancel.is_set():
                    call.cancel()
                    raise AsrReupload("requires_reupload", cause_code="CANCELLED")
                self._inflight = call
            try:
                try:
                    return call.result(timeout=remaining)
                except TransportError as exc:
                    self._raise_terminal(exc)
                    if attempt >= self._max_attempts or not self._pause(attempt, deadline):
                        raise AsrReupload("requires_reupload", cause_code=exc.code) from exc
            finally:
                with self._call_lock:
                    if self._inflight is call:
                        self._inflight = None
        raise AsrReupload("requires_reupload")

    async def transcribe_async(
        self, wav: bytes, *, language_code: str, deadline_s: float
    ) -> Transcript:
        try:
            return await asyncio.to_thread(
                self.transcribe, wav, language_code=language_code, deadline_s=deadline_s
            )
        except asyncio.CancelledError:
            self.cancel_nowait()
            raise

    def cancel_nowait(self) -> None:
        """Corta el RPC en vuelo. `to_thread` no interrumpe el hilo por sí solo."""
        self._cancel.set()
        with self._call_lock:
            call = self._inflight
        if call is not None:
            call.cancel()

    def _ensure_running(self, deadline: float) -> None:
        if self._cancel.is_set() or self._clock() >= deadline:
            raise AsrReupload("requires_reupload")

    def _raise_terminal(self, exc: TransportError) -> None:
        if exc.code == "CANCELLED":
            raise AsrReupload("requires_reupload", cause_code=exc.code) from exc
        if exc.code in _AUTH or exc.code in _INVALID:
            raise AsrRejected(exc.code) from exc
        if exc.code not in _TRANSIENT:
            raise AsrReupload("requires_reupload", cause_code=exc.code) from exc

    def _pause(self, attempt: int, deadline: float) -> bool:
        delay = _backoff(
            attempt,
            self._jitter,
            base_s=self._backoff_base_s,
            max_s=self._max_backoff_s,
        )
        if self._cancel.is_set() or self._clock() + delay >= deadline:
            return False
        self._sleep(delay)
        return True


def _reject_language(language_code: str) -> None:
    code = language_code.strip()
    folded = code.casefold()
    if not code or folded == "multi" or "translate" in folded:
        raise AsrRejected("invalid_language")


def _backoff(
    attempt: int,
    jitter: Callable[[], float],
    *,
    base_s: float = _BACKOFF_BASE_S,
    max_s: float = _DEFAULT_MAX_BACKOFF_S,
) -> float:
    base = min(base_s * float(1 << (attempt - 1)), max_s)
    drawn = jitter()
    if drawn < 0.0:
        drawn = 0.0
    elif drawn > 1.0:
        drawn = 1.0
    return base + (drawn * base)


class GrpcRivaTransport:
    """Un canal TLS para todo el transporte. Las pruebas no lo abren contra NVIDIA."""

    def __init__(
        self,
        *,
        api_key: str,
        server: str,
        function_id: str,
        per_call_timeout_s: float = _DEFAULT_PER_CALL_TIMEOUT_S,
        use_ssl: bool = True,
        ca_file: str | None = None,
    ) -> None:
        self._api_key = api_key
        self._server = validate_riva_server(server)
        self._function_id = function_id
        self._per_call_timeout_s = per_call_timeout_s
        self._use_ssl = use_ssl
        self._ca_file = ca_file
        if not use_ssl and _environment_name() != "local":
            raise RuntimeError("riva_tls_required")
        self._auth: object | None = None
        self._asr: object | None = None

    def __repr__(self) -> str:
        return "GrpcRivaTransport()"

    def start(self, wav: bytes, *, language_code: str, deadline_s: float) -> RecognizeCall:
        import riva.client

        config = riva.client.RecognitionConfig(
            language_code=language_code,
            max_alternatives=1,
            profanity_filter=False,
            enable_automatic_punctuation=True,
            verbatim_transcripts=True,
            enable_word_time_offsets=True,
        )
        call = self._service().offline_recognize(wav, config, future=True)  # type: ignore[attr-defined]
        per_call = min(deadline_s, self._per_call_timeout_s)
        return _GrpcCall(call, per_call, on_transient_error=self.close)

    def close(self) -> None:
        auth = self._auth
        self._auth = None
        self._asr = None
        if auth is not None:
            _close_channel(auth)

    def _service(self) -> object:
        if self._asr is not None:
            return self._asr
        import riva.client

        silence_grpc_metadata_trace()
        ca_file = resolve_riva_tls(use_ssl=self._use_ssl, ca_file=self._ca_file)
        auth = riva.client.Auth(
            use_ssl=self._use_ssl,
            ssl_root_cert=ca_file,
            uri=self._server,
            metadata_args=[
                ["function-id", self._function_id],
                ["authorization", "Bearer " + self._api_key],
            ],
            options=[
                ("grpc.max_send_message_length", _CLIENT_MAX_MESSAGE_LENGTH),
                ("grpc.max_receive_message_length", _CLIENT_MAX_MESSAGE_LENGTH),
                ("grpc.keepalive_time_ms", 30_000),
                ("grpc.keepalive_timeout_ms", 10_000),
                ("grpc.keepalive_permit_without_calls", 1),
                ("grpc.http2.max_pings_without_data", 0),
            ],
        )
        try:
            asr = riva.client.ASRService(auth)
        except Exception:
            _close_channel(auth)
            raise
        self._auth = auth
        self._asr = asr
        return asr


class _GrpcCall:
    """Una llamada. Reutiliza el canal, pero lo reinicia ante errores transitorios de transporte."""

    def __init__(
        self,
        call: object,
        deadline_s: float,
        *,
        on_transient_error: Callable[[], None] | None = None,
    ) -> None:
        self._call = call
        self._deadline_s = deadline_s
        self._on_transient_error = on_transient_error

    def _reset_transport(self) -> None:
        if self._on_transient_error is not None:
            self._on_transient_error()

    def result(self, timeout: float) -> Transcript:
        import grpc

        wait_s = min(timeout, self._deadline_s)
        try:
            response = self._call.result(timeout=wait_s)  # type: ignore[attr-defined]
        except grpc.FutureTimeoutError:
            self.cancel()
            self._reset_transport()
            raise TransportError("DEADLINE_EXCEEDED") from None
        except grpc.FutureCancelledError:
            raise TransportError("CANCELLED") from None
        except grpc.RpcError as exc:
            code = _status_name(exc)
            if code == "ABORTED" or (code in {"INTERNAL", "UNKNOWN"} and _is_http2_reset(exc)):
                code = "UNAVAILABLE"
            if code in _TRANSIENT:
                self._reset_transport()
            raise TransportError(code) from None
        except TimeoutError:
            self.cancel()
            self._reset_transport()
            raise TransportError("DEADLINE_EXCEEDED") from None
        else:
            return _transcript_from(response)

    def cancel(self) -> None:
        cancel = getattr(self._call, "cancel", None)
        if callable(cancel):
            cancel()


def riva_transport_from_env() -> GrpcRivaTransport:
    key = os.environ.get("NVIDIA_API_KEY", "").strip()
    if not key:
        raise RuntimeError("riva_unconfigured")
    use_ssl = _use_ssl_from_env()
    server = validate_riva_server(
        os.environ.get("RIVA_SERVER", _DEFAULT_SERVER).strip() or _DEFAULT_SERVER
    )
    function_id = os.environ.get("RIVA_FUNCTION_ID", _DEFAULT_FUNCTION_ID).strip()
    if not function_id:
        function_id = _DEFAULT_FUNCTION_ID
    ca_file = os.environ.get("INGEST_RIVA_CA_FILE", "").strip() or None
    # Fail at construction: a bad server, plaintext outside local, or a swapped
    # root store must not wait until the first audio byte.
    resolve_riva_tls(use_ssl=use_ssl, ca_file=ca_file)
    return GrpcRivaTransport(
        api_key=key, server=server, function_id=function_id, use_ssl=use_ssl, ca_file=ca_file,
    )


def _close_channel(auth: object) -> None:
    channel = getattr(auth, "channel", None)
    close = getattr(channel, "close", None)
    if callable(close):
        close()


def _is_http2_reset(exc: BaseException) -> bool:
    details = getattr(exc, "details", None)
    if not callable(details):
        return False
    try:
        text = details()
    except Exception:
        return False
    if not isinstance(text, str):
        return False
    lowered = text.casefold()
    return any(marker in lowered for marker in _HTTP2_RESET_MARKERS)


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
    words: list[Word] = []
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
    text = " ".join(part.strip() for part in text_parts if part.strip())
    if not words:
        return Transcript(text, (), "none")
    return Transcript(text, tuple(words), "word")


def _word_from(word: object) -> Word | None:
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
    raw_confidence = getattr(word, "confidence", None)
    # parse_confidence maps the protobuf default 0.0 to unknown on this path too.
    confidence = parse_confidence(raw_confidence)
    return Word(text, start_s, end_s, confidence)

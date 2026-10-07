"""A2.4 sin Postgres: recepción en streaming, corte, compensación y guardia «sin spool».

El cuerpo del `PUT` nunca se materializa (spec M2): la guardia AST de abajo prohibió en
los routers `UploadFile`/`File(...)`/`request.body()` y `SpooledTemporaryFile`, y exige que
el router delegue la lectura en `services/uploads.receive_content`, el único dueño de
`request.stream()`. El resto cubre lo que no necesita BD: corte en caliente contando bytes,
SHA-256 incremental, «limpiar antes de responder», compensación de un `admit()` fallido,
desconexión a mitad de subida y el mapeo de códigos del sink al catálogo. El dedupe contra
el índice único `audios_dedupe_identity` vive en `tests/integration/
test_put_content_dedupe.py` (I-S2-AN-05). Sin marker `integration`: corre en `python-unit`.
"""

import ast
import hashlib
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

import app.routers
import app.services.uploads as uploads
from app.core.config import settings
from app.core.errors import ApiError
from app.models import Audio, IngestionAttempt
from app.services import dedupe
from app.services.audios import RETRY_AFTER_SECONDS
from app.services.uploads import Admission, SinkError, effective_upload_limit, receive_content

CONTENT = b"RIFF" + b"\x00" * 60
# Identificadores que jamás deben aparecer en el código de recepción (spec M2, «sin spool»).
_FORBIDDEN = frozenset({"UploadFile", "SpooledTemporaryFile", "NamedTemporaryFile", "tempfile"})


def _forbidden_hits(path: Path) -> set[str]:
    """Uso real (nombres en el AST), no texto: los docstrings pueden mencionar lo prohibido."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in _FORBIDDEN:
            hits.add(node.id)
        elif isinstance(node, ast.Attribute) and node.attr == "body":
            hits.add("request.body()")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "File":
                hits.add("File(...)")
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            hits.update(alias.name for alias in node.names if alias.name in _FORBIDDEN)
    return hits


def test_router_delegates_streaming_and_nobody_spools_to_disk() -> None:
    router_path = Path(app.routers.__file__).parent / "audios.py"
    uploads_path = Path(uploads.__file__)

    assert _forbidden_hits(router_path) == set()
    assert _forbidden_hits(uploads_path) == set()

    # El router no recorre el stream: solo valida y delega (y no hace SQL, ADR-0006).
    router_tree = ast.parse(router_path.read_text(encoding="utf-8"))
    router_attrs = {
        node.attr for node in ast.walk(router_tree) if isinstance(node, ast.Attribute)
    }
    assert "stream" not in router_attrs and "body" not in router_attrs
    assert any(
        isinstance(node, ast.Name) and node.id == "receive_content"
        for node in ast.walk(router_tree)
    )

    # `services/uploads` es el único que recorre `request.stream()`.
    uploads_tree = ast.parse(uploads_path.read_text(encoding="utf-8"))
    assert any(
        isinstance(node, ast.Attribute) and node.attr == "stream"
        for node in ast.walk(uploads_tree)
    )


# --- Dobles locales (sin PG ni HTTP) ----------------------------------------------------


class _Sink:
    """`ReceptionSink` de mentira: bytes solo en RAM y eventos ordenados."""

    def __init__(self, *, begin_error: str | None = None, admit_error: str | None = None) -> None:
        self.events: list[str] = []
        self.written = 0
        self.retained = bytearray()
        self._begin_error = begin_error
        self._admit_error = admit_error

    async def begin(
        self,
        *,
        user_id: uuid.UUID,
        audio_id: uuid.UUID,
        attempt_id: uuid.UUID,
        expected_bytes: int | None,
    ) -> None:
        self.events.append("begin")
        if self._begin_error is not None:
            raise SinkError(self._begin_error)

    async def write(self, chunk: bytes) -> None:
        self.events.append("write")
        self.written += len(chunk)
        self.retained += chunk

    async def finish(self) -> None:
        self.events.append("finish")

    async def discard(self) -> None:
        self.events.append("discard")
        self.retained.clear()

    async def admit(self) -> Admission:
        self.events.append("admit")
        if self._admit_error is not None:
            raise SinkError(self._admit_error)
        self.retained.clear()  # el supervisor se lo queda: ya no «espera decisión»
        return Admission(status="transcribing")


class _FakeRequest:
    """`Request` de mentira: solo expone `stream()`, con fallo opcional al final."""

    def __init__(self, chunks: list[bytes], *, fail_after: Exception | None = None) -> None:
        self._chunks = chunks
        self._fail_after = fail_after
        self.consumed = 0

    async def stream(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            self.consumed += 1
            yield chunk
        if self._fail_after is not None:
            raise self._fail_after


class _StubDb:
    """`AsyncSession` de mentira para los caminos que solo hacen `commit`/`rollback`."""

    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


def _db() -> AsyncSession:
    return cast(AsyncSession, _StubDb())


def _audio(**fields: Any) -> Audio:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "subject_id": uuid.uuid4(),
        "language_code": "es",
        "class_date": "2026-09-21",
        "class_timezone": "America/Bogota",
        "sha256": None,
        "original_bytes": None,
    }
    values.update(fields)
    return cast(Audio, SimpleNamespace(**values))


def _attempt(**fields: Any) -> IngestionAttempt:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "status": "awaiting_upload",
        "received_bytes": 0,
        "expected_bytes": None,
    }
    values.update(fields)
    return cast(IngestionAttempt, SimpleNamespace(**values))


def _patch_no_duplicates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Los tests sin PG no tocan tablas: lookups y absorción del dedupe se doblan."""

    async def _none(db: AsyncSession, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(dedupe, "find_confirmed_variant", _none)
    monkeypatch.setattr(dedupe, "lookup_exact_identity", _none)
    monkeypatch.setattr(dedupe, "lookup_variant", _none)
    monkeypatch.setattr(dedupe, "absorb_reservation", _none)


def _patch_owned(
    monkeypatch: pytest.MonkeyPatch, *objects: Audio | IngestionAttempt
) -> None:
    """`get_owned` de mentira para `_restore_identity` (compensación sin BD)."""

    async def _owned(
        db: AsyncSession, model: type[Any], user_id: uuid.UUID, obj_id: uuid.UUID
    ) -> Any:
        for obj in objects:
            if obj.id == obj_id:
                return obj
        return None

    monkeypatch.setattr(uploads, "get_owned", _owned)


async def _receive(
    request: _FakeRequest,
    sink: _Sink,
    *,
    audio: Audio | None = None,
    attempt: IngestionAttempt | None = None,
    content_length: int | None = None,
    db: AsyncSession | None = None,
) -> Any:
    audio = audio or _audio()
    attempt = attempt or _attempt()
    return await receive_content(
        db or _db(),
        cast(Request, request),
        sink,
        user_id=audio.user_id,
        audio=audio,
        attempt=attempt,
        content_length=content_length,
    )


# --- Flujos ------------------------------------------------------------------------------


async def test_stream_is_cut_at_the_limit_and_never_admits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "upload_max_bytes", 64)
    sink = _Sink()
    attempt = _attempt()

    with pytest.raises(ApiError) as excinfo:
        await _receive(
            _FakeRequest([b"a" * 48, b"b" * 48]), sink, attempt=attempt, content_length=None
        )

    error = excinfo.value
    assert (error.status_code, error.code) == (413, "payload_too_large")
    assert error.details == {"max_bytes": 64}
    # Acotado: se corta ANTES de escribir el chunk que rebasa y se descarta lo recibido.
    assert sink.events == ["begin", "write", "discard"]
    assert sink.written == 48 and sink.retained == b""
    assert attempt.status == "awaiting_upload"  # la reserva queda intacta para reintentar (C6)


async def test_empty_body_is_415_and_never_202() -> None:
    sink = _Sink()

    with pytest.raises(ApiError) as excinfo:
        await _receive(_FakeRequest([]), sink)

    assert (excinfo.value.status_code, excinfo.value.code) == (415, "unsupported_format")
    assert sink.events == ["begin", "discard"] and "admit" not in sink.events


async def test_sink_down_is_503_before_reading_a_byte() -> None:
    sink = _Sink(begin_error="capacity_unavailable")
    request = _FakeRequest([CONTENT])

    with pytest.raises(ApiError) as excinfo:
        await _receive(request, sink)

    error = excinfo.value
    assert (error.status_code, error.code) == (503, "capacity_unavailable")
    assert error.headers["Retry-After"] == str(RETRY_AFTER_SECONDS)
    assert request.consumed == 0 and sink.events == ["begin"]  # ni un byte leído


async def test_unknown_sink_code_is_not_exposed_as_a_contract_error() -> None:
    sink = _Sink(begin_error="error_inventado_por_el_sink")

    with pytest.raises(ApiError) as excinfo:
        await _receive(_FakeRequest([CONTENT]), sink)

    error = excinfo.value
    assert (error.status_code, error.code) == (500, "internal_error")
    assert "error_inventado_por_el_sink" not in error.message


async def test_new_content_hashes_incrementally_and_admits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_no_duplicates(monkeypatch)
    sink = _Sink()
    audio = _audio()
    attempt = _attempt()

    result = await _receive(
        _FakeRequest([b"RIFF", b"\x00" * 60]),
        sink,
        audio=audio,
        attempt=attempt,
        content_length=64,
    )

    assert result.status_code == 202
    assert result.payload == {
        "attempt_id": str(attempt.id),
        "status": "transcribing",
        "received_bytes": 64,
    }
    # SHA-256 incremental por chunks, no del cuerpo entero (2 GB no caben en memoria).
    assert audio.sha256 == hashlib.sha256(CONTENT).hexdigest()
    assert (audio.original_bytes, attempt.received_bytes, attempt.expected_bytes) == (64, 64, 64)
    assert sink.events == ["begin", "write", "write", "finish", "admit"]
    assert sink.retained == b""


async def test_duplicate_exact_discards_before_responding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_no_duplicates(monkeypatch)
    canonical = _audio()

    async def _exact(db: AsyncSession, **kwargs: Any) -> Audio:
        return canonical

    monkeypatch.setattr(dedupe, "lookup_exact_identity", _exact)
    sink = _Sink()

    result = await _receive(_FakeRequest([CONTENT]), sink, content_length=64)

    assert result.status_code == 200
    assert result.payload == {"audio_id": str(canonical.id), "outcome": "duplicate_exact"}
    # El orden ES la prueba: discard() termina ANTES de la respuesta y jamás hay admit().
    assert sink.events == ["begin", "write", "finish", "discard"]


async def test_admit_failure_compensates_identity_and_is_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_no_duplicates(monkeypatch)
    audio = _audio()
    attempt = _attempt()
    _patch_owned(monkeypatch, audio, attempt)
    sink = _Sink(admit_error="capacity_unavailable")

    with pytest.raises(ApiError) as excinfo:
        await _receive(
            _FakeRequest([CONTENT]), sink, audio=audio, attempt=attempt, content_length=64
        )

    error = excinfo.value
    assert (error.status_code, error.code) == (503, "capacity_unavailable")
    assert error.headers.get("Retry-After")  # el 503 de capacidad exige Retry-After (§3.1)
    # Transacción compensadora: la identidad ya escrita vuelve a estar limpia (§8.1).
    assert audio.sha256 is None and audio.original_bytes is None
    assert (attempt.received_bytes, attempt.expected_bytes) == (0, None)
    assert sink.events == ["begin", "write", "finish", "admit", "discard"]
    assert sink.retained == b""


async def test_client_disconnect_cancels_attempt_and_cleans_up() -> None:
    sink = _Sink()
    attempt = _attempt()
    request = _FakeRequest([b"RIFF"], fail_after=RuntimeError("conexión perdida"))

    with pytest.raises(RuntimeError, match="conexión perdida"):
        await _receive(request, sink, attempt=attempt)

    # «Desconectar cancela y limpia el intento» (C6), sin admit y sin respuesta posible.
    assert attempt.status == "cancelled"
    assert sink.events == ["begin", "write", "discard"] and "admit" not in sink.events
    assert sink.retained == b""


def test_effective_limit_follows_published_capabilities(monkeypatch: pytest.MonkeyPatch) -> None:
    # Sin publicación del spike → límite provisional de S1; con ella, la publicada manda (C5).
    unvalidated = SimpleNamespace(limits_validated=False, max_upload_bytes=None)
    monkeypatch.setattr(settings, "ingestion_capabilities", unvalidated)
    assert effective_upload_limit() == settings.upload_max_bytes
    published = SimpleNamespace(limits_validated=True, max_upload_bytes=123_456)
    monkeypatch.setattr(settings, "ingestion_capabilities", published)
    assert effective_upload_limit() == 123_456

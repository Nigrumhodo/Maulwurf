"""I-S2-AN-05 (M2/G3): dedupe en el `PUT`, cleanup previo y token de variante (A2.4).

Contra PostgreSQL real y con un doble `ReceptionSink` inyectado: el contrato interno de
recepción con S2.1 sigue sin congelar (C2), así que en producción el sink por defecto
devuelve `503` antes de leer —ese precedente lo fija `test_a18_endpoints.py`—. Aquí se
cubre la matriz de S2.md §A2.4:

- `202` SOLO para contenido nuevo admitido; `200 duplicate_exact` y `409 duplicate_variant`
  siempre con `discard()` verificado antes de la respuesta y la reserva absorbida;
- subidas exactas concurrentes convergiendo hacia la canónica por `audios_dedupe_identity`;
- token de variante de un solo uso/TTL (en BD solo su SHA-256) y verificación del hash en
  el segundo `PUT` (`422 variant_hash_mismatch` antes de ASR);
- límite: pre-filtro por cabecera y corte EN CALIENTE contando bytes en streaming;
  `410`/`409`/`404`/`403`/`401` sin abrir el sink;
- compensación ante fallo de `commit` o de `admit()`, desconexión, cuerpo vacío y
  descarte fallido de duplicados (nunca se responde duplicado sin cleanup).

Bytes sintéticos generados en RAM por cada test: jamás se persisten como archivo ni como
artefacto de CI.
"""

import hashlib
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import ClientDisconnect

from app.core.config import settings
from app.core.db import get_session
from app.core.deps import CSRF_HEADER
from app.main import app
from app.models import Audio, IngestionAttempt, VariantConfirmationToken
from app.services import dedupe
from app.services.audios import token_hash
from app.services.uploads import Admission, SinkError, get_reception_sink, receive_content
from tests.integration.actors import (
    Actor,
    make_actor,
    make_attempt,
    make_subject,
    make_variant_confirmation_token,
)

pytestmark = pytest.mark.integration

CONTENT = b"RIFF" + b"\x00" * 60
DIGEST = hashlib.sha256(CONTENT).hexdigest()


class FakeSink:
    """`ReceptionSink` de mentira: bytes solo en RAM y eventos ordenados de observación."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.written = 0
        self.retained = bytearray()
        self.begin_error: str | None = None
        self.admit_error: str | None = None
        self.discard_error: str | None = None

    async def begin(
        self,
        *,
        user_id: uuid.UUID,
        audio_id: uuid.UUID,
        attempt_id: uuid.UUID,
        expected_bytes: int | None,
    ) -> None:
        self.events.append("begin")
        if self.begin_error is not None:
            raise SinkError(self.begin_error)

    async def write(self, chunk: bytes) -> None:
        self.events.append("write")
        self.written += len(chunk)
        self.retained += chunk

    async def finish(self) -> None:
        self.events.append("finish")

    async def discard(self) -> None:
        self.events.append("discard")
        if self.discard_error is not None:
            raise SinkError(self.discard_error)
        self.retained.clear()

    async def admit(self) -> Admission:
        self.events.append("admit")
        if self.admit_error is not None:
            raise SinkError(self.admit_error)
        self.retained.clear()  # el supervisor se lo queda: ya no «espera decisión»
        return Admission(status="transcribing")


@pytest.fixture
def sink() -> FakeSink:
    return FakeSink()


@pytest.fixture
async def client(db_session: AsyncSession, sink: FakeSink) -> AsyncIterator[AsyncClient]:
    async def override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = override
    app.dependency_overrides[get_reception_sink] = lambda: sink
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=settings.allowed_origin
    ) as c:
        yield c
    app.dependency_overrides.clear()


async def _subject_id(db: AsyncSession, actor: Actor) -> uuid.UUID:
    subject = await make_subject(db, actor)
    # Plano: los `commit()` de los handlers expiran la fila y leerla luego rompe (greenlet).
    return subject.id


def _raw_body(subject_id: uuid.UUID, **overrides: Any) -> dict[str, Any]:
    return {
        "subject_id": str(subject_id),
        "class_date": "2026-09-21",
        "class_timezone": "America/Bogota",
        "language_code": "es",
        "title": "Derivadas",
        "privacy_notice_version": settings.privacy_notice_version,
        "cloud_processing_accepted": True,
        "third_party_voice_acknowledged": True,
        **overrides,
    }


async def _reserve(client: AsyncClient, actor: Actor, body: dict[str, Any]) -> dict[str, Any]:
    response = await client.post(
        "/audios", json=body, cookies=actor.cookies, headers=actor.headers
    )
    assert response.status_code == 201, response.text
    data: dict[str, Any] = response.json()
    return data


async def _put(
    client: AsyncClient,
    actor: Actor,
    url: str,
    *,
    content: bytes = CONTENT,
    extra: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
) -> Any:
    headers = {**actor.headers, "Content-Type": "audio/wav", **(extra or {})}
    # `cookies or actor.cookies` borraría un `cookies={}` intencional (caso sin sesión).
    return await client.put(
        url,
        content=content,
        cookies=actor.cookies if cookies is None else cookies,
        headers=headers,
    )


async def _count(db: AsyncSession, model: type[Any]) -> int:
    return int(await db.scalar(select(func.count()).select_from(model)) or 0)


def _assert_clean(response: Any, sink: FakeSink) -> None:
    """Tras CADA respuesta: nada retenido en el sink y los bytes crudos fuera de ella."""
    assert sink.retained == b""
    assert CONTENT not in response.content


# --- A. Contenido nuevo ------------------------------------------------------------------


async def test_new_content_is_202_and_persists_identity(
    client: AsyncClient, db_session: AsyncSession, sink: FakeSink
) -> None:
    actor = await make_actor(db_session)
    reserved = await _reserve(client, actor, _raw_body(await _subject_id(db_session, actor)))

    response = await _put(client, actor, reserved["upload_url"])

    assert response.status_code == 202
    assert response.json() == {
        "attempt_id": reserved["attempt_id"],
        "status": "transcribing",
        "received_bytes": len(CONTENT),
    }
    attempt = await db_session.get(IngestionAttempt, uuid.UUID(reserved["attempt_id"]))
    assert attempt is not None
    # La API no transiciona estados: el estado lo devuelve el sink (C12).
    assert (attempt.status, attempt.received_bytes, attempt.expected_bytes) == (
        "awaiting_upload",
        len(CONTENT),
        len(CONTENT),
    )
    audio = await db_session.get(Audio, uuid.UUID(reserved["audio_id"]))
    assert audio is not None
    assert audio.sha256 == DIGEST and audio.original_bytes == len(CONTENT)
    assert sink.events == ["begin", "write", "finish", "admit"]
    _assert_clean(response, sink)


async def test_chunked_without_content_length_is_202(
    client: AsyncClient, db_session: AsyncSession, sink: FakeSink
) -> None:
    actor = await make_actor(db_session)
    reserved = await _reserve(client, actor, _raw_body(await _subject_id(db_session, actor)))

    async def chunks() -> AsyncIterator[bytes]:
        yield b"RIFF"
        yield b"\x00" * 60

    response = await client.put(
        reserved["upload_url"], cookies=actor.cookies, headers=actor.headers, content=chunks()
    )

    assert "content-length" not in response.request.headers
    assert response.status_code == 202
    attempt = await db_session.get(IngestionAttempt, uuid.UUID(reserved["attempt_id"]))
    assert attempt is not None
    # Sin cabecera no hay tamaño declarado; el recibido se contó durante el streaming.
    assert (attempt.received_bytes, attempt.expected_bytes) == (len(CONTENT), None)
    _assert_clean(response, sink)


# --- B. Duplicados -----------------------------------------------------------------------


async def test_duplicate_exact_cleans_before_reserving(
    client: AsyncClient, db_session: AsyncSession, sink: FakeSink
) -> None:
    actor = await make_actor(db_session)
    subject_id = await _subject_id(db_session, actor)
    first = await _reserve(client, actor, _raw_body(subject_id))
    assert (await _put(client, actor, first["upload_url"])).status_code == 202
    sink.events.clear()

    second = await _reserve(client, actor, _raw_body(subject_id))
    response = await _put(client, actor, second["upload_url"])

    assert response.status_code == 200
    assert response.json() == {"audio_id": first["audio_id"], "outcome": "duplicate_exact"}
    # M2: el cleanup TERMINA antes de la respuesta y nunca hay admisión sin ASR.
    assert sink.events == ["begin", "write", "finish", "discard"]
    # La reserva perdedora se libera por completo…
    assert await db_session.get(IngestionAttempt, uuid.UUID(second["attempt_id"])) is None
    assert await db_session.get(Audio, uuid.UUID(second["audio_id"])) is None
    # …y la canónica queda intacta.
    canonical = await db_session.get(Audio, uuid.UUID(first["audio_id"]))
    assert canonical is not None and canonical.sha256 == DIGEST
    first_attempt = await db_session.get(IngestionAttempt, uuid.UUID(first["attempt_id"]))
    assert first_attempt is not None and first_attempt.status == "awaiting_upload"
    _assert_clean(response, sink)


async def test_concurrent_exact_upload_converges_to_canonical(
    client: AsyncClient, db_session: AsyncSession, sink: FakeSink, monkeypatch: pytest.MonkeyPatch
) -> None:
    actor = await make_actor(db_session)
    subject_id = await _subject_id(db_session, actor)
    winner = await _reserve(client, actor, _raw_body(subject_id))
    assert (await _put(client, actor, winner["upload_url"])).status_code == 202
    sink.events.clear()
    loser = await _reserve(client, actor, _raw_body(subject_id))

    real_lookup = dedupe.lookup_exact_identity
    calls: list[uuid.UUID] = []

    async def racy_lookup(db: AsyncSession, **kwargs: Any) -> Audio | None:
        calls.append(kwargs["exclude_audio_id"])
        if len(calls) == 1:
            return None  # la otra subida «aún no se había confirmado»: la ventana de carrera
        return await real_lookup(db, **kwargs)

    monkeypatch.setattr(dedupe, "lookup_exact_identity", racy_lookup)

    response = await _put(client, actor, loser["upload_url"])

    # La unicidad transaccional convierte la carrera en un duplicate_exact convergente.
    assert response.status_code == 200
    assert response.json() == {"audio_id": winner["audio_id"], "outcome": "duplicate_exact"}
    assert len(calls) == 2  # consulta en la ventana + reconsulta tras violar el índice único
    assert sink.events == ["begin", "write", "finish", "discard"]  # rollback + cleanup + 200
    assert await db_session.get(IngestionAttempt, uuid.UUID(loser["attempt_id"])) is None
    assert await db_session.get(Audio, uuid.UUID(loser["audio_id"])) is None
    winner_audio = await db_session.get(Audio, uuid.UUID(winner["audio_id"]))
    assert winner_audio is not None and winner_audio.sha256 == DIGEST
    _assert_clean(response, sink)


async def test_duplicate_variant_cleans_before_409_and_issues_single_use_token(
    client: AsyncClient, db_session: AsyncSession, sink: FakeSink
) -> None:
    actor = await make_actor(db_session)
    subject_id = await _subject_id(db_session, actor)
    canonical = await _reserve(client, actor, _raw_body(subject_id))
    assert (await _put(client, actor, canonical["upload_url"])).status_code == 202
    sink.events.clear()

    other = await _reserve(
        client, actor, _raw_body(subject_id, class_date="2026-09-22")  # mismo hash, otro contexto
    )
    response = await _put(client, actor, other["upload_url"])

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "duplicate_variant"
    details = error["details"]
    assert details["existing_audio_id"] == canonical["audio_id"]
    plaintext = details["variant_confirmation_token"]
    assert isinstance(plaintext, str) and len(plaintext) >= 20
    expires = datetime.fromisoformat(str(details["expires_at"]).replace("Z", "+00:00"))
    ttl = expires - datetime.now(UTC)
    assert timedelta(minutes=1) <= ttl <= timedelta(minutes=settings.variant_token_ttl_minutes)
    # Cleanup verificado ANTES del 409 y reserva provisional absorbida.
    assert sink.events == ["begin", "write", "finish", "discard"]
    assert await db_session.get(Audio, uuid.UUID(other["audio_id"])) is None
    assert await db_session.get(IngestionAttempt, uuid.UUID(other["attempt_id"])) is None
    # En BD solo vive el SHA-256 del token: el plano jamás se persiste.
    tokens = (await db_session.execute(select(VariantConfirmationToken))).scalars().all()
    assert [row.token_hash for row in tokens] == [token_hash(plaintext)]
    token = tokens[0]
    assert token.content_sha256 == DIGEST
    assert token.canonical_audio_id == uuid.UUID(canonical["audio_id"])
    assert token.consumed_at is None and token.expires_at > datetime.now(UTC)
    # Los metadatos ligados son los del contexto DESCARTADO (lo que el usuario intentaba).
    assert (token.subject_id, token.class_date.isoformat(), token.class_timezone) == (
        subject_id,
        "2026-09-22",
        "America/Bogota",
    )
    _assert_clean(response, sink)


# --- C. Variante confirmada --------------------------------------------------------------


async def _variant_chain(
    client: AsyncClient, actor: Actor, subject_id: uuid.UUID
) -> tuple[dict[str, Any], str]:
    """Reserva canónica admitida + reserva de variante que ya chocó: devuelve (reserva, token)."""
    canonical = await _reserve(client, actor, _raw_body(subject_id))
    assert (await _put(client, actor, canonical["upload_url"])).status_code == 202
    other = await _reserve(client, actor, _raw_body(subject_id, class_date="2026-09-22"))
    rejected = await _put(client, actor, other["upload_url"])
    assert rejected.status_code == 409
    token = rejected.json()["error"]["details"]["variant_confirmation_token"]
    confirmed = await _reserve(
        client,
        actor,
        _raw_body(
            subject_id,
            class_date="2026-09-22",
            variant_confirmation_token=token,
        ),
    )
    return confirmed, token


async def test_confirmed_variant_with_same_hash_is_202(
    client: AsyncClient, db_session: AsyncSession, sink: FakeSink
) -> None:
    actor = await make_actor(db_session)
    subject_id = await _subject_id(db_session, actor)
    confirmed, token = await _variant_chain(client, actor, subject_id)
    sink.events.clear()

    response = await _put(client, actor, confirmed["upload_url"])

    assert response.status_code == 202
    variant_audio = await db_session.get(Audio, uuid.UUID(confirmed["audio_id"]))
    assert variant_audio is not None and variant_audio.sha256 == DIGEST
    variant_attempt = await db_session.get(IngestionAttempt, uuid.UUID(confirmed["attempt_id"]))
    assert variant_attempt is not None and variant_attempt.received_bytes == len(CONTENT)
    # El token quedó consumido y ligado a esta reserva; la canónica no se mutó.
    token_row = (
        await db_session.execute(
            select(VariantConfirmationToken).where(
                VariantConfirmationToken.token_hash == token_hash(token)
            )
        )
    ).scalar_one()
    assert token_row.consumed_at is not None
    assert token_row.reserved_audio_id == uuid.UUID(confirmed["audio_id"])
    assert sink.events == ["begin", "write", "finish", "admit"]
    _assert_clean(response, sink)


async def test_second_put_with_different_hash_is_422(
    client: AsyncClient, db_session: AsyncSession, sink: FakeSink
) -> None:
    actor = await make_actor(db_session)
    subject_id = await _subject_id(db_session, actor)
    confirmed, _token = await _variant_chain(client, actor, subject_id)
    sink.events.clear()
    swapped = CONTENT[:4] + b"\x01" * 60  # mismo tamaño, distinto contenido

    response = await _put(client, actor, confirmed["upload_url"], content=swapped)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "variant_hash_mismatch"
    attempt = await db_session.get(IngestionAttempt, uuid.UUID(confirmed["attempt_id"]))
    assert attempt is not None
    assert attempt.status == "rejected" and attempt.error_code == "variant_hash_mismatch"
    audio = await db_session.get(Audio, uuid.UUID(confirmed["audio_id"]))
    assert audio is not None and audio.sha256 is None  # sin identidad, sin ASR
    assert sink.events == ["begin", "write", "finish", "discard"]
    assert "admit" not in sink.events
    _assert_clean(response, sink)


@pytest.mark.parametrize("case", ["expired", "consumed", "foreign"])
async def test_second_post_rejects_bad_token_without_reserving(
    client: AsyncClient, db_session: AsyncSession, case: str
) -> None:
    presenter = await make_actor(db_session)
    owner = await make_actor(db_session) if case == "foreign" else presenter
    token = await make_variant_confirmation_token(db_session, owner)
    presented = "token-opaco-de-prueba-un-uso"
    token.token_hash = token_hash(presented)
    if case == "expired":
        token.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    elif case == "consumed":
        token.consumed_at = datetime.now(UTC)
        token.reserved_audio_id = token.canonical_audio_id
    await db_session.flush()
    body = {
        "subject_id": str(token.subject_id),
        "class_date": token.class_date.isoformat(),
        "class_timezone": token.class_timezone,
        "language_code": token.language_code,
        "title": "Variante",
        "privacy_notice_version": settings.privacy_notice_version,
        "cloud_processing_accepted": True,
        "third_party_voice_acknowledged": True,
        "variant_confirmation_token": presented,
    }
    before = (
        await _count(db_session, Audio),
        await _count(db_session, IngestionAttempt),
        await _count(db_session, VariantConfirmationToken),
    )

    response = await client.post(
        "/audios", json=body, cookies=presenter.cookies, headers=presenter.headers
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
    assert (
        await _count(db_session, Audio),
        await _count(db_session, IngestionAttempt),
        await _count(db_session, VariantConfirmationToken),
    ) == before  # 0 escrituras: el token malo no reserva capacidad


# --- D. Límite y validaciones previas al sink --------------------------------------------


async def test_declared_too_large_is_413_without_opening_sink(
    client: AsyncClient, db_session: AsyncSession, sink: FakeSink
) -> None:
    actor = await make_actor(db_session)
    reserved = await _reserve(client, actor, _raw_body(await _subject_id(db_session, actor)))
    headers = {"Content-Length": str(settings.upload_max_bytes + 1)}

    response = await _put(client, actor, reserved["upload_url"], content=b"", extra=headers)

    assert response.status_code == 413
    assert response.json()["error"]["details"] == {"max_bytes": settings.upload_max_bytes}
    assert sink.events == []  # ni siquiera se abre el sink


async def test_stream_over_limit_is_cut_hot(
    client: AsyncClient, db_session: AsyncSession, sink: FakeSink, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "upload_max_bytes", 64)  # límite efectivo sin publicación (C5)
    actor = await make_actor(db_session)
    reserved = await _reserve(client, actor, _raw_body(await _subject_id(db_session, actor)))

    async def chunks() -> AsyncIterator[bytes]:
        yield b"a" * 48
        yield b"b" * 48
        yield b"c" * 48  # nunca se escribe: el corte ocurre al contarlo

    response = await client.put(
        reserved["upload_url"], cookies=actor.cookies, headers=actor.headers, content=chunks()
    )

    assert response.status_code == 413
    assert response.json()["error"]["details"] == {"max_bytes": 64}
    assert sink.events == ["begin", "write", "discard"]  # corte ANTES del chunk que rebasa
    assert sink.written == 48 and sink.retained == b""
    attempt = await db_session.get(IngestionAttempt, uuid.UUID(reserved["attempt_id"]))
    assert attempt is not None
    assert attempt.status == "awaiting_upload" and attempt.received_bytes == 0
    audio = await db_session.get(Audio, uuid.UUID(reserved["audio_id"]))
    assert audio is not None and audio.sha256 is None


@pytest.mark.parametrize(
    "case", ["expired", "cancelled", "foreign_audio", "bad_csrf", "no_session"]
)
async def test_validation_failures_never_open_sink(
    client: AsyncClient, db_session: AsyncSession, sink: FakeSink, case: str
) -> None:
    actor = await make_actor(db_session)
    reserved = await _reserve(client, actor, _raw_body(await _subject_id(db_session, actor)))
    if case in {"expired", "cancelled"}:
        attempt = await db_session.get(IngestionAttempt, uuid.UUID(reserved["attempt_id"]))
        assert attempt is not None
        if case == "expired":
            attempt.upload_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        else:
            attempt.status = "cancelled"
        await db_session.flush()

    url = reserved["upload_url"]
    cookies: dict[str, str] = dict(actor.cookies)
    headers = dict(actor.headers)
    expected = {"expired": 410, "cancelled": 409, "foreign_audio": 404, "bad_csrf": 403,
                "no_session": 401}[case]
    if case == "foreign_audio":
        # Otro usuario completo (cookie + CSRF suyos): el 404 es de propiedad, no de CSRF.
        other = await make_actor(db_session)
        cookies = other.cookies
        headers = dict(other.headers)
    elif case == "bad_csrf":
        headers[CSRF_HEADER] = "forged"
    elif case == "no_session":
        cookies = {}

    response = await _put(client, actor, url, cookies=cookies, extra=headers)

    assert response.status_code == expected
    assert sink.events == []


# --- E. Fallos y compensaciones ----------------------------------------------------------


async def test_commit_failure_after_receive_compensates(
    client: AsyncClient, db_session: AsyncSession, sink: FakeSink, monkeypatch: pytest.MonkeyPatch
) -> None:
    actor = await make_actor(db_session)
    reserved = await _reserve(client, actor, _raw_body(await _subject_id(db_session, actor)))

    async def boom(self: AsyncSession) -> None:
        raise RuntimeError("bd caída")

    monkeypatch.setattr(AsyncSession, "commit", boom)
    response = await _put(client, actor, reserved["upload_url"])

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    # Compensación: rollback + temporales descartados, la reserva queda como estaba.
    attempt = await db_session.get(IngestionAttempt, uuid.UUID(reserved["attempt_id"]))
    assert attempt is not None
    assert attempt.status == "awaiting_upload" and attempt.received_bytes == 0
    audio = await db_session.get(Audio, uuid.UUID(reserved["audio_id"]))
    assert audio is not None and audio.sha256 is None
    assert sink.events == ["begin", "write", "finish", "discard"]
    _assert_clean(response, sink)


async def test_admit_failure_restores_identity_and_is_503(
    client: AsyncClient, db_session: AsyncSession, sink: FakeSink
) -> None:
    actor = await make_actor(db_session)
    reserved = await _reserve(client, actor, _raw_body(await _subject_id(db_session, actor)))
    sink.admit_error = "capacity_unavailable"

    response = await _put(client, actor, reserved["upload_url"])

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "capacity_unavailable"
    assert response.headers.get("Retry-After")  # 503 de capacidad exige Retry-After (§3.1)
    # Transacción compensadora: la identidad ya confirmada vuelve a estar limpia (§8.1).
    attempt = await db_session.get(IngestionAttempt, uuid.UUID(reserved["attempt_id"]))
    assert attempt is not None
    assert (attempt.status, attempt.received_bytes, attempt.expected_bytes) == (
        "awaiting_upload",
        0,
        None,
    )
    audio = await db_session.get(Audio, uuid.UUID(reserved["audio_id"]))
    assert audio is not None and audio.sha256 is None and audio.original_bytes is None
    assert sink.events == ["begin", "write", "finish", "admit", "discard"]
    _assert_clean(response, sink)


async def test_default_sink_is_503_before_reading(
    client: AsyncClient, db_session: AsyncSession, sink: FakeSink
) -> None:
    # Sin el doble inyectado: el sink por defecto (C2 sin congelar) responde 503 honesto.
    del app.dependency_overrides[get_reception_sink]
    actor = await make_actor(db_session)
    reserved = await _reserve(client, actor, _raw_body(await _subject_id(db_session, actor)))

    response = await _put(client, actor, reserved["upload_url"])

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "capacity_unavailable"
    assert response.headers.get("Retry-After")
    attempt = await db_session.get(IngestionAttempt, uuid.UUID(reserved["attempt_id"]))
    assert attempt is not None and attempt.received_bytes == 0  # ni un byte llego a la BD
    assert sink.events == []  # el doble del fixture jamás se usó


async def test_failed_discard_never_answers_duplicate(
    client: AsyncClient, db_session: AsyncSession, sink: FakeSink
) -> None:
    actor = await make_actor(db_session)
    subject_id = await _subject_id(db_session, actor)
    canonical = await _reserve(client, actor, _raw_body(subject_id))
    assert (await _put(client, actor, canonical["upload_url"])).status_code == 202
    sink.events.clear()
    sink.discard_error = "capacity_unavailable"

    loser = await _reserve(client, actor, _raw_body(subject_id))
    response = await _put(client, actor, loser["upload_url"])

    # Sin cleanup verificado no hay respuesta 200/409: 500 y la reserva NO se toca.
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    attempt = await db_session.get(IngestionAttempt, uuid.UUID(loser["attempt_id"]))
    assert attempt is not None and attempt.status == "awaiting_upload"
    audio = await db_session.get(Audio, uuid.UUID(loser["audio_id"]))
    assert audio is not None and audio.sha256 is None
    assert "admit" not in sink.events


async def test_client_disconnect_cancels_attempt(
    db_session: AsyncSession, sink: FakeSink
) -> None:
    actor = await make_actor(db_session)
    attempt = await make_attempt(db_session, actor)
    attempt_id = attempt.id
    audio_id = attempt.audio_id
    audio = await db_session.get(Audio, audio_id)
    assert audio is not None

    async def broken_stream() -> AsyncIterator[bytes]:
        yield b"RIFF"
        raise ClientDisconnect()

    request = SimpleNamespace(stream=lambda: broken_stream())

    with pytest.raises(ClientDisconnect):
        await receive_content(
            db_session,
            cast(Request, request),
            sink,
            user_id=actor.user_id,
            audio=audio,
            attempt=attempt,
            content_length=None,
        )

    # «Desconectar cancela y limpia el intento» (C6), sin respuesta posible.
    fresh = await db_session.get(IngestionAttempt, attempt_id)
    assert fresh is not None and fresh.status == "cancelled"
    audio_after = await db_session.get(Audio, audio_id)
    assert audio_after is not None and audio_after.sha256 is None
    assert sink.events == ["begin", "write", "discard"] and "admit" not in sink.events
    assert sink.retained == b""


async def test_empty_body_is_415_never_202(
    client: AsyncClient, db_session: AsyncSession, sink: FakeSink
) -> None:
    actor = await make_actor(db_session)
    reserved = await _reserve(client, actor, _raw_body(await _subject_id(db_session, actor)))

    response = await _put(client, actor, reserved["upload_url"], content=b"")

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_format"
    assert 202 != response.status_code
    attempt = await db_session.get(IngestionAttempt, uuid.UUID(reserved["attempt_id"]))
    assert attempt is not None and attempt.status == "awaiting_upload"
    assert sink.events == ["begin", "discard"]


# --- F. Invariante global ----------------------------------------------------------------


async def test_no_raw_audio_bytes_survive_any_outcome(
    client: AsyncClient, db_session: AsyncSession, sink: FakeSink
) -> None:
    actor = await make_actor(db_session)
    subject_id = await _subject_id(db_session, actor)

    new = await _reserve(client, actor, _raw_body(subject_id))
    admitted = await _put(client, actor, new["upload_url"])
    assert admitted.status_code == 202
    _assert_clean(admitted, sink)

    duplicate = await _reserve(client, actor, _raw_body(subject_id))
    exact = await _put(client, actor, duplicate["upload_url"])
    assert exact.status_code == 200
    _assert_clean(exact, sink)

    variant = await _reserve(client, actor, _raw_body(subject_id, class_date="2026-09-22"))
    conflicted = await _put(client, actor, variant["upload_url"])
    assert conflicted.status_code == 409
    _assert_clean(conflicted, sink)

    rows = (
        (await db_session.execute(select(Audio).where(Audio.user_id == actor.user_id)))
        .scalars()
        .all()
    )
    assert rows  # solo queda la clase canónica
    # Ninguna fila guarda los bytes crudos: únicamente el hash hex (spec M2: no audio durable).
    assert all(row.sha256 in (None, DIGEST) for row in rows)

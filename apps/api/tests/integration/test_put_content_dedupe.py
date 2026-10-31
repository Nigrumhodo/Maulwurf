"""I-S2-AN-05 (M2/G3): dedupe en el `PUT`, cleanup previo y token de variante (A2.1/A2.4/A2.5).

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

A2.5 additions (PLAN_TESTS §2.2): the second POST also rejects a token whose bound metadata does
not match (`mismatch` case) and a token replayed after a valid consumption; and two simultaneous
uploads with the same hash and different contexts resolve toward ONE canonical — the late arrival
cleans up and gets the `409` + token instead of a second `202` (S2.md §A2.5 review finding
2026-10-11). The `test_simultaneous_variant_contexts_*` test fails on develop without the
post-admission re-check and passes with it.

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
from app.services.ingest_sink import get_reception_sink
from app.services.uploads import (
    Admission,
    DiscardReason,
    Received,
    SinkError,
    receive_content,
)
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
        self.hasher = hashlib.sha256()
        self.begin_error: str | None = None
        self.admit_error: str | None = None
        self.discard_error: str | None = None

    async def begin(
        self,
        *,
        user_id: uuid.UUID,
        audio_id: uuid.UUID,
        attempt_id: uuid.UUID,
        fencing_token: int,
        declared_bytes: int | None,
    ) -> None:
        # One sink serves a test's several PUTs: each reception starts its own hash and count.
        self.hasher = hashlib.sha256()
        self.written = 0
        self.events.append("begin")
        if self.begin_error is not None:
            raise SinkError(self.begin_error)

    async def write(self, chunk: bytes) -> None:
        self.events.append("write")
        self.hasher.update(chunk)
        self.written += len(chunk)
        self.retained += chunk

    async def finish(self) -> Received:
        self.events.append("finish")
        return Received(sha256=self.hasher.hexdigest(), received_bytes=self.written)

    async def abort(self) -> None:
        self.events.append("abort")
        self.retained.clear()

    async def discard(self, reason: DiscardReason) -> None:
        self.events.append(f"discard:{reason}")
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
    # A2.14: the API claims `awaiting_upload -> receiving` before the first byte. The fake
    # sink does not model ingest's next write, so the row stays claimed here; the real
    # ingest moves it to `transcribing` on admit.
    assert (attempt.status, attempt.received_bytes, attempt.expected_bytes) == (
        "receiving",
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
    assert sink.events == ["begin", "write", "finish", "discard:duplicate_exact"]
    # La reserva perdedora se libera por completo…
    assert await db_session.get(IngestionAttempt, uuid.UUID(second["attempt_id"])) is None
    assert await db_session.get(Audio, uuid.UUID(second["audio_id"])) is None
    # …y la canónica queda intacta.
    canonical = await db_session.get(Audio, uuid.UUID(first["audio_id"]))
    assert canonical is not None and canonical.sha256 == DIGEST
    first_attempt = await db_session.get(IngestionAttempt, uuid.UUID(first["attempt_id"]))
    assert first_attempt is not None and first_attempt.status == "receiving"
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
    # rollback + cleanup + 200
    assert sink.events == ["begin", "write", "finish", "discard:duplicate_exact"]
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
    assert sink.events == ["begin", "write", "finish", "discard:duplicate_variant"]
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
    # The fake sink does not bump the fencing token, so the API's release still owns the
    # claimed row and cancels it. With the real ingest the token moves on acquire and
    # `ingest` writes `rejected` / `variant_hash_mismatch` from the discard reason.
    assert attempt.status == "cancelled"
    audio = await db_session.get(Audio, uuid.UUID(confirmed["audio_id"]))
    assert audio is not None and audio.sha256 is None  # sin identidad, sin ASR
    assert sink.events == ["begin", "write", "finish", "discard:variant_hash_mismatch"]
    assert "admit" not in sink.events
    _assert_clean(response, sink)


@pytest.mark.parametrize("case", ["expired", "consumed", "foreign", "mismatch"])
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
    posted_date = token.class_date.isoformat()
    if case == "mismatch":
        # A2.5: the token is bound to its metadata; another class_date cannot consume it.
        posted_date = (token.class_date + timedelta(days=1)).isoformat()
    await db_session.flush()
    body = {
        "subject_id": str(token.subject_id),
        "class_date": posted_date,
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


async def test_consumed_token_cannot_be_replayed_by_the_full_flow(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A2.5: a token consumed through the real flow stays single-use for a full replay."""
    actor = await make_actor(db_session)
    subject_id = await _subject_id(db_session, actor)
    _confirmed, token = await _variant_chain(client, actor, subject_id)
    before = (
        await _count(db_session, Audio),
        await _count(db_session, IngestionAttempt),
        await _count(db_session, VariantConfirmationToken),
    )

    replay = await client.post(
        "/audios",
        json=_raw_body(subject_id, class_date="2026-09-22", variant_confirmation_token=token),
        cookies=actor.cookies,
        headers=actor.headers,
    )

    assert replay.status_code == 422
    assert replay.json()["error"]["code"] == "validation_failed"
    assert (
        await _count(db_session, Audio),
        await _count(db_session, IngestionAttempt),
        await _count(db_session, VariantConfirmationToken),
    ) == before


async def test_simultaneous_variant_contexts_resolve_to_one_canonical(
    client: AsyncClient, db_session: AsyncSession, sink: FakeSink, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A2.5 review finding (2026-10-11): same hash + different contexts arriving at once.

    Both pre-commit lookups can miss the other row (it is not committed yet) and the
    `audios_dedupe_identity` unique index does not collide across contexts, so without the
    post-admission re-check BOTH uploads would be admitted (`202`). The late arrival must clean
    up and get the `409 duplicate_variant` + token instead. This test fails on develop.
    """
    actor = await make_actor(db_session)
    subject_id = await _subject_id(db_session, actor)
    winner = await _reserve(client, actor, _raw_body(subject_id))
    assert (await _put(client, actor, winner["upload_url"])).status_code == 202
    sink.events.clear()
    loser = await _reserve(client, actor, _raw_body(subject_id, class_date="2026-09-22"))

    # Inside the test transaction `now()` is the transaction timestamp, so both reservations
    # share `created_at` and precedence would fall to the random PK: force a strict ordering
    # (in production each reservation commits in its own transaction and the clock decides).
    winner_audio = await db_session.get(Audio, uuid.UUID(winner["audio_id"]))
    loser_audio = await db_session.get(Audio, uuid.UUID(loser["audio_id"]))
    assert winner_audio is not None and loser_audio is not None
    base = datetime.now(UTC) - timedelta(minutes=5)
    winner_audio.created_at = base
    loser_audio.created_at = base + timedelta(minutes=1)
    await db_session.flush()

    real_lookup = dedupe.lookup_variant
    calls: list[uuid.UUID] = []

    async def racy_lookup(db: AsyncSession, **kwargs: Any) -> Audio | None:
        calls.append(kwargs["exclude_audio_id"])
        if len(calls) == 1:
            return None  # the window: the winner was not committed yet when we looked
        return await real_lookup(db, **kwargs)

    monkeypatch.setattr(dedupe, "lookup_variant", racy_lookup)

    response = await _put(client, actor, loser["upload_url"])

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "duplicate_variant"
    assert error["details"]["existing_audio_id"] == winner["audio_id"]
    assert len(calls) == 2  # pre-commit miss + post-admission re-check
    # Verified cleanup before the 409 and the provisional class absorbed.
    assert sink.events == ["begin", "write", "finish", "discard:duplicate_variant"]
    assert await db_session.get(IngestionAttempt, uuid.UUID(loser["attempt_id"])) is None
    assert await db_session.get(Audio, uuid.UUID(loser["audio_id"])) is None
    fresh_winner = await db_session.get(Audio, uuid.UUID(winner["audio_id"]))
    assert fresh_winner is not None and fresh_winner.sha256 == DIGEST
    # The token binds the discarded context so the client can confirm via a second POST.
    plaintext = error["details"]["variant_confirmation_token"]
    token_row = (
        await db_session.execute(
            select(VariantConfirmationToken).where(
                VariantConfirmationToken.token_hash == token_hash(plaintext)
            )
        )
    ).scalar_one()
    assert token_row.canonical_audio_id == uuid.UUID(winner["audio_id"])
    assert token_row.class_date.isoformat() == "2026-09-22"
    _assert_clean(response, sink)


async def test_distinct_tenants_uploading_same_content_never_dedupe_each_other(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A2.5 / G3: dedupe and variants are strictly tenant-isolated.

    Two distinct users uploading the exact same audio bytes (even with identical metadata)
    each receive their own 202 admission, with distinct audio IDs and separate DB rows.
    """
    alice = await make_actor(db_session)
    bob = await make_actor(db_session)
    alice_user_id = alice.user.id
    bob_user_id = bob.user.id
    alice_subject = await _subject_id(db_session, alice)
    bob_subject = await _subject_id(db_session, bob)

    alice_res = await _reserve(client, alice, _raw_body(alice_subject))
    bob_res = await _reserve(client, bob, _raw_body(bob_subject))

    alice_put = await _put(client, alice, alice_res["upload_url"])
    assert alice_put.status_code == 202
    assert alice_put.json()["attempt_id"] == alice_res["attempt_id"]

    bob_put = await _put(client, bob, bob_res["upload_url"])
    assert bob_put.status_code == 202
    assert bob_put.json()["attempt_id"] == bob_res["attempt_id"]

    assert alice_res["audio_id"] != bob_res["audio_id"]
    alice_audio = await db_session.get(Audio, uuid.UUID(alice_res["audio_id"]))
    bob_audio = await db_session.get(Audio, uuid.UUID(bob_res["audio_id"]))
    assert alice_audio is not None and alice_audio.user_id == alice_user_id
    assert bob_audio is not None and bob_audio.user_id == bob_user_id
    assert alice_audio.sha256 == DIGEST
    assert bob_audio.sha256 == DIGEST




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
    assert sink.events == ["begin", "write", "abort"]  # cut BEFORE the chunk that exceeds the limit
    assert sink.written == 48 and sink.retained == b""
    attempt = await db_session.get(IngestionAttempt, uuid.UUID(reserved["attempt_id"]))
    assert attempt is not None
    # A2.14: a failed PUT frees the reservation immediately (the fake keeps the API as the
    # owner because it does not bump the token).
    assert attempt.status == "cancelled" and attempt.received_bytes == 0
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

    real_commit = AsyncSession.commit
    commits = 0

    async def flaky(self: AsyncSession) -> None:
        nonlocal commits
        commits += 1
        if commits == 2:  # the claim commits first; the identity write is the failing one
            raise RuntimeError("bd caída")
        await real_commit(self)

    monkeypatch.setattr(AsyncSession, "commit", flaky)
    response = await _put(client, actor, reserved["upload_url"])

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    # Compensación: rollback + temporales descartados; A2.14 releases the failed claim.
    attempt = await db_session.get(IngestionAttempt, uuid.UUID(reserved["attempt_id"]))
    assert attempt is not None
    assert attempt.status == "cancelled" and attempt.received_bytes == 0
    audio = await db_session.get(Audio, uuid.UUID(reserved["audio_id"]))
    assert audio is not None and audio.sha256 is None
    assert sink.events == ["begin", "write", "finish", "discard:api_error"]
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
    # A2.14: the failed PUT released the claim (the fake sink did not take the lease).
    assert (attempt.status, attempt.received_bytes, attempt.expected_bytes) == (
        "cancelled",
        0,
        None,
    )
    audio = await db_session.get(Audio, uuid.UUID(reserved["audio_id"]))
    assert audio is not None and audio.sha256 is None and audio.original_bytes is None
    assert sink.events == ["begin", "write", "finish", "admit", "discard:api_error"]
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
    # The API claimed the row before `begin`; the 503 released it again (A2.14).
    assert attempt.status == "cancelled"
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
    # The fake sink does not bump the token, so the API's release still owns the claim and
    # cancels it; with the real ingest the failed discard stays under the ingest lease for
    # S2.7 reconciliation.
    assert attempt is not None and attempt.status == "cancelled"
    audio = await db_session.get(Audio, uuid.UUID(loser["audio_id"]))
    assert audio is not None and audio.sha256 is None
    assert "admit" not in sink.events


async def test_client_disconnect_aborts_the_hop_without_writing_status(
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

    # Disconnecting cancels the internal hop (ADR-0008 §7); `ingest` writes the `cancelled` state.
    fresh = await db_session.get(IngestionAttempt, attempt_id)
    assert fresh is not None and fresh.status == "awaiting_upload"
    audio_after = await db_session.get(Audio, audio_id)
    assert audio_after is not None and audio_after.sha256 is None
    assert sink.events == ["begin", "write", "abort"] and "admit" not in sink.events
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
    # A2.14: the failed PUT releases the claim; uploading again needs a new POST.
    assert attempt is not None and attempt.status == "cancelled"
    assert sink.events == ["begin", "abort"]


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

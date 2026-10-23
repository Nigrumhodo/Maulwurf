"""I-S2-AN-06 contra PostgreSQL real: `DELETE /audios/{id}` purga derivados y el cancel
invalida el fencing (A2.7, gate G3).

Cubre la purga verificable tabla por tabla, el aislamiento de tenant y los 401/403/404 del
contrato, el borrado durante ASR activo con replay del commit tardío, el CAS perdido en
carrera (review del PR #65) y la liberación del índice `audios_dedupe_identity`. El lado
decisional puro (estados, token, opción remota) vive en `tests/unit/
test_cancel_tombstone.py` (U-S2-AN-04).
"""

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import CursorResult, event, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session
from app.core.deps import CSRF_HEADER
from app.core.errors import ApiError
from app.main import app
from app.models import (
    Audio,
    Chunk,
    ChunkSegment,
    Conversation,
    Embedding,
    IngestionAttempt,
    Message,
    MessageSource,
    OutboxEvent,
    ProcessingRun,
    Segment,
    Transcript,
)
from app.routers import audios as audios_router
from app.services import audios as audios_services
from app.services.audios import cas_transition
from tests.integration import actors

pytestmark = pytest.mark.integration

TEXT = "secreto de clase"


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    async def override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = override
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=settings.allowed_origin
    ) as c:
        yield c
    app.dependency_overrides.clear()


@dataclass
class SeededClass:
    """Una clase completa atada a UN solo audio: es lo que el tombstone debe purgar."""

    audio: Audio
    attempt: IngestionAttempt
    transcript: Transcript
    segment: Segment
    chunk: Chunk
    chunk_segment: ChunkSegment
    embedding: Embedding
    run: ProcessingRun
    event: OutboxEvent
    done_event: OutboxEvent
    conversation: Conversation
    message: Message
    source: MessageSource


async def _seed_class(
    db: AsyncSession, actor: actors.Actor, *, attempt_status: str = "succeeded"
) -> SeededClass:
    subject = await actors.make_subject(db, actor)
    audio = await actors.make_audio(db, actor, subject=subject)
    audio.sha256 = "a" * 64
    attempt = await actors.make_attempt(db, actor, audio=audio)
    attempt.status = attempt_status
    transcript = Transcript(
        user_id=actor.user_id,
        audio_id=audio.id,
        version=1,
        language_requested="es",
        model="test-model",
        model_config={},
        timestamp_precision="none",
        text=TEXT,
        char_count=len(TEXT),
        fts_config="spanish",
        summary="resumen secreto",
    )
    db.add(transcript)
    await db.flush()
    segment = Segment(
        user_id=actor.user_id,
        transcript_id=transcript.id,
        ordinal=1,
        text=TEXT,
        char_start=0,
        char_end=len(TEXT),
    )
    db.add(segment)
    await db.flush()
    chunk = Chunk(
        user_id=actor.user_id,
        audio_id=audio.id,
        transcript_id=transcript.id,
        transcript_version=1,
        index_version="test-v1",
        ordinal=1,
        content=TEXT,
        char_start=0,
        char_end=len(TEXT),
        token_count=3,
    )
    db.add(chunk)
    await db.flush()
    chunk_segment = ChunkSegment(
        user_id=actor.user_id, chunk_id=chunk.id, segment_id=segment.id
    )
    db.add(chunk_segment)
    await db.flush()
    embedding = await actors.make_embedding(db, actor, chunk=chunk)
    run = ProcessingRun(
        user_id=actor.user_id,
        audio_id=audio.id,
        stage="index",
        transcript_version=1,
        config_version="test-v1",
    )
    db.add(run)
    await db.flush()
    event = OutboxEvent(
        user_id=actor.user_id,
        type="index_requested",
        resource_type="audio",
        resource_id=audio.id,
        resource_version=1,
        enabled=True,
        blocked_reason=None,
    )
    db.add(event)
    await db.flush()
    # Evento ya consumido: el tombstone solo salta lo `pending`, la historia se conserva.
    done_event = OutboxEvent(
        user_id=actor.user_id,
        type="analyze_requested",
        resource_type="audio",
        resource_id=audio.id,
        resource_version=1,
        enabled=True,
        blocked_reason=None,
        status="completed",
        completed_at=datetime.now(UTC),
    )
    db.add(done_event)
    await db.flush()
    conversation = Conversation(user_id=actor.user_id, mode="class", audio_id=audio.id)
    db.add(conversation)
    await db.flush()
    message = Message(
        user_id=actor.user_id,
        conversation_id=conversation.id,
        role="assistant",
        content="resumen generado: secreto de clase",
    )
    db.add(message)
    await db.flush()
    source = MessageSource(
        user_id=actor.user_id,
        message_id=message.id,
        kind="transcript",
        audio_id=audio.id,
        transcript_id=transcript.id,
        transcript_version=1,
        chunk_id=chunk.id,
        segment_id=segment.id,
    )
    db.add(source)
    await db.flush()
    return SeededClass(
        audio=audio,
        attempt=attempt,
        transcript=transcript,
        segment=segment,
        chunk=chunk,
        chunk_segment=chunk_segment,
        embedding=embedding,
        run=run,
        event=event,
        done_event=done_event,
        conversation=conversation,
        message=message,
        source=source,
    )


async def _count(db: AsyncSession, model: Any, user_id: uuid.UUID) -> int:
    # `ChunkSegment`/`Embedding` no son `TenantOwned` pero sí llevan `user_id`: el doble
    # filtrado por tenant sigue siendo la regla de todos los conteos de este archivo.
    result = await db.execute(
        select(func.count()).select_from(model).where(model.user_id == user_id)
    )
    return int(result.scalar_one())


# --- DELETE: purga verificada -----------------------------------------------------------


async def test_delete_purges_every_derivative_and_keeps_google_by_default(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    seeded = await _seed_class(db_session, actor)
    user_id = actor.user_id

    # Copia plana antes de la petición: el commit del handler "vence" los objetos de la
    # sesión compartida (mismo motivo que `Actor` en `actors.py`).
    event_id = seeded.event.id
    done_event_id = seeded.done_event.id
    response = await client.request(
        "DELETE",
        f"/audios/{seeded.audio.id}",
        json={"delete_google_events": False},
        cookies=actor.cookies,
        headers=actor.headers,
    )

    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "tombstoned"
    assert data["google_events"] == "kept"  # por defecto se conservan y se avisa (M2)
    assert "calendario" in data["notice"]
    assert data["purged"] == {
        "attempts_cancelled": 0,  # el intento ya era final: su causa se preserva
        "outbox_events_skipped": 1,
        "processing_runs_cancelled": 1,
        "message_sources_deleted": 1,
        "messages_tombstoned": 1,
        "embeddings_deleted": 1,
        "chunk_segments_deleted": 1,
        "chunks_deleted": 1,
        "segments_deleted": 1,
        "transcripts_deleted": 1,
    }

    await db_session.refresh(seeded.audio)
    assert seeded.audio.deleted_at is not None
    assert seeded.audio.active_transcript_version is None
    for model in (Transcript, Segment, Chunk, ChunkSegment, Embedding, MessageSource):
        assert await _count(db_session, model, user_id) == 0, model.__name__
    await db_session.refresh(seeded.attempt)
    assert seeded.attempt.status == "succeeded"  # final intacto, sin reescribir error_code
    await db_session.refresh(seeded.run)
    assert seeded.run.status == "cancelled"
    event_status = (
        await db_session.execute(select(OutboxEvent.status).where(OutboxEvent.id == event_id))
    ).scalar_one()
    assert event_status == "skipped"  # nunca `enabled=false`: el CHECK cleanup_gate manda
    done_status = (
        await db_session.execute(
            select(OutboxEvent.status).where(OutboxEvent.id == done_event_id)
        )
    ).scalar_one()
    assert done_status == "completed"  # solo se salta lo `pending`; el histórico no se toca
    await db_session.refresh(seeded.message)
    assert seeded.message.content == ""  # content-free tombstone: nada recuperable (M2)
    assert seeded.message.citas == []


async def test_delete_tombstones_only_the_messages_that_cite_this_class(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # A2.7 §5.1 (8): la purga de citas es POR AUDIO, no por usuario. Otra clase del mismo
    # estudiante conserva su transcript, sus fuentes y su mensaje; solo vacía el propio.
    actor = await actors.make_actor(db_session)
    mine = await _seed_class(db_session, actor)
    other = await _seed_class(db_session, actor)
    user_id = actor.user_id
    other_text = other.message.content

    response = await client.delete(
        f"/audios/{mine.audio.id}", cookies=actor.cookies, headers=actor.headers
    )

    assert response.status_code == 202
    assert response.json()["purged"]["messages_tombstoned"] == 1
    await db_session.refresh(mine.message)
    await db_session.refresh(other.message)
    assert (mine.message.content, mine.message.citas) == ("", [])
    assert other.message.content == other_text
    # Lo de la otra clase sigue vivo y citable: ni transcript, ni sources, ni chunks.
    assert await _count(db_session, Transcript, user_id) == 1
    assert await _count(db_session, MessageSource, user_id) == 1
    assert await _count(db_session, Chunk, user_id) == 1


async def test_delete_requires_session_csrf_and_origin(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    seeded = await _seed_class(db_session, actor)
    url = f"/audios/{seeded.audio.id}"

    assert (await client.delete(url)).status_code == 401
    no_csrf = await client.delete(
        url, cookies=actor.cookies, headers={"Origin": settings.allowed_origin}
    )
    assert no_csrf.status_code == 403
    bad_origin = await client.delete(
        url,
        cookies=actor.cookies,
        headers={"Origin": "https://evil.test", CSRF_HEADER: actor.csrf_token},
    )
    assert bad_origin.status_code == 403
    # Nada de lo anterior purga: la clase sigue viva para su dueño.
    assert (
        await _count(db_session, Transcript, actor.user_id)
    ) == 1


async def test_delete_with_the_explicit_google_option_is_frozen_until_s3(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    seeded = await _seed_class(db_session, actor)

    response = await client.request(
        "DELETE",
        f"/audios/{seeded.audio.id}",
        json={"delete_google_events": True},
        cookies=actor.cookies,
        headers=actor.headers,
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "google_deletion_unavailable"
    await db_session.refresh(seeded.audio)
    assert seeded.audio.deleted_at is None  # fail-closed: nada se purga ni se finge remoto


async def test_delete_foreign_is_404_and_the_second_delete_too(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    owner = await actors.make_actor(db_session)
    stranger = await actors.make_actor(db_session)
    seeded = await _seed_class(db_session, owner)
    url = f"/audios/{seeded.audio.id}"

    foreign = await client.delete(url, cookies=stranger.cookies, headers=stranger.headers)
    assert foreign.status_code == 404  # ajeno e inexistente responden igual (ADR-0006)
    assert await _count(db_session, Transcript, owner.user_id) == 1  # el ajeno no purga

    first = await client.delete(url, cookies=owner.cookies, headers=owner.headers)
    assert first.status_code == 202
    second = await client.delete(url, cookies=owner.cookies, headers=owner.headers)
    assert second.status_code == 404  # idempotencia vía «ya no existe»
    assert await _count(db_session, Transcript, owner.user_id) == 0


async def test_delete_while_asr_runs_cancels_cleanly_without_orphans(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    subject = await actors.make_subject(db_session, actor)
    audio = await actors.make_audio(db_session, actor, subject=subject)
    now = datetime.now(UTC)
    attempt = await actors.make_attempt(db_session, actor, audio=audio)
    attempt.status = "transcribing"
    attempt.owner_instance = "ingest:test"
    attempt.lease_expires_at = now + timedelta(seconds=60)
    attempt.heartbeat_at = now
    await db_session.flush()
    stale_token = attempt.fencing_token
    attempt_id, audio_id = attempt.id, audio.id

    response = await client.delete(
        f"/audios/{audio_id}", cookies=actor.cookies, headers=actor.headers
    )

    assert response.status_code == 202
    assert response.json()["purged"]["attempts_cancelled"] == 1
    status, token, lease, cleanup = (
        await db_session.execute(
            select(
                IngestionAttempt.status,
                IngestionAttempt.fencing_token,
                IngestionAttempt.lease_expires_at,
                IngestionAttempt.cleanup_status,
            ).where(IngestionAttempt.id == attempt_id)
        )
    ).one()
    assert status == "cancelled"
    assert token == stale_token + 1  # fencing invalidado en la misma sentencia
    assert lease is None  # `ingest` pierde el lease en su próximo heartbeat y limpia
    assert cleanup == "pending"  # solo la evidencia real verifica la limpieza, nunca el borrado

    # Replay del commit tardío con el token viejo: exactamente lo que intentaría `ingest`.
    late = cast(
        "CursorResult[Any]",
        await db_session.execute(
            update(IngestionAttempt)
            .where(
                IngestionAttempt.id == attempt_id,
                IngestionAttempt.user_id == actor.user_id,
                IngestionAttempt.fencing_token == stale_token,
                IngestionAttempt.status == "transcribing",
            )
            .values(status="transcript_committed_cleanup_pending")
        ),
    )
    assert late.rowcount == 0  # nadie puede publicar tarde con un token invalidado
    derived = await db_session.execute(
        select(func.count()).select_from(Transcript).where(Transcript.audio_id == audio_id)
    )
    assert derived.scalar_one() == 0  # sin huérfanos de ninguna tabla derivada


# --- POST cancel: fencing e idempotencia ------------------------------------------------


@pytest.mark.parametrize("attempt_status", ["awaiting_upload", "transcribing"])
async def test_cancel_endpoint_invalidates_fencing_and_is_idempotent(
    client: AsyncClient, db_session: AsyncSession, attempt_status: str
) -> None:
    actor = await actors.make_actor(db_session)
    subject = await actors.make_subject(db_session, actor)
    audio = await actors.make_audio(db_session, actor, subject=subject)
    now = datetime.now(UTC)
    attempt = await actors.make_attempt(db_session, actor, audio=audio)
    attempt.status = attempt_status
    attempt.lease_expires_at = now + timedelta(seconds=60)
    await db_session.flush()
    attempt_id = attempt.id
    url = f"/audios/{audio.id}/attempts/{attempt_id}/cancel"

    first = await client.post(url, cookies=actor.cookies, headers=actor.headers)
    assert first.status_code == 202
    assert first.json() == {"attempt_id": str(attempt_id), "status": "cancelled"}
    status, token, lease = (
        await db_session.execute(
            select(
                IngestionAttempt.status,
                IngestionAttempt.fencing_token,
                IngestionAttempt.lease_expires_at,
            ).where(IngestionAttempt.id == attempt_id)
        )
    ).one()
    assert status == "cancelled"
    assert token == 2  # el token 1 del reservation quedó invalidado
    assert lease is None  # sin lease vigente, `ingest` pierde la posesión en su heartbeat

    # Idempotente: un segundo cancel responde 202 y NO vuelve a mover el token (H1).
    again = await client.post(url, cookies=actor.cookies, headers=actor.headers)
    assert again.status_code == 202
    assert (
        await db_session.execute(
            select(IngestionAttempt.fencing_token).where(IngestionAttempt.id == attempt_id)
        )
    ).scalar_one() == 2


async def test_cancel_after_the_commit_is_409_and_keeps_the_transcript(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """I-S2-SG-10: cancel after the commit conflicts and does not destroy the transcript."""
    actor = await actors.make_actor(db_session)
    seeded = await _seed_class(
        db_session, actor, attempt_status="transcript_committed_cleanup_pending"
    )
    url = f"/audios/{seeded.audio.id}/attempts/{seeded.attempt.id}/cancel"

    response = await client.post(url, cookies=actor.cookies, headers=actor.headers)

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "invalid_transition"
    assert error["details"]["current_status"] == "transcript_committed_cleanup_pending"
    assert await _count(db_session, Transcript, actor.user_id) == 1  # nunca destruido


async def test_cancel_foreign_attempt_is_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    owner = await actors.make_actor(db_session)
    stranger = await actors.make_actor(db_session)
    seeded = await _seed_class(db_session, owner)
    url = f"/audios/{seeded.audio.id}/attempts/{seeded.attempt.id}/cancel"

    response = await client.post(url, cookies=stranger.cookies, headers=stranger.headers)

    assert response.status_code == 404
    await db_session.refresh(seeded.attempt)
    assert seeded.attempt.status == "succeeded"  # nada cambió en el intento ajeno


# --- CAS: la máquina valida, la BD garantiza (H2) ---------------------------------------


async def test_cas_loses_the_race_instead_of_overwriting(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    subject = await actors.make_subject(db_session, actor)
    audio = await actors.make_audio(db_session, actor, subject=subject)
    attempt = await actors.make_attempt(db_session, actor, audio=audio)
    attempt.status = "receiving"
    await db_session.flush()
    attempt_id = attempt.id

    # «Otro proceso» (ingest) mueve el intento: escribir por la `Connection` cruda evita
    # la sincronización de sesión del ORM, que es exactamente lo que NO pasa entre
    # procesos distintos — así el objeto en memoria queda legítimamente obsoleto.
    conn = await db_session.connection()
    await conn.execute(
        update(IngestionAttempt)
        .where(IngestionAttempt.id == attempt_id)
        .values(status="transcribing")
    )
    assert attempt.status == "receiving"

    with pytest.raises(ApiError) as excinfo:
        await cas_transition(db_session, attempt=attempt, target="cancelled")
    assert excinfo.value.code == "invalid_transition"
    assert (
        await db_session.execute(
            select(IngestionAttempt.status).where(IngestionAttempt.id == attempt_id)
        )
    ).scalar_one() == "transcribing"  # la carrera no se pisó


async def test_cancel_is_one_conditional_statement_and_keeps_the_fence_monotonic(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A2.7/H2: el CAS gana la carrera con UNA sentencia y el `commit` de la petición no
    # puede añadir un segundo `UPDATE` incondicional. La carrera se simula con la única
    # escritura real que sube el token SIN cambiar estado (`lease.acquire` de `ingest`),
    # hecha DESPUÉS de que el router ya leyó el intento: un eco de commit reescribiría el
    # token leído antes y la valla retrocedería (un dueño caduco volvería a superar el
    # chequeo de token). El token final prueba que manda la fila, no la lectura vieja.
    actor = await actors.make_actor(db_session)
    subject = await actors.make_subject(db_session, actor)
    audio = await actors.make_audio(db_session, actor, subject=subject)
    attempt = await actors.make_attempt(db_session, actor, audio=audio)
    attempt.status = "transcribing"
    await db_session.flush()
    attempt_id = attempt.id
    stale_token = attempt.fencing_token  # 1, el de la reserva

    original_cancel = audios_services.cancel_attempt

    async def _acquire_lease(db: AsyncSession, *, attempt: IngestionAttempt) -> str:
        # «ingest» renueva su lease y suma token mientras la petición ya tenía leído el
        # intento: `synchronize_session=False` deja el objeto de memoria desactualizado,
        # que es exactamente lo que pasa entre dos procesos.
        await db.execute(
            update(IngestionAttempt)
            .where(
                IngestionAttempt.id == attempt.id,
                IngestionAttempt.user_id == attempt.user_id,
            )
            .values(fencing_token=IngestionAttempt.fencing_token + 1)
            .execution_options(synchronize_session=False)
        )
        return await original_cancel(db, attempt=attempt)

    monkeypatch.setattr(audios_router, "cancel_attempt", _acquire_lease)

    statements: list[str] = []

    def _capture(
        conn: Any, cursor: Any, statement: str, parameters: Any, context: Any,
        executemany: Any,
    ) -> None:
        statements.append(" ".join(statement.split()))

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", _capture)
    try:
        response = await client.post(
            f"/audios/{audio.id}/attempts/{attempt_id}/cancel",
            cookies=actor.cookies,
            headers=actor.headers,
        )
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    assert response.status_code == 202
    token = (
        await db_session.execute(
            select(IngestionAttempt.fencing_token).where(IngestionAttempt.id == attempt_id)
        )
    ).scalar_one()
    assert token == stale_token + 2  # lease + cancel, calculados por la BD; el commit no baja
    updates = [s for s in statements if s.startswith("UPDATE ingestion_attempts")]
    assert len(updates) == 2  # el bump simulado del lease + UN solo CAS; el commit no eco


# --- El tombstone libera la identidad de dedupe ----------------------------------------


async def test_delete_frees_the_exact_dedupe_identity(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    seeded = await _seed_class(db_session, actor)
    identity = (
        seeded.audio.sha256,
        seeded.audio.language_code,
        seeded.audio.subject_id,
        seeded.audio.class_date,
        seeded.audio.class_timezone,
    )

    response = await client.delete(
        f"/audios/{seeded.audio.id}", cookies=actor.cookies, headers=actor.headers
    )
    assert response.status_code == 202

    # Un segundo contenido idéntico ya cabe: `audios_dedupe_identity` es parcial sobre
    # `deleted_at IS NULL`, así que re-subir la misma clase no choca con la tombstoneada.
    clone = Audio(
        user_id=actor.user_id,
        subject_id=identity[2],
        sha256=identity[0],
        class_date=identity[3],
        class_timezone=identity[4],
        language_code=identity[1],
    )
    db_session.add(clone)
    await db_session.flush()  # sin IntegrityError

"""Actores y factorías por tabla de propiedad para los tests de aislamiento (A1.9).

Cada factoría crea una fila que pertenece al actor dado y hace `flush()`, de modo que el
test puede pedirla luego como otro usuario. `FACTORIES` debe cubrir todo modelo
`TenantOwned`; `test_isolation.py` falla si aparece una tabla de propiedad sin factoría.
"""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.config import settings
from app.core.deps import COOKIE_NAME, CSRF_HEADER
from app.models import (
    Audio,
    Chunk,
    Conversation,
    GoogleCredential,
    IndexGeneration,
    IngestionAttempt,
    Message,
    MessageSource,
    OutboxEvent,
    ProcessingRun,
    Segment,
    Session,
    Subject,
    Transcript,
    User,
)
from app.models.base import TenantOwned
from app.services import sessions

# Scopes de ejemplo: el test comprueba que nunca aparecen en respuestas de otro usuario.
GOOGLE_SCOPES = ["openid", "https://www.googleapis.com/auth/calendar.events"]


class Actor:
    """Usuario con sesión válida y las cabeceras que enviaría su navegador."""

    def __init__(self, user: User, issued: sessions.IssuedSession) -> None:
        self.user = user
        # Copias planas: los handlers hacen commit() y expiran los atributos del ORM, y
        # leerlos después fuera de un await rompe (MissingGreenlet).
        self.user_id = user.id
        self.email = user.email
        self.session = issued.session
        self.csrf_token = issued.csrf_token
        self.cookies = {COOKIE_NAME: issued.cookie_token}
        self.headers = {"Origin": settings.allowed_origin, CSRF_HEADER: issued.csrf_token}


async def make_actor(db: AsyncSession, *, email: str | None = None) -> Actor:
    """Usuario nuevo (sub y email únicos) con una sesión recién emitida."""
    user = User(
        google_sub=f"sub-{uuid.uuid4()}",
        email=email or f"{uuid.uuid4().hex[:8]}@example.test",
    )
    db.add(user)
    await db.flush()
    return Actor(user, await sessions.create_session(db, user.id))


async def make_session(db: AsyncSession, actor: Actor) -> Session:
    # `make_actor` ya creó la sesión del actor: es la fila de propiedad de esta tabla.
    return actor.session


async def make_subject(db: AsyncSession, actor: Actor) -> Subject:
    subject = Subject(user_id=actor.user.id, name=f"Materia {uuid.uuid4().hex[:6]}")
    db.add(subject)
    await db.flush()
    return subject


async def make_audio(db: AsyncSession, actor: Actor, *, subject: Subject | None = None) -> Audio:
    """Audio del actor; la FK compuesta exige una materia del mismo usuario."""
    subject = subject or await make_subject(db, actor)
    audio = Audio(
        user_id=actor.user.id,
        subject_id=subject.id,
        class_date=date(2026, 9, 21),
        class_timezone="America/Bogota",
        language_code="es",
    )
    db.add(audio)
    await db.flush()
    return audio


async def make_attempt(
    db: AsyncSession, actor: Actor, *, audio: Audio | None = None
) -> IngestionAttempt:
    """Intento `awaiting_upload` vigente (15 min), como lo deja `POST /audios`."""
    audio = audio or await make_audio(db, actor)
    now = datetime.now(UTC)
    attempt = IngestionAttempt(
        user_id=actor.user.id,
        audio_id=audio.id,
        privacy_notice_version=settings.privacy_notice_version,
        cloud_processing_accepted_at=now,
        third_party_voice_acknowledged_at=now,
        declared_providers={"asr": "test"},
        status="awaiting_upload",
        owner_instance="test",
        fencing_token=1,
        upload_expires_at=now + timedelta(minutes=15),
    )
    db.add(attempt)
    await db.flush()
    return attempt


async def make_outbox_event(db: AsyncSession, actor: Actor) -> OutboxEvent:
    # Tipo fuera de CLEANUP_GATED_EVENT_TYPES: el CHECK `cleanup_gate` exige enabled sin
    # motivo de bloqueo.
    event = OutboxEvent(
        user_id=actor.user.id,
        type="sync_task_event",
        resource_type="audio",
        resource_id=uuid.uuid4(),
        resource_version=1,
        enabled=True,
        blocked_reason=None,
    )
    db.add(event)
    await db.flush()
    return event


async def make_transcript(db: AsyncSession, actor: Actor) -> Transcript:
    audio = await make_audio(db, actor)
    transcript = Transcript(
        user_id=actor.user_id,
        audio_id=audio.id,
        version=1,
        language_requested="es",
        model="test-model",
        model_config={},
        timestamp_precision="none",
        text="texto de prueba",
        char_count=len("texto de prueba"),
        fts_config="spanish",
    )
    db.add(transcript)
    await db.flush()
    return transcript


async def make_segment(db: AsyncSession, actor: Actor) -> Segment:
    transcript = await make_transcript(db, actor)
    segment = Segment(
        user_id=actor.user_id,
        transcript_id=transcript.id,
        ordinal=1,
        text=transcript.text,
        char_start=0,
        char_end=transcript.char_count,
    )
    db.add(segment)
    await db.flush()
    return segment


async def make_chunk(db: AsyncSession, actor: Actor) -> Chunk:
    transcript = await make_transcript(db, actor)
    chunk = Chunk(
        user_id=actor.user_id,
        audio_id=transcript.audio_id,
        transcript_id=transcript.id,
        transcript_version=transcript.version,
        index_version="test-v1",
        ordinal=1,
        content=transcript.text,
        char_start=0,
        char_end=transcript.char_count,
        token_count=3,
    )
    db.add(chunk)
    await db.flush()
    return chunk


async def make_index_generation(db: AsyncSession, actor: Actor) -> IndexGeneration:
    generation = IndexGeneration(
        user_id=actor.user_id,
        version=f"test-{uuid.uuid4().hex}",
        model="test-model",
        dimension=1536,
        status="building",
    )
    db.add(generation)
    await db.flush()
    return generation


async def make_processing_run(db: AsyncSession, actor: Actor) -> ProcessingRun:
    audio = await make_audio(db, actor)
    run = ProcessingRun(
        user_id=actor.user_id,
        audio_id=audio.id,
        stage="index",
        transcript_version=1,
        config_version="test-v1",
    )
    db.add(run)
    await db.flush()
    return run


async def make_conversation(db: AsyncSession, actor: Actor) -> Conversation:
    conversation = Conversation(user_id=actor.user_id, mode="all")
    db.add(conversation)
    await db.flush()
    return conversation


async def make_message(db: AsyncSession, actor: Actor) -> Message:
    conversation = await make_conversation(db, actor)
    message = Message(user_id=actor.user_id, conversation_id=conversation.id, role="user")
    db.add(message)
    await db.flush()
    return message


async def make_message_source(db: AsyncSession, actor: Actor) -> MessageSource:
    message = await make_message(db, actor)
    source = MessageSource(user_id=actor.user_id, message_id=message.id, kind="general")
    db.add(source)
    await db.flush()
    return source


async def make_google_credential(db: AsyncSession, actor: Actor) -> GoogleCredential:
    """Credencial cifrada del actor (PK = `user_id`, no es `TenantOwned`)."""
    blob, version = crypto.encrypt(
        f"ya29.test-{uuid.uuid4().hex}",
        user_id=actor.user.id,
        field="google_credentials.access_token",
    )
    credential = GoogleCredential(
        user_id=actor.user.id,
        access_token_enc=blob,
        key_version=version,
        scopes=list(GOOGLE_SCOPES),
    )
    db.add(credential)
    await db.flush()
    return credential


Factory = Callable[[AsyncSession, Actor], Awaitable[Any]]

FACTORIES: dict[type[TenantOwned], Factory] = {
    Session: make_session,
    Subject: make_subject,
    Audio: make_audio,
    Transcript: make_transcript,
    Segment: make_segment,
    Chunk: make_chunk,
    IndexGeneration: make_index_generation,
    ProcessingRun: make_processing_run,
    Conversation: make_conversation,
    Message: make_message,
    MessageSource: make_message_source,
    IngestionAttempt: make_attempt,
    OutboxEvent: make_outbox_event,
}

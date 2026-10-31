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
    CalendarEvent,
    Chunk,
    Conversation,
    Embedding,
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
    VariantConfirmationToken,
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


async def make_variant_confirmation_token(
    db: AsyncSession, actor: Actor
) -> VariantConfirmationToken:
    """Token de confirmación ligado al audio canónico del mismo tenant."""
    canonical_audio = await make_audio(db, actor)
    token = VariantConfirmationToken(
        user_id=actor.user_id,
        token_hash=f"hash-{uuid.uuid4().hex}",
        canonical_audio_id=canonical_audio.id,
        content_sha256="a" * 64,
        subject_id=canonical_audio.subject_id,
        class_date=canonical_audio.class_date,
        class_timezone=canonical_audio.class_timezone,
        language_code=canonical_audio.language_code,
        metadata_digest="b" * 64,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    db.add(token)
    await db.flush()
    return token


async def make_outbox_event(db: AsyncSession, actor: Actor) -> OutboxEvent:
    # Outside CLEANUP_GATED_EVENT_TYPES, so the `cleanup_gate` CHECK requires enabled
    # without a blocked reason. `task_cancelled` has no consumer yet.
    event = OutboxEvent(
        user_id=actor.user.id,
        type="task_cancelled",
        resource_type="task",
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


async def make_class_with_transcript(
    db: AsyncSession, actor: Actor, *, required_stages: list[str] | None = None
) -> tuple[Audio, IngestionAttempt, Transcript]:
    """Clase con transcript utilizable (A2.8): intento `succeeded` con cleanup verificado.

    Es el estado en el que la matriz de decisión permite `retry`: `active_transcript_version`
    apunta a una fila `transcripts` viva y el último intento no está activo. `make_transcript`
    no sirve porque crea su propio audio; aquí audio, intento y transcript comparten fila.
    """
    audio = await make_audio(db, actor)
    if required_stages is not None:
        audio.required_stages = required_stages
    attempt = await make_attempt(db, actor, audio=audio)
    attempt.status = "succeeded"
    attempt.cleanup_status = "verified"
    text = "texto de prueba"
    transcript = Transcript(
        user_id=actor.user_id,
        audio_id=audio.id,
        version=1,
        language_requested=audio.language_code,
        model="test-model",
        model_config={},
        timestamp_precision="none",
        text=text,
        char_count=len(text),
        fts_config="spanish",
    )
    db.add(transcript)
    await db.flush()
    audio.active_transcript_version = 1
    await db.flush()
    return audio, attempt, transcript


async def make_committed_cleanup_pending(
    db: AsyncSession,
    actor: Actor,
    *,
    audio: Audio | None = None,
    required_stages: list[str] | None = None,
) -> tuple[Audio, IngestionAttempt, Transcript, list[Segment], dict[str, OutboxEvent]]:
    """State left by the S2.5 atomic commit, before cleanup is verified (A2.10).

    Mirrors ``maulwurf_ingest.persistence.commit_transcript`` in one transaction:
    transcript v1 (language, model, model_config, version, timestamp precision and
    char count) + segments + ``active_transcript_version`` + disabled
    ``index_requested``/``analyze_requested`` rows with
    ``blocked_reason='cleanup_pending'``, and the attempt in
    ``transcript_committed_cleanup_pending``. It is a test fixture, not a second
    writer; I-S2-SG-06 covers the real transaction in ``apps/ingest``.
    """
    audio = audio or await make_audio(db, actor)
    stages = ["index"] if required_stages is None else required_stages
    audio.required_stages = stages
    now = datetime.now(UTC)
    attempt = await make_attempt(db, actor, audio=audio)
    attempt.status = "transcript_committed_cleanup_pending"
    attempt.cleanup_status = "pending"
    attempt.fragments_done = 2
    attempt.fragments_total = 2
    attempt.lease_expires_at = now + timedelta(minutes=5)

    text = "texto de prueba"
    transcript = Transcript(
        user_id=actor.user_id,
        audio_id=audio.id,
        version=1,
        language_requested=audio.language_code,
        model="test-model",
        model_config={"fragment_seconds": 30, "overlap_s": 0.25},
        timestamp_precision="word",
        text=text,
        char_count=len(text),
        fts_config="spanish",
    )
    db.add(transcript)
    await db.flush()
    audio.active_transcript_version = transcript.version
    segments = [
        Segment(
            user_id=actor.user_id,
            transcript_id=transcript.id,
            ordinal=0,
            text="texto",
            char_start=0,
            char_end=5,
            t_start=0.0,
            t_end=1.0,
        ),
        Segment(
            user_id=actor.user_id,
            transcript_id=transcript.id,
            ordinal=1,
            text="de prueba",
            char_start=6,
            char_end=15,
            t_start=1.0,
            t_end=2.0,
        ),
    ]
    db.add_all(segments)

    stage_event = {"index": "index_requested", "analyze": "analyze_requested"}
    events: dict[str, OutboxEvent] = {}
    for stage in stages:
        if stage not in stage_event:
            continue
        event = OutboxEvent(
            user_id=actor.user_id,
            type=stage_event[stage],
            resource_type="audio",
            resource_id=audio.id,
            resource_version=transcript.version,
            payload={"transcript_id": str(transcript.id), "version": transcript.version},
            enabled=False,
            blocked_reason="cleanup_pending",
            dedupe_key=stage,
        )
        db.add(event)
        events[stage] = event
    await db.flush()

    assert transcript.char_count == len(text)
    assert audio.active_transcript_version == transcript.version
    for stage, event in events.items():
        assert event.type == stage_event[stage]
        assert event.resource_type == "audio"
        assert event.resource_id == audio.id
        assert event.resource_version == transcript.version
        assert event.enabled is False
        assert event.blocked_reason == "cleanup_pending"
    return audio, attempt, transcript, segments, events


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


async def make_timed_segment(
    db: AsyncSession,
    actor: Actor,
    *,
    transcript_id: uuid.UUID,
    ordinal: int,
    t_start: float | None,
    t_end: float | None,
    text: str | None = None,
) -> Segment:
    """Segment of the actor's transcript with measured times (A2.9 SRT export tests).

    The CHECK `time_range` (A2.1) only accepts both times NULL or both set with
    `0 <= t_start < t_end`, so callers cannot persist a half-timed segment.
    """
    segment = Segment(
        user_id=actor.user_id,
        transcript_id=transcript_id,
        ordinal=ordinal,
        text=text or f"segmento {ordinal}",
        char_start=0,
        char_end=1,
        t_start=t_start,
        t_end=t_end,
    )
    db.add(segment)
    await db.flush()
    return segment


async def make_class_with_timed_segments(
    db: AsyncSession,
    actor: Actor,
    *,
    precision: str = "segment",
    duration_seconds: float | None = 10.0,
    times: tuple[tuple[float | None, float | None], ...] = ((0.0, 5.0), (5.0, 10.0)),
) -> tuple[Audio, IngestionAttempt, Transcript, list[Segment]]:
    """Class with a usable transcript and ordered, measured segments (A2.9).

    Returns the segments too, so a test can ask for one by id (`?segment=`). `precision`
    and `duration_seconds` are the two levers the SRT rule reads (A2.9/exports).
    """
    audio, attempt, transcript = await make_class_with_transcript(db, actor)
    transcript.timestamp_precision = precision
    audio.duration_seconds = duration_seconds
    segments = [
        await make_timed_segment(
            db,
            actor,
            transcript_id=transcript.id,
            ordinal=ordinal,
            t_start=start,
            t_end=end,
        )
        for ordinal, (start, end) in enumerate(times, start=1)
    ]
    await db.flush()
    return audio, attempt, transcript, segments


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


async def make_embedding(
    db: AsyncSession, actor: Actor, *, chunk: Chunk | None = None
) -> Embedding:
    """Vector de un chunk propio o del dado (A2.7: `ChunkSegment`/`Embedding` sin factoría)."""
    chunk = chunk or await make_chunk(db, actor)
    embedding = Embedding(
        user_id=actor.user.id,
        chunk_id=chunk.id,
        index_version="test-v1",
        model="test-model",
        dimension=1536,
        vector=[0.0] * 1536,
    )
    db.add(embedding)
    await db.flush()
    return embedding


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


async def make_calendar_event(db: AsyncSession, actor: Actor) -> CalendarEvent:
    """Timed mirror event of the actor (J3.7)."""
    start = datetime.now(UTC) + timedelta(days=1)
    event = CalendarEvent(
        user_id=actor.user_id,
        calendar_id="primary",
        event_id=f"event-{uuid.uuid4().hex}",
        title="Evento de prueba",
        start_at=start,
        end_at=start + timedelta(hours=1),
        last_synced_at=datetime.now(UTC),
    )
    db.add(event)
    await db.flush()
    return event


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
    VariantConfirmationToken: make_variant_confirmation_token,
    OutboxEvent: make_outbox_event,
    CalendarEvent: make_calendar_event,
}

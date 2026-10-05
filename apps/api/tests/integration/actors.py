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
    GoogleCredential,
    IngestionAttempt,
    OutboxEvent,
    Session,
    Subject,
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


async def make_audio(
    db: AsyncSession, actor: Actor, *, subject: Subject | None = None
) -> Audio:
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
    IngestionAttempt: make_attempt,
    OutboxEvent: make_outbox_event,
}

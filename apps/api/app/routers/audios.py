"""Esqueleto de ingesta (A1.8, contrato S1.md §1.2).

`POST /audios` valida metadatos y consentimiento ANTES de reservar nada y crea el intento en
`awaiting_upload`. `PUT /audios/{id}/content` valida sesión, CSRF, Origin, propiedad,
estado, expiración y tamaño declarado, pero en S1 no existe la recepción efímera (S2): con
todo válido responde `503 capacity_unavailable` SIN leer el cuerpo. Responder 202 afirmaría
haber aceptado un audio que se descarta. No hay dedupe: el hash solo se conoce en S2.
"""
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Header, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import settings
from app.core.deps import DbSession, MutationSession
from app.core.errors import ApiError, not_found
from app.core.validation import is_iana_timezone
from app.models import Audio, IngestionAttempt, Subject
from app.services.tenant import get_owned

router = APIRouter(tags=["audios"])

# Proveedores que el aviso de privacidad declara al usuario (se registran con el intento).
DECLARED_PROVIDERS = {"asr": "nvidia-riva/whisper-large-v3"}
# Quién posee la reserva hasta que S2 asigne una instancia de `ingest`.
UNASSIGNED_OWNER = "api:unassigned"


class AudioCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_id: uuid.UUID
    class_date: date
    class_timezone: str = Field(min_length=1, max_length=64)
    language_code: str = Field(min_length=2, max_length=16)
    title: str | None = Field(default=None, max_length=200)
    teacher: str | None = Field(default=None, max_length=200)
    privacy_notice_version: str = Field(min_length=1, max_length=64)
    cloud_processing_accepted: bool
    third_party_voice_acknowledged: bool

    @field_validator("class_timezone")
    @classmethod
    def _iana(cls, value: str) -> str:
        if not is_iana_timezone(value):
            raise ValueError("zona horaria IANA desconocida")
        return value


class AudioCreated(BaseModel):
    audio_id: uuid.UUID
    attempt_id: uuid.UUID
    upload_url: str
    upload_expires_at: datetime
    outcome: str = "new"


def _consent_required() -> ApiError:
    return ApiError(
        422,
        "consent_required",
        "Acepta el aviso de privacidad vigente y el procesamiento en la nube para continuar.",
        {"privacy_notice_version": settings.privacy_notice_version},
    )


@router.post("/audios", status_code=201)
async def create_audio(body: AudioCreate, session: MutationSession, db: DbSession) -> AudioCreated:
    # Consentimiento e idioma se rechazan antes de tocar la BD o reservar capacidad.
    if (
        not body.cloud_processing_accepted
        or not body.third_party_voice_acknowledged
        or body.privacy_notice_version != settings.privacy_notice_version
    ):
        raise _consent_required()
    if body.language_code not in settings.ingest_languages:
        raise ApiError(
            422,
            "language_not_allowed",
            "Idioma no disponible para transcripción.",
            {"allowed": list(settings.ingest_languages)},
        )
    subject = await get_owned(db, Subject, session.user_id, body.subject_id)
    if subject is None or subject.deleted_at is not None:
        raise not_found()

    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=settings.upload_ttl_minutes)
    audio = Audio(
        user_id=session.user_id,
        subject_id=subject.id,
        title=body.title,
        teacher=body.teacher,
        class_date=body.class_date,
        class_timezone=body.class_timezone,
        language_code=body.language_code,
    )
    db.add(audio)
    await db.flush()
    attempt = IngestionAttempt(
        user_id=session.user_id,
        audio_id=audio.id,
        privacy_notice_version=body.privacy_notice_version,
        cloud_processing_accepted_at=now,
        third_party_voice_acknowledged_at=now,
        declared_providers=DECLARED_PROVIDERS,
        status="awaiting_upload",
        owner_instance=UNASSIGNED_OWNER,
        fencing_token=1,
        upload_expires_at=expires_at,
    )
    db.add(attempt)
    await db.flush()
    # Valores leídos antes del commit: no dependen de que la sesión expire o no los objetos.
    created = AudioCreated(
        audio_id=audio.id,
        attempt_id=attempt.id,
        upload_url=f"/audios/{audio.id}/content?attempt_id={attempt.id}",
        upload_expires_at=expires_at,
    )
    await db.commit()
    return created


@router.put(
    "/audios/{audio_id}/content",
    # En S1 la única salida con todo válido es 503: OpenAPI no debe prometer un 202 que no
    # puede ocurrir. S2 vuelve a 202 al implementar la recepción efímera.
    status_code=503,
    response_model=None,
    responses={
        401: {"description": "Sin sesión (auth_required)"},
        403: {"description": "CSRF u Origin inválido (csrf_invalid)"},
        404: {"description": "Audio o intento inexistente o ajeno (not_found)"},
        409: {"description": "El intento ya no está activo (attempt_not_active)"},
        410: {"description": "La subida expiró (upload_expired)"},
        413: {"description": "Content-Length declarado supera el máximo (payload_too_large)"},
        503: {"description": "Recepción efímera no disponible en S1 (capacity_unavailable)"},
    },
)
async def upload_content(
    audio_id: uuid.UUID,
    attempt_id: Annotated[uuid.UUID, Query()],
    session: MutationSession,
    db: DbSession,
    content_length: Annotated[int | None, Header()] = None,
) -> Response:
    audio = await get_owned(db, Audio, session.user_id, audio_id)
    if audio is None or audio.deleted_at is not None:
        raise not_found()
    attempt = await get_owned(db, IngestionAttempt, session.user_id, attempt_id)
    if attempt is None or attempt.audio_id != audio.id:
        raise not_found()
    if attempt.status != "awaiting_upload":
        raise ApiError(409, "attempt_not_active", "Este intento de subida ya no está activo.")
    if attempt.upload_expires_at is None or attempt.upload_expires_at <= datetime.now(UTC):
        raise ApiError(410, "upload_expired", "La subida expiró; vuelve a crear la clase.")
    # Se decide por la cabecera, sin leer bytes: el cuerpo nunca se consume en S1. Es solo
    # un pre-filtro: una subida chunked no declara Content-Length, así que en S2 el límite
    # real se impone contando los bytes recibidos durante el streaming.
    if content_length is not None and content_length > settings.upload_max_bytes:
        raise ApiError(
            413,
            "payload_too_large",
            "El archivo supera el tamaño máximo permitido.",
            {"max_bytes": settings.upload_max_bytes},
        )
    raise ApiError(
        503,
        "capacity_unavailable",
        "La recepción de audio aún no está disponible.",
        {"retryable": False},
    )

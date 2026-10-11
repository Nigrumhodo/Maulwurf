"""Reserva de ingesta (A2.3, S2.md §A2.3 sobre `ESPECIFICACION.md` M2) y `PUT` (A2.4).

`POST /audios` valida la forma del body, el consentimiento versionado, el idioma de la
allowlist, el token de variante opcional, la materia del tenant, la cuota y el slot ANTES
de reservar nada, y crea el intento en `awaiting_upload` con una URL relativa de carga.
No hay dedupe: el hash no existe hasta el `PUT`, así que este POST responde `201`, `422`,
`429` o `503` (más `401`/`403`/`404`/`409` de contrato).

`PUT /audios/{id}/content` (A2.4) valida sesión, CSRF, Origin, propiedad, estado,
expiración y tamaño declarado SIN leer el cuerpo, y delega la recepción en
`services/uploads.receive_content`: streaming sin spool, SHA-256 incremental, corte por
límite en caliente y dedupe resuelto DESPUÉS de limpiar (`200 duplicate_exact` /
`409 duplicate_variant`); `202` solo para contenido admitido por el sink de ingesta. El
sink por defecto aún no está disponible (contrato interno con S2.1 pendiente, punto C2):
responde `503 capacity_unavailable` antes de leer un byte, igual que en A1.8.
"""
import uuid
from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import settings
from app.core.deps import DbSession, MutationSession
from app.core.errors import ApiError, consent_required, language_not_allowed, not_found
from app.core.validation import is_iana_timezone
from app.models import Audio, IngestionAttempt, Subject
from app.services.audios import (
    allowed_languages,
    check_quota,
    consume_variant_token,
    create_reservation,
    reserve_slot,
)
from app.services.tenant import get_owned
from app.services.uploads import (
    ReceptionSink,
    effective_upload_limit,
    get_reception_sink,
    receive_content,
)

router = APIRouter(tags=["audios"])


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
    # Opaco: solo puede venir de un `409 duplicate_variant` previo (A2.5); se valida y
    # consume aquí, nunca viaja a logs ni a los mensajes de error.
    variant_confirmation_token: str | None = Field(default=None, min_length=1, max_length=256)

    @field_validator("language_code")
    @classmethod
    def _normalize_language(cls, value: str) -> str:
        # BCP-47 no distingue mayúsculas: "ES" y "es" son el mismo idioma, y se persiste
        # normalizado para que el dedupe de S2 no vea variantes.
        return value.lower()

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


@router.post(
    "/audios",
    status_code=201,
    responses={
        401: {"description": "Sin sesión (auth_required)"},
        403: {"description": "CSRF u Origin inválido (csrf_invalid)"},
        404: {"description": "Materia inexistente o de otro tenant (not_found)"},
        409: {"description": "Token de variante ya consumido (invalid_transition)"},
        422: {"description": "consent_required | language_not_allowed | validation_failed"},
        429: {"description": "Cuota por usuario (rate_limited), con Retry-After"},
        503: {"description": "Sin slot de ingesta (capacity_unavailable), con Retry-After"},
    },
)
async def create_audio(
    body: AudioCreate, response: Response, session: MutationSession, db: DbSession
) -> AudioCreated:
    # Consentimiento versionado ANTES de tocar la BD: ausencia o versión obsoleta no
    # reserva capacidad ni acepta bytes (M2, U-S2-AN-01).
    if (
        not body.cloud_processing_accepted
        or not body.third_party_voice_acknowledged
        or body.privacy_notice_version != settings.privacy_notice_version
    ):
        raise consent_required(settings.privacy_notice_version)
    allowed = allowed_languages()
    # `multi` queda fuera de MVP/v1 aunque lo publicaran: la política es selección explícita.
    if body.language_code == "multi" or body.language_code not in allowed:
        raise language_not_allowed(allowed)
    token = None
    if body.variant_confirmation_token is not None:
        token = await consume_variant_token(
            db,
            user_id=session.user_id,
            presented=body.variant_confirmation_token,
            subject_id=body.subject_id,
            class_date=body.class_date,
            class_timezone=body.class_timezone,
            language_code=body.language_code,
        )
    # Materia del tenant: inexistente y ajeno responden 404 por igual (ADR-0006).
    subject = await get_owned(db, Subject, session.user_id, body.subject_id)
    if subject is None or subject.deleted_at is not None:
        raise not_found()
    # Cuota y slot se comprueban sin escribir: un 404 o un 422 no deben consumir capacidad.
    await check_quota(db, session.user_id)
    await reserve_slot(db)
    reservation = await create_reservation(
        db,
        user_id=session.user_id,
        subject_id=subject.id,
        title=body.title,
        teacher=body.teacher,
        class_date=body.class_date,
        class_timezone=body.class_timezone,
        language_code=body.language_code,
        privacy_notice_version=body.privacy_notice_version,
        token=token,
    )
    upload_url = f"/audios/{reservation.audio_id}/content?attempt_id={reservation.attempt_id}"
    # Valores leídos antes del commit: los atributos ORM expiran al confirmar.
    created = AudioCreated(
        audio_id=reservation.audio_id,
        attempt_id=reservation.attempt_id,
        upload_url=upload_url,
        upload_expires_at=reservation.upload_expires_at,
    )
    await db.commit()
    response.headers["Location"] = created.upload_url
    return created


@router.put(
    "/audios/{audio_id}/content",
    # A2.4: la recepción real está implementada; `202` es la salida por defecto con todo
    # válido y el sink de ingesta admitiendo el contenido (S2.md §A2.4).
    status_code=202,
    response_model=None,
    responses={
        200: {"description": "duplicate_exact: la clase canónica ya existe (tras cleanup)"},
        202: {"description": "Contenido nuevo admitido por el sink de ingesta"},
        401: {"description": "Sin sesión (auth_required)"},
        403: {"description": "CSRF u Origin inválido (csrf_invalid)"},
        404: {"description": "Audio o intento inexistente o ajeno (not_found)"},
        409: {"description": "attempt_not_active | duplicate_variant (con token de variante)"},
        410: {"description": "La subida expiró (upload_expired)"},
        413: {"description": "Tamaño superado, declarado o en streaming (payload_too_large)"},
        415: {"description": "unsupported_format: contenido vacío o rechazo de la ingesta"},
        422: {"description": "variant_hash_mismatch en el segundo PUT"},
        503: {"description": "capacity_unavailable (sink/caudal), con Retry-After"},
    },
)
async def upload_content(
    audio_id: uuid.UUID,
    attempt_id: Annotated[uuid.UUID, Query()],
    request: Request,
    session: MutationSession,
    db: DbSession,
    sink: Annotated[ReceptionSink, Depends(get_reception_sink)],
    content_length: Annotated[int | None, Header()] = None,
) -> Response:
    """A2.4: recibe el binario en streaming sin spool y devuelve 200/202 (o un error tipado).

    Todas las validaciones de abajo ocurren ANTES de leer un byte; la recepción, el SHA-256,
    el corte en caliente, el dedupe y la compensación viven en `services/uploads` (el router
    no hace SQL ni toca el cuerpo, guardia ADR-0006).
    """
    audio = await get_owned(db, Audio, session.user_id, audio_id)
    if audio is None or audio.deleted_at is not None:
        raise not_found()
    attempt = await get_owned(db, IngestionAttempt, session.user_id, attempt_id)
    if attempt is None or attempt.audio_id != audio.id:
        raise not_found()
    if attempt.status != "awaiting_upload":
        raise ApiError(409, "attempt_not_active", "Este intento de subida ya no está activo.")
    if attempt.upload_expires_at is None:
        # Un intento sin ventana de subida no admite bytes: no "expiró", nunca estuvo activo.
        raise ApiError(409, "attempt_not_active", "Este intento de subida ya no está activo.")
    if attempt.upload_expires_at <= datetime.now(UTC):
        raise ApiError(410, "upload_expired", "La subida expiró; vuelve a crear la clase.")
    # Pre-filtro por cabecera, sin leer bytes: el límite real se impone contando los bytes
    # durante el streaming (una subida chunked no declara Content-Length).
    limit = effective_upload_limit()
    if content_length is not None and content_length > limit:
        raise ApiError(
            413,
            "payload_too_large",
            "El archivo supera el tamaño máximo permitido.",
            {"max_bytes": limit},
        )
    outcome = await receive_content(
        db,
        request,
        sink,
        user_id=session.user_id,
        audio=audio,
        attempt=attempt,
        content_length=content_length,
    )
    return JSONResponse(status_code=outcome.status_code, content=outcome.payload)

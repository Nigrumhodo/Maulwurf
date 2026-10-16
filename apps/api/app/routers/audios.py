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

Cancelación y tombstone (A2.7, S2.md §A2.7): `POST /audios/{id}/attempts/{attempt_id}/cancel`
cancela un intento pre-commit invalidando su fencing (`202`; `409 invalid_transition` tras
el commit o en un final), y `DELETE /audios/{id}` tombstonea la clase purgando todos sus
derivados en la misma transacción (`202` trazable). El router solo resuelve ownership y
monta la respuesta: la máquina de estados decide y el SQL vive en `services/audios` (ADR-0006).

Retry vs reupload (A2.8, S2.md §A2.8): `POST /audios/{id}/retry` (`202`) requeues text
stages over a usable transcript with idempotent runs, and `POST /audios/{id}/reupload`
(`201`) opens a new ephemeral session on the SAME class when there is no usable text.
The conflict matrix (`409 transcript_unavailable|invalid_transition`) and the SQL live in
`services/runs` (ADR-0006).

Library reads and exports (A2.9, S2.md §A2.9): `GET /audios` lists the tenant's live classes,
`GET /audios/{id}` returns the derived per-stage states with their honest aggregate, `GET
/audios/{id}/progress` streams DB snapshots as SSE (state, not increments) and `GET
/audios/{id}/transcript.{txt,srt}` serve the durable text — TXT always with a transcript,
SRT only when the measured times are usable (`409 srt_unavailable`). All SQL lives in
`services/library` and the pure rules in `services/{exports,progress}` (ADR-0006).
"""

import uuid
from datetime import UTC, date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, Header, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import settings
from app.core.deps import CurrentSession, DbSession, MutationSession, ProgressSessions
from app.core.errors import (
    ApiError,
    consent_required,
    google_deletion_unavailable,
    language_not_allowed,
    not_found,
    srt_unavailable,
)
from app.core.validation import is_iana_timezone
from app.models import Audio, IngestionAttempt, Subject
from app.services import library, progress
from app.services.audios import (
    allowed_languages,
    cancel_attempt,
    check_quota,
    consume_variant_token,
    create_reservation,
    reserve_slot,
    tombstone_audio,
)
from app.services.exports import render_srt, render_txt, srt_exportable
from app.services.runs import (
    ensure_reupload_allowed,
    open_reupload,
    request_retry,
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


class AudioRetry(BaseModel):
    """Body of `POST /audios/{id}/retry` (A2.8): text stages over a transcript.

    The shape is validated here (`422 validation_failed`): at least one stage, no duplicates
    and only `index`/`analyze`. If the transcript does not exist, is obsolete or the stage
    is not owed, `services/runs` answers `409` with its typed code.
    """

    model_config = ConfigDict(extra="forbid")

    stages: list[Literal["index", "analyze"]] = Field(min_length=1)
    transcript_version: int = Field(ge=1)

    @field_validator("stages")
    @classmethod
    def _no_duplicates(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("etapas duplicadas")
        return value


class RetryStageAccepted(BaseModel):
    stage: str
    run_id: uuid.UUID
    status: str


class RetryAccepted(BaseModel):
    audio_id: uuid.UUID
    transcript_version: int
    runs: list[RetryStageAccepted]


class ReuploadCreated(BaseModel):
    """`201` of the reupload (A2.8): same class, new ephemeral upload session."""

    audio_id: uuid.UUID
    attempt_id: uuid.UUID
    upload_url: str
    upload_expires_at: datetime
    outcome: str = "reupload"


class AudioDelete(BaseModel):
    """Opciones del tombstone (A2.7): el contrato congela el nombre del flag remoto."""

    model_config = ConfigDict(extra="forbid")

    # DISENO §3.5: borrar eventos de Google ya creados exige una opción explícita. En S2
    # no puede existir ninguno (las tablas remotas son S3/F2), así que `true` responde
    # `409 google_deletion_unavailable` en vez de fingir trabajo remoto.
    delete_google_events: bool = False


class CancelAccepted(BaseModel):
    attempt_id: uuid.UUID
    status: str


class TombstonePurged(BaseModel):
    """Conteos por tabla: hacen verificable la purga sin exponer contenido (M9)."""

    attempts_cancelled: int
    outbox_events_skipped: int
    processing_runs_cancelled: int
    message_sources_deleted: int
    messages_tombstoned: int
    embeddings_deleted: int
    chunk_segments_deleted: int
    chunks_deleted: int
    segments_deleted: int
    transcripts_deleted: int


class TombstoneAccepted(BaseModel):
    audio_id: uuid.UUID
    status: str = "tombstoned"
    purged: TombstonePurged
    google_events: str = "kept"
    notice: str


class SubjectRefOut(BaseModel):
    id: str
    name: str
    color: str


class AttemptOut(BaseModel):
    """Honest snapshot of the latest attempt: real bytes and fragments, no percentages."""

    attempt_id: str
    status: str
    received_bytes: int
    fragments_done: int | None
    fragments_total: int | None
    cleanup_status: str


class AudioListItemOut(BaseModel):
    id: str
    subject: SubjectRefOut
    title: str | None
    class_date: date
    class_timezone: str
    language_code: str
    duration_seconds: float | None
    created_at: datetime
    attempt: AttemptOut | None
    stages: dict[str, str]
    aggregate_status: str


class AudioListOut(BaseModel):
    items: list[AudioListItemOut]


class TranscriptOut(BaseModel):
    version: int
    timestamp_precision: str
    char_count: int


class ExportsOut(BaseModel):
    """Which exports the class can serve RIGHT NOW; `srt` is false unless times are valid."""

    txt: bool
    srt: bool


class AudioDetailOut(BaseModel):
    id: str
    subject: SubjectRefOut
    title: str | None
    class_date: date
    class_timezone: str
    language_code: str
    duration_seconds: float | None
    created_at: datetime
    attempt: AttemptOut | None
    stages: dict[str, str]
    aggregate_status: str
    audio_retained: bool = False
    transcript: TranscriptOut | None
    exports: ExportsOut


def _subject_out(ref: library.SubjectRef) -> SubjectRefOut:
    return SubjectRefOut(id=str(ref.id), name=ref.name, color=ref.color)


def _attempt_out(attempt: library.AttemptSummary | None) -> AttemptOut | None:
    if attempt is None:
        return None
    return AttemptOut(
        attempt_id=str(attempt.attempt_id),
        status=attempt.status,
        received_bytes=attempt.received_bytes,
        fragments_done=attempt.fragments_done,
        fragments_total=attempt.fragments_total,
        cleanup_status=attempt.cleanup_status,
    )


def _list_item_out(row: library.AudioSummary) -> AudioListItemOut:
    return AudioListItemOut(
        id=str(row.id),
        subject=_subject_out(row.subject),
        title=row.title,
        class_date=row.class_date,
        class_timezone=row.class_timezone,
        language_code=row.language_code,
        duration_seconds=row.duration_seconds,
        created_at=row.created_at,
        attempt=_attempt_out(row.attempt),
        stages=dict(row.stages),
        aggregate_status=row.aggregate_status,
    )


def _detail_out(detail: library.AudioDetail) -> AudioDetailOut:
    row = detail.summary
    return AudioDetailOut(
        id=str(row.id),
        subject=_subject_out(row.subject),
        title=row.title,
        class_date=row.class_date,
        class_timezone=row.class_timezone,
        language_code=row.language_code,
        duration_seconds=row.duration_seconds,
        created_at=row.created_at,
        attempt=_attempt_out(row.attempt),
        stages=dict(row.stages),
        aggregate_status=row.aggregate_status,
        transcript=(
            TranscriptOut(
                version=detail.transcript.version,
                timestamp_precision=detail.transcript.timestamp_precision,
                char_count=detail.transcript.char_count,
            )
            if detail.transcript is not None
            else None
        ),
        exports=ExportsOut(txt=detail.transcript is not None, srt=detail.srt_exportable),
    )


async def _owned_live_audio(db: DbSession, user_id: uuid.UUID, audio_id: uuid.UUID) -> Audio:
    """Owned, non-tombstoned class or `404`; never reveals a foreign id (ADR-0006)."""
    audio = await get_owned(db, Audio, user_id, audio_id)
    if audio is None or audio.deleted_at is not None:
        raise not_found()
    return audio


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
    # Share lock: `DELETE /subjects/{id}` cannot tombstone the subject until this commits.
    subject = await get_owned(db, Subject, session.user_id, body.subject_id, lock="share")
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


@router.post(
    "/audios/{audio_id}/attempts/{attempt_id}/cancel",
    status_code=202,
    responses={
        202: {"description": "Intento cancelado (idempotente si ya estaba cancelled)"},
        401: {"description": "Sin sesión (auth_required)"},
        403: {"description": "CSRF u Origin inválido (csrf_invalid)"},
        404: {"description": "Audio o intento inexistente o ajeno (not_found)"},
        409: {"description": "invalid_transition: tras el commit o en un estado final"},
    },
)
async def cancel_attempt_endpoint(
    audio_id: uuid.UUID, attempt_id: uuid.UUID, session: MutationSession, db: DbSession
) -> CancelAccepted:
    """Cancelación explícita (A2.7, S2.md §A2.7): invalida el fencing en una sentencia.

    El router solo resuelve ownership (ADR-0006); `services.audios.cancel_attempt` decide
    con la máquina y gana o pierde la carrera con un `UPDATE` condicional. `ingest` ve el
    token nuevo en su próximo heartbeat, cancela subprocess/gRPC y verifica la limpieza.
    Tras el commit el transcript nunca se destruye: `409 invalid_transition` (M2).
    """
    audio = await get_owned(db, Audio, session.user_id, audio_id)
    if audio is None or audio.deleted_at is not None:
        raise not_found()
    attempt = await get_owned(db, IngestionAttempt, session.user_id, attempt_id)
    if attempt is None or attempt.audio_id != audio.id:
        raise not_found()
    # Valores leídos antes del commit: los atributos ORM expiran al confirmar.
    attempt_id_value = attempt.id
    status = await cancel_attempt(db, attempt=attempt)
    await db.commit()
    return CancelAccepted(attempt_id=attempt_id_value, status=status)


@router.delete(
    "/audios/{audio_id}",
    status_code=202,
    responses={
        202: {"description": "Tombstone aceptado; purga de derivados en la misma transacción"},
        401: {"description": "Sin sesión (auth_required)"},
        403: {"description": "CSRF u Origin inválido (csrf_invalid)"},
        404: {"description": "Audio inexistente, ajeno o ya tombstoneado (not_found)"},
        409: {"description": "google_deletion_unavailable: la opción remota aún no existe (S3)"},
    },
)
async def delete_audio(
    audio_id: uuid.UUID,
    session: MutationSession,
    db: DbSession,
    body: Annotated[AudioDelete | None, Body()] = None,
) -> TombstoneAccepted:
    """Tombstone de una clase (A2.7): cancela intentos/jobs y purga cada derivado.

    `deleted_at` invalida la clase para todo lector; los intentos activos quedan
    `cancelled` con el fencing invalidado (un commit tardío de `ingest` es rechazado);
    transcripts, segmentos, chunks, embeddings, citas y resúmenes se borran en la misma
    transacción. Los eventos de Google existentes se CONSERVAN por defecto con aviso:
    borrarlos exige la opción explícita `delete_google_events` (DISENO §3.5), hoy
    respondida con `409 google_deletion_unavailable` porque `google_remote_operations` es
    S3/F2. `202` porque el trabajo remoto diferido (S3) podrá venir detrás de este tombstone.
    """
    audio = await get_owned(db, Audio, session.user_id, audio_id)
    if audio is None or audio.deleted_at is not None:
        raise not_found()
    options = body or AudioDelete()
    if options.delete_google_events:
        raise google_deletion_unavailable()
    # Valores leídos antes del commit: los atributos ORM expiran al confirmar.
    audio_id_value = audio.id
    result = await tombstone_audio(db, user_id=session.user_id, audio=audio)
    await db.commit()
    return TombstoneAccepted(
        audio_id=audio_id_value,
        purged=TombstonePurged(
            attempts_cancelled=result.attempts_cancelled,
            outbox_events_skipped=result.outbox_events_skipped,
            processing_runs_cancelled=result.processing_runs_cancelled,
            message_sources_deleted=result.message_sources_deleted,
            messages_tombstoned=result.messages_tombstoned,
            embeddings_deleted=result.embeddings_deleted,
            chunk_segments_deleted=result.chunk_segments_deleted,
            chunks_deleted=result.chunks_deleted,
            segments_deleted=result.segments_deleted,
            transcripts_deleted=result.transcripts_deleted,
        ),
        notice=(
            "La clase fue eliminada; los eventos de Google ya creados se conservan en tu "
            "calendario y pueden borrarse allí."
        ),
    )


@router.post(
    "/audios/{audio_id}/retry",
    status_code=202,
    responses={
        202: {"description": "Etapas encoladas (idempotente: un run reutilizado no duplica)"},
        401: {"description": "Sin sesión (auth_required)"},
        403: {"description": "CSRF u Origin inválido (csrf_invalid)"},
        404: {"description": "Audio inexistente, ajeno o tombstoneado (not_found)"},
        409: {"description": "transcript_unavailable | invalid_transition"},
        422: {"description": "validation_failed: stages vacío, duplicado o desconocido"},
    },
)
async def retry_audio(
    audio_id: uuid.UUID, body: AudioRetry, session: MutationSession, db: DbSession
) -> RetryAccepted:
    """Retry text stages (A2.8, S2.md §A2.8): only over a usable transcript.

    `202` when every stage ends as a `pending` run (created, reset or already in flight),
    `409 transcript_unavailable` without usable or obsolete text, and `409 invalid_transition`
    if the stage is not owed, already succeeded or the attempt is still active. Runs are
    idempotent on their unique key; the router does no SQL (ADR-0006).
    """
    audio = await get_owned(db, Audio, session.user_id, audio_id, lock="share")
    if audio is None or audio.deleted_at is not None:
        raise not_found()
    outcome = await request_retry(
        db,
        user_id=session.user_id,
        audio=audio,
        stages=body.stages,
        transcript_version=body.transcript_version,
    )
    # `get_session` never commits (`core/db.py`): without this the runs and their outbox
    # events would live only in this request's session and vanish on close. Flat values,
    # so they are safe to read after the commit.
    accepted = RetryAccepted(
        audio_id=outcome.audio_id,
        transcript_version=outcome.transcript_version,
        runs=[
            RetryStageAccepted(stage=run.stage, run_id=run.run_id, status=run.status)
            for run in outcome.runs
        ],
    )
    await db.commit()
    return accepted


@router.post(
    "/audios/{audio_id}/reupload",
    status_code=201,
    responses={
        201: {"description": "Nueva sesión efímera de subida para la misma clase"},
        401: {"description": "Sin sesión (auth_required)"},
        403: {"description": "CSRF u Origin inválido (csrf_invalid)"},
        404: {"description": "Audio inexistente, ajeno o tombstoneado (not_found)"},
        409: {"description": "invalid_transition: transcript utilizable o intento activo"},
        429: {"description": "Cuota por usuario (rate_limited), con Retry-After"},
        503: {"description": "Sin slot de ingesta (capacity_unavailable), con Retry-After"},
    },
)
async def reupload_audio(
    audio_id: uuid.UUID, response: Response, session: MutationSession, db: DbSession
) -> ReuploadCreated:
    """Upload again (A2.8): a new ephemeral session over the SAME class identity.

    It only exists WITHOUT a usable transcript and without an active attempt (otherwise, `409
    invalid_transition`). It copies the previous attempt's consent snapshot and clears
    `sha256`; quota and slot are checked as in `POST /audios` before reserving anything.
    `lock="update"` serializes concurrent reuploads of the same class: the second one sees
    the attempt the first just opened and gets its `409` instead of an IntegrityError.
    """
    audio = await get_owned(db, Audio, session.user_id, audio_id, lock="update")
    if audio is None or audio.deleted_at is not None:
        raise not_found()
    previous = await ensure_reupload_allowed(db, user_id=session.user_id, audio=audio)
    # Quota and slot, as in `POST /audios` and the cancel: a rejection reserves no capacity.
    await check_quota(db, session.user_id)
    await reserve_slot(db)
    reservation = await open_reupload(
        db, user_id=session.user_id, audio=audio, previous=previous
    )
    upload_url = f"/audios/{reservation.audio_id}/content?attempt_id={reservation.attempt_id}"
    # Values read before the commit: the ORM attributes expire on confirm.
    created = ReuploadCreated(
        audio_id=reservation.audio_id,
        attempt_id=reservation.attempt_id,
        upload_url=upload_url,
        upload_expires_at=reservation.upload_expires_at,
    )
    await db.commit()
    response.headers["Location"] = upload_url
    return created


@router.get(
    "/audios",
    responses={
        200: {"description": "Clases vivas del tenant, más recientes primero"},
        401: {"description": "Sin sesión (auth_required)"},
        422: {"description": "validation_failed: limit fuera de rango o subject_id no UUID"},
    },
)
async def list_audios(
    response: Response,
    session: CurrentSession,
    db: DbSession,
    subject_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> AudioListOut:
    """A2.9: tenant listing with derived per-stage state, aggregate and attempt (S2.md §A2.9).

    A foreign or unknown `subject_id` yields an empty list instead of a `404`: the filter
    never discloses whether a subject exists in another tenant (ADR-0006). Tombstoned
    classes are excluded. The SQL lives in `services/library` (ADR-0006).
    """
    response.headers["Cache-Control"] = "no-store"
    rows = await library.list_audios(
        db, user_id=session.user_id, subject_id=subject_id, limit=limit
    )
    return AudioListOut(items=[_list_item_out(row) for row in rows])


@router.get(
    "/audios/{audio_id}",
    responses={
        200: {"description": "Detalle con estados por etapa, agregado, intento y transcript"},
        401: {"description": "Sin sesión (auth_required)"},
        404: {"description": "Audio inexistente, ajeno o tombstoneado (not_found)"},
    },
)
async def get_audio(
    audio_id: uuid.UUID, response: Response, session: CurrentSession, db: DbSession
) -> AudioDetailOut:
    """A2.9: per-stage statuses + honest aggregate + latest attempt + export availability."""
    response.headers["Cache-Control"] = "no-store"
    audio = await _owned_live_audio(db, session.user_id, audio_id)
    detail = await library.get_audio_detail(db, user_id=session.user_id, audio=audio)
    return _detail_out(detail)


@router.get(
    "/audios/{audio_id}/progress",
    responses={
        200: {"description": "text/event-stream: snapshot, progress, ping, done|error"},
        401: {"description": "Sin sesión (auth_required)"},
        404: {"description": "Audio inexistente, ajeno o tombstoneado (not_found)"},
    },
)
async def audio_progress(
    audio_id: uuid.UUID,
    session: CurrentSession,
    db: DbSession,
    factory: ProgressSessions,
) -> Response:
    """A2.9: SSE progress read from the DB (S2.md §A2.9 on DISENO §3.5).

    Ownership is resolved BEFORE returning the stream, so a foreign id is a real `404` HTTP
    status. The stream is STATE: every connect/reconnect re-sends a fresh `snapshot`; deltas
    report only measured bytes/fragments and exactly one `done`/`error` closes it. Reads use
    the injected factory with a short-lived session per poll (no long transaction).
    """
    await _owned_live_audio(db, session.user_id, audio_id)
    stream = library.stream_progress(
        factory,
        user_id=session.user_id,
        audio_id=audio_id,
        poll_seconds=progress.POLL_SECONDS,
        heartbeat_seconds=progress.HEARTBEAT_SECONDS,
        max_seconds=progress.MAX_SECONDS,
    )
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            # Proxies must not buffer the stream (Caddy already disables buffering, J1.x).
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/audios/{audio_id}/transcript.txt",
    response_class=Response,
    responses={
        200: {"description": "text/plain con la transcripción activa (o el segmento pedido)"},
        401: {"description": "Sin sesión (auth_required)"},
        404: {"description": "Audio/transcript/segmento inexistente, ajeno o tombstoneado"},
    },
)
async def export_transcript_txt(
    audio_id: uuid.UUID,
    session: CurrentSession,
    db: DbSession,
    segment: Annotated[uuid.UUID | None, Query()] = None,
) -> Response:
    """A2.9: the durable text as-is. `?segment=` narrows to one owned segment (M9)."""
    audio = await _owned_live_audio(db, session.user_id, audio_id)
    transcript = await library.active_transcript(db, user_id=session.user_id, audio=audio)
    if transcript is None:
        raise not_found()
    text = transcript.text
    if segment is not None:
        row = await library.get_segment(
            db, user_id=session.user_id, transcript_id=transcript.id, segment_id=segment
        )
        if row is None:
            # Foreign or unknown segment: indistinguishable from missing (ADR-0006).
            raise not_found()
        text = row.text
    return Response(
        content=render_txt(text=text),
        media_type="text/plain",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": 'attachment; filename="transcript.txt"',
        },
    )


@router.get(
    "/audios/{audio_id}/transcript.srt",
    response_class=Response,
    responses={
        200: {"description": "application/x-subrip con tiempos medidos y ordenados"},
        401: {"description": "Sin sesión (auth_required)"},
        404: {"description": "Audio o transcript inexistente, ajeno o tombstoneado"},
        409: {"description": "srt_unavailable: sin tiempos válidos o fuera de la duración"},
    },
)
async def export_transcript_srt(
    audio_id: uuid.UUID, session: CurrentSession, db: DbSession
) -> Response:
    """A2.9: SRT only with usable times, else `409 srt_unavailable` (SPEC M3, fails closed)."""
    audio = await _owned_live_audio(db, session.user_id, audio_id)
    transcript = await library.active_transcript(db, user_id=session.user_id, audio=audio)
    if transcript is None:
        raise not_found()
    segments = await library.segments_for(
        db, user_id=session.user_id, transcript_id=transcript.id
    )
    if not srt_exportable(
        timestamp_precision=transcript.timestamp_precision,
        segments=segments,
        duration_seconds=audio.duration_seconds,
    ):
        raise srt_unavailable()
    return Response(
        content=render_srt(segments),
        media_type="application/x-subrip",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": 'attachment; filename="transcript.srt"',
        },
    )


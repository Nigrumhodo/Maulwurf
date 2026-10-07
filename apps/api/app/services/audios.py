"""Reserva de `POST /audios`: cuota, slot, token de variante y creación (A2.3, S2.md §A2.3).

Toda consulta de este ticket vive aquí y no en el router (guardia ADR-0006). El orden lo
decide el router: consentimiento → idioma → token → materia → cuota → slot → reserva. Por
eso ninguna función escribe antes de que todo lo anterior pase: **un rechazo no reserva
capacidad**. `Audio` e `IngestionAttempt` se crean en la misma transacción en la que se
consume el token, así que o se reserva y consume todo, o no queda nada.

Cuota (`429`) y slot (`503`) son mecanismos condicionales (A2.3/decisión D4): DISENO §3.1
exige `Retry-After`, pero el acta F0.1 mantiene D4 `blocked`, así que **sin límite publicado
no se limita**. No se inventan numéricos antes de que F0 los establezca.
"""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ApiError, validation_failed
from app.models import Audio, IngestionAttempt, VariantConfirmationToken
from app.models.ingestion import ACTIVE_INGESTION_STATES

# Proveedores que el aviso de privacidad declara al usuario (se registran con el intento).
DECLARED_PROVIDERS = {"asr": "nvidia-riva/whisper-large-v3"}
# Quién posee la reserva hasta que la ingesta asigne una instancia propia.
UNASSIGNED_OWNER = "api:unassigned"
# Pista de reintento provisional para 429/503: DISENO §3.1 exige la cabecera, pero D4 no ha
# publicado cuota ni capacidad todavía, así que es un backoff acotado, no un límite efectivo.
RETRY_AFTER_SECONDS = 60
# Cuota por usuario: sin valor publicado no se limita (fail-open documentado, a la espera
# de D4). El mecanismo existe y es testeable; publicar un valor activa el 429.
per_user_active_limit: int | None = None


@dataclass(frozen=True)
class Reservation:
    """Valores planos de la reserva: se leen antes del `commit` (el ORM expira tras él)."""

    audio_id: uuid.UUID
    attempt_id: uuid.UUID
    upload_expires_at: datetime


def allowed_languages() -> tuple[str, ...]:
    """Allowlist vigente (D3): lo publicado por el spike manda sobre la lista provisional."""
    capabilities = settings.ingestion_capabilities
    if capabilities.limits_validated:
        return capabilities.languages
    return settings.ingest_languages


def _token_hash(presented: str) -> str:
    # Mismo convenio que `app.services.sessions._sha256`: en BD solo vive el SHA-256 hex.
    return hashlib.sha256(presented.encode("utf-8", "surrogatepass")).hexdigest()


def _retry_after() -> dict[str, str]:
    return {"Retry-After": str(RETRY_AFTER_SECONDS)}


async def check_quota(db: AsyncSession, user_id: uuid.UUID) -> None:
    """`429 rate_limited` si el usuario ya ocupa su cuota; sin límite publicado no limita."""
    if per_user_active_limit is None:
        return
    active = await db.scalar(
        select(func.count())
        .select_from(IngestionAttempt)
        .where(
            IngestionAttempt.user_id == user_id,
            IngestionAttempt.status.in_(ACTIVE_INGESTION_STATES),
        )
    )
    if (active or 0) >= per_user_active_limit:
        raise ApiError(
            429,
            "rate_limited",
            "Límite de reservas alcanzado; inténtalo más tarde.",
            headers=_retry_after(),
        )


async def reserve_slot(db: AsyncSession) -> None:
    """`503 capacity_unavailable` si no queda slot libre; sin `active_slots` no limita."""
    limit = settings.ingestion_capabilities.active_slots
    if limit is None:
        # Sin capacidad publicada por el spike no hay cupo que respetar (D4 sigue blocked).
        return
    active = await db.scalar(
        select(func.count())
        .select_from(IngestionAttempt)
        .where(IngestionAttempt.status.in_(ACTIVE_INGESTION_STATES))
    )
    if (active or 0) >= limit:
        raise ApiError(
            503,
            "capacity_unavailable",
            "No queda capacidad de ingesta libre; inténtalo más tarde.",
            headers=_retry_after(),
        )


async def consume_variant_token(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    presented: str,
    subject_id: uuid.UUID,
    class_date: date,
    class_timezone: str,
    language_code: str,
) -> VariantConfirmationToken:
    """Bloquea el token (`FOR UPDATE`) y lo valida; el consumo real ocurre al reservar.

    Un token de otro tenant no existe para este usuario: inexistente, ajeno, caducado,
    consumido o con metadatos distintos comparten un único rechazo, para no revelar
    existencia ni estado (ADR-0006). El `FOR UPDATE` serializa el doble gasto.
    """
    result = await db.execute(
        select(VariantConfirmationToken)
        .where(
            VariantConfirmationToken.user_id == user_id,
            VariantConfirmationToken.token_hash == _token_hash(presented),
        )
        .with_for_update()
    )
    token: VariantConfirmationToken | None = result.scalar_one_or_none()
    now = datetime.now(UTC)
    if (
        token is None
        or token.consumed_at is not None
        or token.expires_at <= now
        or token.subject_id != subject_id
        or token.class_date != class_date
        or token.class_timezone != class_timezone
        or token.language_code != language_code
    ):
        raise validation_failed(
            {"variant_confirmation_token": "Token inválido, caducado o ya utilizado."}
        )
    return token


async def _mark_token_consumed(
    db: AsyncSession, *, token: VariantConfirmationToken, audio_id: uuid.UUID, at: datetime
) -> None:
    """Consumo atómico: `consumed_at` y `reserved_audio_id` van juntos (CHECK de la tabla)."""
    result = await db.execute(
        update(VariantConfirmationToken)
        .where(
            VariantConfirmationToken.id == token.id,
            VariantConfirmationToken.consumed_at.is_(None),
        )
        .values(consumed_at=at, reserved_audio_id=audio_id)
        .returning(VariantConfirmationToken.id)
    )
    if result.scalar_one_or_none() is None:
        # Solo llega aquí si otra petición consumió el token entre el bloqueo y este UPDATE:
        # la transacción entera se revierte y no queda reserva a medias.
        raise ApiError(
            409,
            "invalid_transition",
            "Este token de variante ya se usó; pide uno nuevo para continuar.",
        )


async def create_reservation(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    subject_id: uuid.UUID,
    title: str | None,
    teacher: str | None,
    class_date: date,
    class_timezone: str,
    language_code: str,
    privacy_notice_version: str,
    token: VariantConfirmationToken | None = None,
) -> Reservation:
    """Crea `Audio` (con `sha256=NULL`) e intento `awaiting_upload`, y consume el token.

    `sha256` queda en `NULL`: aquí aún no hay bytes, así que este POST **no decide
    dedupe** (lo hará el `PUT` de A2.4 con el hash real). `lease_expires_at` también queda
    en `NULL` (D6): el lease real lo asigna la ingesta al tomar la propiedad (S2.1).
    """
    now = datetime.now(UTC)
    audio = Audio(
        user_id=user_id,
        subject_id=subject_id,
        title=title,
        teacher=teacher,
        class_date=class_date,
        class_timezone=class_timezone,
        language_code=language_code,
    )
    db.add(audio)
    await db.flush()
    upload_expires_at = now + timedelta(minutes=settings.upload_ttl_minutes)
    attempt = IngestionAttempt(
        user_id=user_id,
        audio_id=audio.id,
        privacy_notice_version=privacy_notice_version,
        cloud_processing_accepted_at=now,
        third_party_voice_acknowledged_at=now,
        declared_providers=DECLARED_PROVIDERS,
        status="awaiting_upload",
        owner_instance=UNASSIGNED_OWNER,
        fencing_token=1,
        upload_expires_at=upload_expires_at,
    )
    db.add(attempt)
    await db.flush()
    if token is not None:
        await _mark_token_consumed(db, token=token, audio_id=audio.id, at=now)
    # El router construye la respuesta y confirma: si el commit falla, no hay reserva ni
    # token consumido, y el cliente puede volver a empezar sin perder nada.
    return Reservation(
        audio_id=audio.id,
        attempt_id=attempt.id,
        upload_expires_at=upload_expires_at,
    )

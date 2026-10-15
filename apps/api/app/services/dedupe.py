"""Dedupe in the `PUT`: exact identity, variants, and the confirmation token (A2.4/A2.5).

Normative source: S2.md §A2.5. All duplicate-decision SQL lives here and not in the router
(ADR-0006 guard). The identity is M2's: SHA-256 + language + subject + class date + time
zone (title and teacher never participate). Complete A2.5 semantics, unit-covered by
`tests/unit/test_dedupe_identity.py`:

- exact/variant lookups filtered by tenant, with `exclude_audio_id` so a retry of the same
  reservation never matches itself;
- opaque single-use token: only its SHA-256 lives in the DB, never the plaintext;
- absorption of the provisional reservation (M2: «clean up and release the reservation»)
  before any `200/409` response;
- identity writes converge: the `audios_dedupe_identity` unique index turns two simultaneous
  exact uploads into `duplicate_exact` toward the canonical;
- `is_late_arrival` settles the remaining tie: two simultaneous uploads with the same hash and
  DIFFERENT contexts can both miss the pre-commit lookups and the unique index does not collide
  across contexts (S2.md §A2.5 review finding 2026-10-11), so `services/uploads.py` re-checks
  after committing its identity and the late arrival yields to the older reservation.

The step order (clean → decide → commit → admit) is owned by `services/uploads.py`.
"""

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Audio, IngestionAttempt, VariantConfirmationToken
from app.services.audios import token_hash

# Bytes de entropía del token opaco: `token_urlsafe(32)` → 43 caracteres, bien dentro del
# `max_length=256` del campo `variant_confirmation_token` en `AudioCreate`.
TOKEN_BYTES = 32


@dataclass(frozen=True)
class Identity:
    """Contexto de identidad de ingesta (sin título/profesor, por M2)."""

    language_code: str
    subject_id: uuid.UUID
    class_date: date
    class_timezone: str


@dataclass(frozen=True)
class IssuedToken:
    """Valor plano y expiración del token recién emitido; el plano solo sale en el 409."""

    plaintext: str
    expires_at: datetime


def identity_of(audio: Audio) -> Identity:
    """Identidad de un audio ya reservado; se lee antes de cualquier `commit`/`rollback`."""
    return Identity(
        language_code=audio.language_code,
        subject_id=audio.subject_id,
        class_date=audio.class_date,
        class_timezone=audio.class_timezone,
    )


def _precedence(audio: Audio) -> tuple[datetime, uuid.UUID]:
    """Precedence key between two classes sharing a hash with different contexts."""
    created = audio.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return (created, audio.id)


def is_late_arrival(rival: Audio, own: Audio) -> bool:
    """True when `own` must yield to `rival`: the older reservation wins, ties broken by id.

    Deterministic in both directions, so a concurrent-variant race (same hash, different
    contexts, both uncommitted during each other's lookups) resolves toward exactly ONE
    canonical: the loser cleans up and hands its discarded context back as a confirmable
    variant (S2.md §A2.5 review finding 2026-10-11).
    """
    return _precedence(own) > _precedence(rival)


def _metadata_digest(identity: Identity) -> str:
    """HMAC de los metadatos normalizados que ve el usuario (DDL: `metadata_digest`).

    Mismo convenio que `sessions.csrf_token_for`: clave del backend, prefijo de ámbito y
    campos normalizados; el resultado solo se compara, nunca se revierte.
    """
    key = settings.secret_key.get_secret_value().encode()
    normalized = "|".join(
        (
            str(identity.subject_id),
            identity.class_date.isoformat(),
            identity.class_timezone,
            identity.language_code,
        )
    )
    return hmac.new(key, f"variant:{normalized}".encode(), hashlib.sha256).hexdigest()


async def lookup_exact_identity(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    sha256_hex: str,
    identity: Identity,
    exclude_audio_id: uuid.UUID,
) -> Audio | None:
    """Clase canónica con la misma identidad; el índice único garantiza como mucho una."""
    result = await db.execute(
        select(Audio).where(
            Audio.user_id == user_id,
            Audio.sha256 == sha256_hex,
            Audio.language_code == identity.language_code,
            Audio.subject_id == identity.subject_id,
            Audio.class_date == identity.class_date,
            Audio.class_timezone == identity.class_timezone,
            Audio.deleted_at.is_(None),
            Audio.id != exclude_audio_id,
        )
    )
    return result.scalar_one_or_none()


async def lookup_variant(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    sha256_hex: str,
    identity: Identity,
    exclude_audio_id: uuid.UUID,
) -> Audio | None:
    """Otra clase con el mismo hash y contexto distinto: candidata a canónica de variante."""
    result = await db.execute(
        select(Audio)
        .where(
            Audio.user_id == user_id,
            Audio.sha256 == sha256_hex,
            Audio.deleted_at.is_(None),
            Audio.id != exclude_audio_id,
            or_(
                Audio.language_code != identity.language_code,
                Audio.subject_id != identity.subject_id,
                Audio.class_date != identity.class_date,
                Audio.class_timezone != identity.class_timezone,
            ),
        )
        # Orden estable: con más de una candidata, siempre se elige la misma.
        .order_by(Audio.created_at)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def find_confirmed_variant(
    db: AsyncSession, *, user_id: uuid.UUID, audio_id: uuid.UUID
) -> VariantConfirmationToken | None:
    """Token ya consumido por A2.3 que reservó exactamente este audio (segundo `POST`)."""
    result = await db.execute(
        select(VariantConfirmationToken).where(
            VariantConfirmationToken.user_id == user_id,
            VariantConfirmationToken.reserved_audio_id == audio_id,
            VariantConfirmationToken.consumed_at.is_not(None),
        )
    )
    return result.scalar_one_or_none()


def verify_variant_hash(token: VariantConfirmationToken, sha256_hex: str) -> bool:
    """El segundo `PUT` compara su hash con el token ANTES de cualquier ASR (M2)."""
    return hmac.compare_digest(token.content_sha256, sha256_hex)


async def absorb_reservation(
    db: AsyncSession, *, audio: Audio, attempt: IngestionAttempt
) -> None:
    """Libera la reserva provisional que perdió el dedupe: intento + audio provisionales.

    Borra antes los tokens de variante ya gastados que apuntan a este audio: su FK
    `reserved_audio_id` no tiene `ON DELETE` y un token consumido sin reserva violaría su
    CHECK si solo se anulara el enlace (borde §8.4 del plan). Un token gastado no sirve
    para nada: su doble gasto ya queda bloqueado por `consumed_at`.
    """
    await db.execute(
        delete(VariantConfirmationToken).where(
            VariantConfirmationToken.reserved_audio_id == audio.id,
            VariantConfirmationToken.consumed_at.is_not(None),
        )
    )
    # El intento tiene FK compuesta (user_id, audio_id) a `audios` y los mappers no declaran
    # `relationship`, así que el unit-of-work no ordena los deletes: hay que flushear el
    # intento ANTES de marcar su audio, o el DELETE choca con la FK.
    await db.delete(attempt)
    await db.flush()
    await db.delete(audio)


async def admit_identity(
    db: AsyncSession,
    *,
    audio: Audio,
    attempt: IngestionAttempt,
    sha256_hex: str,
    received_bytes: int,
    expected_bytes: int | None,
) -> Audio | None:
    """Escribe la identidad recibida y confirma; devuelve la canónica si perdió la carrera.

    Si el commit revienta contra `audios_dedupe_identity` (otra subida exacta simultánea),
    se revierte TODO y se reconsulta: la unicidad transaccional converge sin dejar dos
    clases con la misma identidad. Devuelve `None` cuando la identidad quedó escrita.
    """
    # Capturas previas: `commit`/`rollback` expiran los atributos ORM (MissingGreenlet).
    owner_id = audio.user_id
    own_id = audio.id
    identity = identity_of(audio)
    audio.sha256 = sha256_hex
    audio.original_bytes = received_bytes
    attempt.received_bytes = received_bytes
    attempt.expected_bytes = expected_bytes
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return await lookup_exact_identity(
            db,
            user_id=owner_id,
            sha256_hex=sha256_hex,
            identity=identity,
            exclude_audio_id=own_id,
        )
    return None


async def issue_variant_token(
    db: AsyncSession,
    *,
    canonical: Audio,
    identity: Identity,
    content_sha256: str,
) -> IssuedToken:
    """Emite el token del `409 duplicate_variant`: un solo uso, TTL corto, solo hash en BD.

    Los metadatos persistidos son los del contexto DESCARTADO (la clase que el usuario
    intentaba crear): el segundo `POST` debe reproducirlos exactamente para consumirlo.
    """
    plaintext = secrets.token_urlsafe(TOKEN_BYTES)
    token = VariantConfirmationToken(
        user_id=canonical.user_id,
        token_hash=token_hash(plaintext),
        canonical_audio_id=canonical.id,
        content_sha256=content_sha256,
        subject_id=identity.subject_id,
        class_date=identity.class_date,
        class_timezone=identity.class_timezone,
        language_code=identity.language_code,
        metadata_digest=_metadata_digest(identity),
        expires_at=datetime.now(UTC) + timedelta(minutes=settings.variant_token_ttl_minutes),
    )
    db.add(token)
    await db.flush()
    return IssuedToken(plaintext=plaintext, expires_at=token.expires_at)

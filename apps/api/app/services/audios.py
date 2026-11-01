"""Reserva de `POST /audios`: cuota, slot, token de variante y creación (A2.3, S2.md §A2.3).

Toda consulta de este ticket vive aquí y no en el router (guardia ADR-0006). El orden lo
decide el router: consentimiento → idioma → token → materia → cuota → slot → reserva. Por
eso ninguna función escribe antes de que todo lo anterior pase: **un rechazo no reserva
capacidad**. `Audio` e `IngestionAttempt` se crean en la misma transacción en la que se
consume el token, así que o se reserva y consume todo, o no queda nada.

Cuota (`429`) y slot (`503`) son mecanismos condicionales (A2.3/decisión D4): DISENO §3.1
exige `Retry-After`, pero el acta F0.1 mantiene D4 `blocked`, así que **sin límite publicado
no se limita**. No se inventan numéricos antes de que F0 los establezca.

Cancelación, tombstone y fencing (A2.7, S2.md §A2.7): la máquina de estados decide y la BD
garantiza. `cas_transition` es el primitivo compare-and-swap que `uploads._abort_disconnected`
y el endpoint de cancelación comparten; `cancel_attempt` cancela E invalida el fencing en UNA
sentencia condicional (estilo `ingest/lease.py`), y `tombstone_audio` purga todos los
derivados de la clase en una sola transacción.

Reservation lifecycle (A2.14): `open_reception` claims the attempt under a row lock before
the first byte, `cancel_reservation` releases a failed PUT while the API still owns the
fencing token, and `expire_stale_reservations` reaps abandoned reservations for J2.4.

Contract conformance (A2.19): the language allowlist and the reservation deadlines are
resolved from the SAME published capabilities as the size limit (`allowed_languages`,
`attempt_deadlines`), and every unusable variant token — unknown, expired, consumed or
mismatched — answers the same opaque `422 validation_failed` (ADR-0006).
"""

import hashlib
import uuid
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal, cast

from sqlalchemy import CursorResult, Result, and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from app.core.config import settings
from app.core.errors import ApiError, not_found, validation_failed
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
    VariantConfirmationToken,
)
from app.models.ingestion import ACTIVE_INGESTION_STATES
from app.services import state_machine
from app.services.state_machine import TOMBSTONE_EXIT
from app.services.tenant import get_owned

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

# A2.14: PostgreSQL advisory-lock namespace. The two-int form keeps these keys apart from
# other applications' locks. The global key serializes every reservation against
# `active_slots`; the per-user key serializes one user's reservations against
# `per_user_active_limit`. Both are transaction-scoped, so the lock ends at the commit that
# makes the new attempt visible to the next counter.
_CAPACITY_LOCK_NAMESPACE = 0x4D57  # "MW"
_CAPACITY_GLOBAL_KEY = 0
# Statuses the API may still release: before `ingest` acquires the reception, only the API
# owns the row. From the successful acquire on, the fencing token moves and the CAS misses.
ApiOwnedStatus = Literal["awaiting_upload", "receiving"]


@dataclass(frozen=True)
class Reservation:
    """Valores planos de la reserva: se leen antes del `commit` (el ORM expira tras él)."""

    audio_id: uuid.UUID
    attempt_id: uuid.UUID
    upload_expires_at: datetime


@dataclass(frozen=True)
class AttemptDeadlines:
    """Ephemeral deadlines of one attempt; `None` means no approved TTL to enforce."""

    upload_expires_at: datetime
    receive_deadline: datetime | None
    asr_deadline: datetime | None


def allowed_languages() -> tuple[str, ...]:
    """Allowlist vigente (D3): lo publicado por el spike manda sobre la lista provisional.

    BCP-47 no distingue mayúsculas, así que la lista se normaliza igual que el `language_code`
    de la petición (`AudioCreate`): un código publicado como `ES` sigue siendo `es`.
    """
    capabilities = settings.ingestion_capabilities
    if capabilities.limits_validated:
        published = capabilities.languages
    else:
        published = settings.ingest_languages
    return tuple(dict.fromkeys(code.lower() for code in published))


def _effective_ttl_minutes() -> tuple[int, int | None, int | None]:
    """(upload, receive, asr) minutes: the benchmarked publication wins.

    S4.4 publishes the three TTLs independently of the size/duration limits, so they are
    effective even while `limits_validated` is false. Only an explicit no-TTL publication
    (all three null) falls back to the S1 provisional `upload_ttl_minutes`; in that case no
    receive/ASR deadline is invented.
    """
    ttl = settings.ingestion_capabilities.ttl
    if ttl.upload_start_minutes and ttl.receive_minutes and ttl.asr_minutes:
        return ttl.upload_start_minutes, ttl.receive_minutes, ttl.asr_minutes
    return settings.upload_ttl_minutes, None, None


def attempt_deadlines(now: datetime) -> AttemptDeadlines:
    """Deadlines of a new attempt, chained so each ephemeral phase has its own budget.

    The client must upload within `upload_start_minutes`; reception may then use
    `receive_minutes` and ASR `asr_minutes`. Chaining keeps `upload_expires_at <=
    receive_deadline <= asr_deadline`, which a per-phase-from-`now` calculation cannot
    guarantee. The API writes these values when it reserves; `ingest` owns the live lease.
    """
    upload_minutes, receive_minutes, asr_minutes = _effective_ttl_minutes()
    upload_expires_at = now + timedelta(minutes=upload_minutes)
    receive_deadline = (
        upload_expires_at + timedelta(minutes=receive_minutes)
        if receive_minutes is not None
        else None
    )
    asr_deadline = (
        receive_deadline + timedelta(minutes=asr_minutes)
        if receive_deadline is not None and asr_minutes is not None
        else None
    )
    return AttemptDeadlines(
        upload_expires_at=upload_expires_at,
        receive_deadline=receive_deadline,
        asr_deadline=asr_deadline,
    )


def token_hash(presented: str) -> str:
    """SHA-256 hex de un token opaco: público desde A2.4 para que emisión y consumo (A2.3)
    compartan UN solo convenio; en BD solo vive el hash, jamás el valor presentado."""
    # Mismo convenio que `app.services.sessions._sha256`: en BD solo vive el SHA-256 hex.
    return hashlib.sha256(presented.encode("utf-8", "surrogatepass")).hexdigest()


def _retry_after() -> dict[str, str]:
    return {"Retry-After": str(RETRY_AFTER_SECONDS)}


def _rowcount(result: Result[Any]) -> int:
    # `Session.execute` está tipeado como `Result`, pero un DML devuelve `CursorResult`
    # con `rowcount` en runtime (A2.7): aquí se decide la carrera por filas afectadas,
    # nunca por el estado que el objeto en memoria cree tener.
    return cast("CursorResult[Any]", result).rowcount


def _users_capacity_key(user_id: uuid.UUID) -> int:
    """Stable 31-bit advisory key for one user (fits the signed `int4` of the lock).

    A collision only serializes two unrelated users for a moment; it can never admit more
    reservations than the published limit.
    """
    return user_id.int & 0x7FFF_FFFF


async def _lock_capacity(db: AsyncSession, key: int) -> None:
    """Take the transaction-scoped advisory lock that guards one capacity counter."""
    await db.execute(select(func.pg_advisory_xact_lock(_CAPACITY_LOCK_NAMESPACE, key)))


async def check_quota(db: AsyncSession, user_id: uuid.UUID) -> None:
    """`429 rate_limited` if the user already fills the quota; no published limit = no limit.

    The per-user advisory lock is taken before the count and released by the reservation's
    commit, so two concurrent `POST /audios` of the same user cannot both pass the check
    (A2.14). Without a published limit the function does not lock anything (D4).
    """
    if per_user_active_limit is None:
        return
    await _lock_capacity(db, _users_capacity_key(user_id))
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
    """`503 capacity_unavailable` if no slot is free; no published `active_slots` = no limit.

    The global advisory lock is taken before the count and released by the reservation's
    commit, so concurrent `POST /audios` from different users cannot exceed `active_slots`
    (A2.14). While D4 is blocked nothing locks: there is no published limit to enforce.
    """
    limit = settings.ingestion_capabilities.active_slots
    if limit is None:
        # Sin capacidad publicada por el spike no hay cupo que respetar (D4 sigue blocked).
        return
    await _lock_capacity(db, _CAPACITY_GLOBAL_KEY)
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
            VariantConfirmationToken.token_hash == token_hash(presented),
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
        # Solo llega aquí si otra petición consumió el token entre el bloqueo y este UPDATE.
        # La transacción entera se revierte y la respuesta es la MISMA opaca del lookup
        # (422 sobre el campo): un 409 revelaría que el token existió, A2.19.
        raise validation_failed(
            {"variant_confirmation_token": "Token inválido, caducado o ya utilizado."}
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
    en `NULL` (D6): el lease real lo asigna la ingesta al tomar la propiedad (S2.1). Los
    deadlines efímeros salen de `attempt_deadlines`: TTL publicado si existe (A2.19).
    """
    now = datetime.now(UTC)
    deadlines = attempt_deadlines(now)
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
        upload_expires_at=deadlines.upload_expires_at,
        receive_deadline=deadlines.receive_deadline,
        asr_deadline=deadlines.asr_deadline,
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
        upload_expires_at=deadlines.upload_expires_at,
    )


# --- A2.7: cancelación, tombstone y fencing ---------------------------------------------


async def cas_transition(
    db: AsyncSession,
    *,
    attempt: IngestionAttempt,
    target: str,
    error_code: str | None = None,
) -> None:
    """Valida con la máquina y DESPUÉS gana la escritura o la pierde honestamente (H2).

    La máquina de estados valida el estado en memoria; el `UPDATE` condicional garantiza
    que la fila siga en ese estado al escribir, así una carrera (commit de `ingest`,
    otro request) nunca se pisa. Cero filas = otro escritor movió el intento primero →
    el mismo `409 invalid_transition` que levantaría la máquina.
    """
    state_machine.ensure_transition(attempt.status, target)
    values: dict[str, object] = {"status": target}
    if error_code is not None:
        values["error_code"] = error_code
    result = await db.execute(
        update(IngestionAttempt)
        .where(
            IngestionAttempt.id == attempt.id,
            IngestionAttempt.user_id == attempt.user_id,
            IngestionAttempt.status == attempt.status,
        )
        # `synchronize_session=False`: la fila manda y el objeto se sincroniza a mano
        # debajo; que la sesión re-aplique la sentencia convertiría el commit en una
        # escritura INCONDICIONAL capaz de deshacer el CAS que acaba de ganar.
        .execution_options(synchronize_session=False)
        .values(**values)
    )
    if _rowcount(result) != 1:
        raise state_machine.invalid_transition(attempt.status, target)
    # Estado comprometido de la fila, no atributo sucio: un assign marcaría el objeto
    # como modificado y el `commit()` posterior emitiría un segundo `UPDATE` sin las
    # condiciones del CAS (H2 volvería a abrir la carrera que acaba de cerrarse).
    set_committed_value(attempt, "status", target)
    if error_code is not None:
        set_committed_value(attempt, "error_code", error_code)


async def cancel_attempt(db: AsyncSession, *, attempt: IngestionAttempt) -> str:
    """Cancelación explícita (A2.7): cancela E invalida el fencing en UNA sentencia.

    Idempotente sobre un intento ya `cancelled` (no-op real: ni estado, ni `error_code`,
    ni token). Tras el commit —o en cualquier estado final— la máquina responde `409
    invalid_transition` y el transcript nunca se destruye (DISENO §3.5). `ingest` ve el
    token nuevo en su próximo heartbeat, mata el subprocess y verifica la limpieza.
    """
    if attempt.status == TOMBSTONE_EXIT:
        return TOMBSTONE_EXIT
    # Autoridad única: desde `transcript_committed_cleanup_pending` o desde un final, la
    # máquina levanta el 409 con ambos estados para el cliente.
    state_machine.ensure_transition(attempt.status, TOMBSTONE_EXIT)
    expected = attempt.status
    row = (
        await db.execute(
            update(IngestionAttempt)
            .where(
                IngestionAttempt.id == attempt.id,
                IngestionAttempt.user_id == attempt.user_id,
                IngestionAttempt.status == expected,
            )
            # `synchronize_session=False`: la fila manda y el objeto se sincroniza con el
            # `RETURNING`; que la sesión re-aplique la sentencia convertiría el commit en
            # un `UPDATE` incondicional con el token leído antes de la carrera.
            .execution_options(synchronize_session=False)
            .values(
                status=TOMBSTONE_EXIT,
                fencing_token=IngestionAttempt.fencing_token + 1,
                lease_expires_at=None,
            )
            .returning(
                IngestionAttempt.status,
                IngestionAttempt.fencing_token,
                IngestionAttempt.lease_expires_at,
            )
        )
    ).one_or_none()
    if row is None:
        # El intento se movió mientras escribíamos: la verdad la tiene la fila.
        await db.refresh(attempt, ("status",))
        if attempt.status == TOMBSTONE_EXIT:
            return TOMBSTONE_EXIT
        raise state_machine.invalid_transition(attempt.status, TOMBSTONE_EXIT)
    # El token nuevo lo calcula la BD (`fencing_token + 1`), no este proceso: se registra
    # como estado comprometido para que el commit no emita un segundo `UPDATE` con un
    # valor leído antes de la carrera —ese eco bajaría el token y reabriría la valla.
    status, fencing_token, lease_expires_at = row
    set_committed_value(attempt, "status", status)
    set_committed_value(attempt, "fencing_token", fencing_token)
    set_committed_value(attempt, "lease_expires_at", lease_expires_at)
    return TOMBSTONE_EXIT


@dataclass(frozen=True)
class TombstoneResult:
    """Conteos por tabla de una transacción de tombstone: alimentan el `202` trazable."""

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


async def tombstone_audio(
    db: AsyncSession, *, user_id: uuid.UUID, audio: Audio
) -> TombstoneResult:
    """Tombstone de una clase (A2.7): cancela lo activo y purga cada derivado.

    Una sola transacción: `deleted_at` primero para que nadie la lea como viva; invalida
    el fencing de los intentos activos (un commit tardío falla su chequeo de token);
    outbox y runs se detienen; la purga dura va en orden de FK con conteos verificables.
    Los eventos de Google existentes se conservan por defecto (DISENO §3.5): borrarlos
    exige la opción explícita y `google_remote_operations`, que son S3/F2.
    """
    now = datetime.now(UTC)
    # El guardia del router es una LECTURA (TOCTOU): esta sentencia condicional es la que
    # gana o pierde la carrera contra otro `DELETE` concurrente. El candado de `audios` se
    # toma primero, el mismo orden que usa cualquier otro escritor de esta transacción
    # (`admit_identity`, `_restore_identity`: audios → ingestion_attempts), para que una
    # subida o un segundo DELETE esperen en lugar de cruzar candados.
    marked = await db.execute(
        update(Audio)
        .where(
            Audio.user_id == user_id,
            Audio.id == audio.id,
            Audio.deleted_at.is_(None),
        )
        .execution_options(synchronize_session=False)
        # `active_transcript_version`: nadie trate una versión purgada como la activa
        # (retry/reupload la leen, A2.8).
        .values(deleted_at=now, active_transcript_version=None)
    )
    if _rowcount(marked) != 1:
        raise not_found()  # otro DELETE llegó primero (ADR-0006: «ya no existe» = 404)
    set_committed_value(audio, "deleted_at", now)
    set_committed_value(audio, "active_transcript_version", None)

    cancelled = await db.execute(
        update(IngestionAttempt)
        .where(
            IngestionAttempt.user_id == user_id,
            IngestionAttempt.audio_id == audio.id,
            IngestionAttempt.status.in_(ACTIVE_INGESTION_STATES),
        )
        .execution_options(synchronize_session=False)
        .values(
            status=TOMBSTONE_EXIT,
            fencing_token=IngestionAttempt.fencing_token + 1,
            lease_expires_at=None,
        )
    )
    skipped = await db.execute(
        update(OutboxEvent)
        .where(
            OutboxEvent.user_id == user_id,
            OutboxEvent.resource_type == "audio",
            OutboxEvent.resource_id == audio.id,
            OutboxEvent.status == "pending",
        )
        # `skipped` y nunca `enabled=false`: el CHECK `cleanup_gate` solo admite eventos
        # index/analyze deshabilitados con `blocked_reason='cleanup_pending'`, que sería
        # mentira —esta clase no espera limpieza, está muerta—.
        .execution_options(synchronize_session=False)
        .values(status="skipped")
    )
    stopped = await db.execute(
        update(ProcessingRun)
        .where(
            ProcessingRun.user_id == user_id,
            ProcessingRun.audio_id == audio.id,
            ProcessingRun.status.in_(("pending", "running")),
        )
        .execution_options(synchronize_session=False)
        .values(status="cancelled", finished_at=datetime.now(UTC))
    )

    transcript_ids = select(Transcript.id).where(
        Transcript.user_id == user_id, Transcript.audio_id == audio.id
    )
    segment_ids = select(Segment.id).where(
        Segment.user_id == user_id, Segment.transcript_id.in_(transcript_ids)
    )
    chunk_ids = select(Chunk.id).where(
        Chunk.user_id == user_id, Chunk.audio_id == audio.id
    )

    # Citas primero: `message_sources` tiene FK hacia transcript/chunk/segment. El
    # `RETURNING` da los mensajes citados y el conteo en la misma lectura (el resultado
    # de un DML con `returning` no expone `rowcount`).
    source_rows = (
        await db.execute(
            delete(MessageSource)
            .where(
                MessageSource.user_id == user_id,
                or_(
                    MessageSource.audio_id == audio.id,
                    MessageSource.transcript_id.in_(transcript_ids),
                    MessageSource.chunk_id.in_(chunk_ids),
                    MessageSource.segment_id.in_(segment_ids),
                ),
            )
            .returning(MessageSource.message_id)
        )
    ).all()
    cited_message_ids = [row[0] for row in source_rows]

    # Content-free tombstone (M2): «invalidated nunca deja texto recuperable». La
    # procedencia no se puede separar con seguridad, así que el mensaje que citaba estas
    # fuentes —y los de las conversaciones `mode='class'` de esta clase— queda vacío.
    conditions = [
        Message.conversation_id.in_(
            select(Conversation.id).where(
                Conversation.user_id == user_id,
                Conversation.mode == "class",
                Conversation.audio_id == audio.id,
            )
        )
    ]
    if cited_message_ids:
        conditions.append(Message.id.in_(cited_message_ids))
    messages = await db.execute(
        update(Message)
        .where(Message.user_id == user_id, or_(*conditions))
        .execution_options(synchronize_session=False)
        .values(content="", citas=[])
    )

    # Purga dura en orden de FK (embeddings → chunks → segments → transcripts); el orden
    # explícito, en vez de la cascada, es lo que hace verificables los conteos.
    embeddings = await db.execute(
        delete(Embedding)
        .where(Embedding.user_id == user_id, Embedding.chunk_id.in_(chunk_ids))
        .execution_options(synchronize_session=False)
    )
    chunk_links = await db.execute(
        delete(ChunkSegment)
        .where(ChunkSegment.user_id == user_id, ChunkSegment.chunk_id.in_(chunk_ids))
        .execution_options(synchronize_session=False)
    )
    chunks = await db.execute(
        delete(Chunk)
        .where(Chunk.user_id == user_id, Chunk.audio_id == audio.id)
        .execution_options(synchronize_session=False)
    )
    segments = await db.execute(
        delete(Segment)
        .where(Segment.user_id == user_id, Segment.transcript_id.in_(transcript_ids))
        .execution_options(synchronize_session=False)
    )
    transcripts = await db.execute(
        delete(Transcript)
        .where(Transcript.user_id == user_id, Transcript.audio_id == audio.id)
        .execution_options(synchronize_session=False)
    )

    return TombstoneResult(
        attempts_cancelled=_rowcount(cancelled) or 0,
        outbox_events_skipped=_rowcount(skipped) or 0,
        processing_runs_cancelled=_rowcount(stopped) or 0,
        message_sources_deleted=len(source_rows),
        messages_tombstoned=_rowcount(messages) or 0,
        embeddings_deleted=_rowcount(embeddings) or 0,
        chunk_segments_deleted=_rowcount(chunk_links) or 0,
        chunks_deleted=_rowcount(chunks) or 0,
        segments_deleted=_rowcount(segments) or 0,
        transcripts_deleted=_rowcount(transcripts) or 0,
    )


# --- A2.14: reservation lifecycle and PUT concurrency -----------------------------------


@dataclass(frozen=True)
class ReceptionClaim:
    """Attempt claimed for reception: fresh rows plus the token the sink must present."""

    audio: Audio
    attempt: IngestionAttempt
    fencing_token: int


def _attempt_not_active() -> ApiError:
    return ApiError(409, "attempt_not_active", "Este intento de subida ya no está activo.")


def _upload_expired() -> ApiError:
    return ApiError(410, "upload_expired", "La subida expiró; vuelve a crear la clase.")


async def open_reception(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    audio_id: uuid.UUID,
    attempt_id: uuid.UUID,
    now: datetime | None = None,
) -> ReceptionClaim:
    """Lock the attempt and move ``awaiting_upload → receiving`` before the first byte.

    The row lock spans the ownership check and the commit that publishes ``receiving``, so
    two concurrent ``PUT`` requests cannot both pass: the loser waits and then reads the
    claimed row, or reads it after the claim and gets ``409``; only one reaches the sink.
    The fencing token is deliberately NOT bumped here: `ingest` still acquires it and
    increments it (ADR-0008), so replay protection and lease ownership stay where they are.

    Rows are re-read after the commit (a session may expire objects on commit, and a
    concurrent tombstone must win before any byte is read). A row that already expired is
    cancelled while the lock is still held, so the slot is freed immediately.
    """
    clock = now or datetime.now(UTC)
    audio = await get_owned(db, Audio, user_id, audio_id)
    if audio is None or audio.deleted_at is not None:
        raise not_found()
    attempt = await get_owned(db, IngestionAttempt, user_id, attempt_id, lock="update")
    if attempt is None or attempt.audio_id != audio.id:
        raise not_found()
    if attempt.status != "awaiting_upload":
        raise _attempt_not_active()
    if attempt.upload_expires_at is None:
        raise _attempt_not_active()
    if attempt.upload_expires_at <= clock:
        await cas_transition(
            db, attempt=attempt, target=TOMBSTONE_EXIT, error_code="upload_expired"
        )
        await db.commit()
        raise _upload_expired()
    state_machine.transition(attempt, "receiving")
    fencing_token = attempt.fencing_token
    await db.commit()
    audio = await get_owned(db, Audio, user_id, audio_id)
    attempt = await get_owned(db, IngestionAttempt, user_id, attempt_id)
    if audio is None or audio.deleted_at is not None or attempt is None:
        raise not_found()
    if attempt.status != "receiving" or attempt.fencing_token != fencing_token:
        raise _attempt_not_active()
    return ReceptionClaim(audio=audio, attempt=attempt, fencing_token=fencing_token)


async def cancel_reservation(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    attempt_id: uuid.UUID,
    fencing_token: int,
    from_status: ApiOwnedStatus,
) -> bool:
    """Best-effort release of a reservation that the API still owns; never raises.

    The fencing token is the ownership guard: once `ingest` acquires the reception it bumps
    the token, the conditional update matches no row and the terminal state is written by
    `ingest` under its lease (ADR-0008 §5). A failed release (for example, the database is
    down) also matches no row; `expire_stale_reservations` reaps the reservation after its
    window. `from_status` is explicit so a pre-claim rejection can never cancel a reception
    that another concurrent PUT already claimed.
    """
    try:
        # A failed reception may leave a poisoned transaction; the release needs a clean one.
        await db.rollback()
        result = await db.execute(
            update(IngestionAttempt)
            .where(
                IngestionAttempt.id == attempt_id,
                IngestionAttempt.user_id == user_id,
                IngestionAttempt.status == from_status,
                IngestionAttempt.fencing_token == fencing_token,
            )
            .execution_options(synchronize_session=False)
            .values(
                status=TOMBSTONE_EXIT,
                fencing_token=IngestionAttempt.fencing_token + 1,
                lease_expires_at=None,
            )
        )
        released = _rowcount(result) == 1
        await db.commit()
        return released
    except Exception:
        with suppress(Exception):
            await db.rollback()
        return False


def _stale_reservation_conditions(now: datetime) -> tuple[Any, ...]:
    """Rows past `upload_expires_at` that no lease owner took: only the API can reap them."""
    return (
        IngestionAttempt.upload_expires_at.is_not(None),
        IngestionAttempt.upload_expires_at <= now,
        or_(
            IngestionAttempt.status == "awaiting_upload",
            and_(
                IngestionAttempt.status == "receiving",
                IngestionAttempt.lease_expires_at.is_(None),
            ),
        ),
    )


async def expire_stale_reservations(
    db: AsyncSession, *, now: datetime, limit: int = 100
) -> int:
    """Cancel reservations past `upload_expires_at` that never reached an ingest lease.

    Idempotent: every returned row was ``awaiting_upload``, or ``receiving`` without a
    lease, so a second call with the same `now` counts zero. A ``receiving``/``transcribing``
    row WITH a lease belongs to `ingest`; its expired lease is S2.7's reconciler, not this
    function. The caller owns the transaction (the J2.4 reconciler's session commits it).
    """
    if limit < 1:
        raise ValueError("limit must be positive")
    due = (
        select(IngestionAttempt.id)
        .where(*_stale_reservation_conditions(now))
        .order_by(IngestionAttempt.upload_expires_at, IngestionAttempt.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    result = await db.execute(
        update(IngestionAttempt)
        .where(IngestionAttempt.id.in_(due), *_stale_reservation_conditions(now))
        .execution_options(synchronize_session=False)
        .values(
            status=TOMBSTONE_EXIT,
            error_code="upload_expired",
            fencing_token=IngestionAttempt.fencing_token + 1,
            lease_expires_at=None,
        )
        .returning(IngestionAttempt.id)
    )
    return len(result.all())

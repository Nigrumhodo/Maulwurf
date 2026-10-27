"""Shape S1 de ingesta y outbox (A1.5, DISENO §2.4 y §3.1; S1.md §1.3–§1.4).

No hay columna de almacenamiento de audio: el audio nunca es durable. A2.1 completa el
esquema en S2 (tokens de variante, transcripts, segmentos).
"""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Double,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantOwned, created_at, updated_at, uuid_pk

INGESTION_STATES = (
    "awaiting_upload",
    "receiving",
    "transcribing",
    "transcript_committed_cleanup_pending",
    "succeeded",
    "requires_reupload",
    "rejected",
    "cancelled",
)
# Explícito, no un slice: alimenta el índice parcial `attempts_one_active` (literal en la
# migración 0001), y reordenar INGESTION_STATES no debe cambiarlo en silencio.
ACTIVE_INGESTION_STATES = (
    "awaiting_upload",
    "receiving",
    "transcribing",
    "transcript_committed_cleanup_pending",
)
CLEANUP_STATES = ("pending", "verified", "failed")
# Solo estos tipos nacen bloqueados hasta cleanup verificado (S1.md §1.3).
CLEANUP_GATED_EVENT_TYPES = ("index_requested", "analyze_requested")
OUTBOX_STATES = ("pending", "dispatched", "completed", "failed", "skipped")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Audio(TenantOwned, Base):
    __tablename__ = "audios"
    __table_args__ = (
        UniqueConstraint("user_id", "id"),
        ForeignKeyConstraint(["user_id", "subject_id"], ["subjects.user_id", "subjects.id"]),
        Index(
            "audios_dedupe_identity",
            "user_id",
            "sha256",
            "language_code",
            "subject_id",
            "class_date",
            "class_timezone",
            unique=True,
            postgresql_where=text("deleted_at IS NULL AND sha256 IS NOT NULL"),
        ),
        Index(
            "audios_variant_lookup",
            "user_id",
            "sha256",
            postgresql_where=text("deleted_at IS NULL AND sha256 IS NOT NULL"),
        ),
        Index(
            "audios_library",
            "user_id",
            "subject_id",
            text("class_date DESC"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    teacher: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[str | None] = mapped_column(Text)  # desconocido al reservar
    class_date: Mapped[date] = mapped_column(Date, nullable=False)
    class_timezone: Mapped[str] = mapped_column(Text, nullable=False)  # snapshot IANA
    language_code: Mapped[str] = mapped_column(Text, nullable=False)
    original_bytes: Mapped[int | None] = mapped_column(BigInteger)
    duration_seconds: Mapped[float | None] = mapped_column(Double)
    required_stages: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{index}'")
    )
    active_transcript_version: Mapped[int | None] = mapped_column(Integer)
    active_analysis_version: Mapped[int | None] = mapped_column(Integer)
    active_index_version: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IngestionAttempt(TenantOwned, Base):
    __tablename__ = "ingestion_attempts"
    __table_args__ = (
        UniqueConstraint("user_id", "id"),
        ForeignKeyConstraint(["user_id", "audio_id"], ["audios.user_id", "audios.id"]),
        CheckConstraint(_in("status", INGESTION_STATES), name="status"),
        CheckConstraint(_in("cleanup_status", CLEANUP_STATES), name="cleanup_status"),
        # Un único intento activo por clase; cleanup pendiente sigue siendo trabajo activo.
        Index(
            "attempts_one_active",
            "audio_id",
            unique=True,
            postgresql_where=text(_in("status", ACTIVE_INGESTION_STATES)),
        ),
        Index("attempts_by_audio", "user_id", "audio_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    audio_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    privacy_notice_version: Mapped[str] = mapped_column(Text, nullable=False)
    cloud_processing_accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    third_party_voice_acknowledged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    declared_providers: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    owner_instance: Mapped[str] = mapped_column(Text, nullable=False)
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    upload_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    receive_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    asr_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    expected_bytes: Mapped[int | None] = mapped_column(BigInteger)
    fragments_done: Mapped[int | None] = mapped_column(Integer)
    fragments_total: Mapped[int | None] = mapped_column(Integer)
    cleanup_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    cleanup_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cleanup_evidence: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'")
    )
    audio_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class VariantConfirmationToken(TenantOwned, Base):
    """Token opaco de confirmación de variante; solo se persiste su hash."""

    __tablename__ = "variant_confirmation_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash"),
        ForeignKeyConstraint(
            ["user_id", "canonical_audio_id"], ["audios.user_id", "audios.id"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(["user_id", "subject_id"], ["subjects.user_id", "subjects.id"]),
        ForeignKeyConstraint(["user_id", "reserved_audio_id"], ["audios.user_id", "audios.id"]),
        CheckConstraint(
            "consumed_at IS NULL OR reserved_audio_id IS NOT NULL", name="consumed_has_reservation"
        ),
        Index("variant_tokens_expiry", "expires_at", postgresql_where=text("consumed_at IS NULL")),
        Index("variant_tokens_by_canonical", "user_id", "canonical_audio_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_audio_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    content_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    class_date: Mapped[date] = mapped_column(Date, nullable=False)
    class_timezone: Mapped[str] = mapped_column(Text, nullable=False)
    language_code: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_digest: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reserved_audio_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = created_at()


class OutboxEvent(TenantOwned, Base):
    """Evento durable: solo IDs y configuración no sensible, jamás audio ni tokens."""

    __tablename__ = "outbox_events"
    __table_args__ = (
        CheckConstraint("blocked_reason IN ('cleanup_pending')", name="blocked_reason"),
        CheckConstraint(_in("status", OUTBOX_STATES), name="status"),
        CheckConstraint(
            f"({_in('type', CLEANUP_GATED_EVENT_TYPES)} AND ((enabled AND blocked_reason IS NULL)"
            # IS NOT DISTINCT FROM: con `= 'cleanup_pending'` un NULL daría NULL y el CHECK
            # aceptaría una fila deshabilitada sin motivo.
            " OR (NOT enabled AND blocked_reason IS NOT DISTINCT FROM 'cleanup_pending')))"
            f" OR (NOT {_in('type', CLEANUP_GATED_EVENT_TYPES)}"
            " AND enabled AND blocked_reason IS NULL)",
            name="cleanup_gate",
        ),
        UniqueConstraint(
            "type",
            "resource_id",
            "resource_version",
            "dedupe_key",
            postgresql_nulls_not_distinct=True,
        ),
        Index(
            "outbox_pending",
            "status",
            "next_attempt_at",
            postgresql_where=text("enabled AND status = 'pending'"),
        ),
        Index("outbox_by_user", "user_id", postgresql_where=text("status <> 'completed'")),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    type: Mapped[str] = mapped_column(Text, nullable=False)
    resource_type: Mapped[str] = mapped_column(Text, nullable=False)
    resource_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    resource_version: Mapped[int | None] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'")
    )
    # Sin default a propósito: quien emite decide y el CHECK `cleanup_gate` lo valida.
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    blocked_reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="3")
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dedupe_key: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()

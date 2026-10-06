"""Transcript, segmentos y ejecuciones durables de procesamiento (A2.1)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Double,
    ForeignKeyConstraint,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantOwned, created_at, uuid_pk


class Transcript(TenantOwned, Base):
    __tablename__ = "transcripts"
    __table_args__ = (
        UniqueConstraint("user_id", "id"),
        UniqueConstraint("audio_id", "version"),
        ForeignKeyConstraint(
            ["user_id", "audio_id"], ["audios.user_id", "audios.id"], ondelete="CASCADE"
        ),
        CheckConstraint(
            "timestamp_precision IN ('word', 'segment', 'none')", name="timestamp_precision"
        ),
        CheckConstraint("char_count = char_length(text)", name="char_count_matches_text"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    audio_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    language_requested: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False, server_default="nvidia-riva")
    model: Mapped[str] = mapped_column(Text, nullable=False)
    model_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    client_version: Mapped[str | None] = mapped_column(Text)
    timestamp_precision: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    quality_flags: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=sql_text("'[]'")
    )
    warnings: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=sql_text("'[]'")
    )
    fts_config: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    topics: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    analysis_version: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = created_at()


class Segment(TenantOwned, Base):
    __tablename__ = "segments"
    __table_args__ = (
        UniqueConstraint("user_id", "id"),
        UniqueConstraint("transcript_id", "ordinal"),
        ForeignKeyConstraint(
            ["user_id", "transcript_id"],
            ["transcripts.user_id", "transcripts.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("char_start < char_end", name="char_range"),
        CheckConstraint(
            "(t_start IS NULL AND t_end IS NULL) OR "
            "(t_start IS NOT NULL AND t_end IS NOT NULL AND 0 <= t_start AND t_start < t_end)",
            name="time_range",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    transcript_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    t_start: Mapped[float | None] = mapped_column(Double)
    t_end: Mapped[float | None] = mapped_column(Double)


class ProcessingRun(TenantOwned, Base):
    __tablename__ = "processing_runs"
    __table_args__ = (
        UniqueConstraint("user_id", "id"),
        UniqueConstraint("audio_id", "stage", "transcript_version", "config_version"),
        ForeignKeyConstraint(["user_id", "audio_id"], ["audios.user_id", "audios.id"]),
        CheckConstraint("stage IN ('index', 'analyze')", name="stage"),
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')", name="status"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    audio_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    stage: Mapped[str] = mapped_column(Text, nullable=False)
    transcript_version: Mapped[int] = mapped_column(Integer, nullable=False)
    config_version: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error_code: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()

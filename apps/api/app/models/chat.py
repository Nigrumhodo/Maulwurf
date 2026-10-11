"""Persistencia tenant-aware del chat y sus fuentes validadas (A2.1)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantOwned, created_at, updated_at, uuid_pk


class Conversation(TenantOwned, Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("user_id", "id"),
        ForeignKeyConstraint(["user_id", "subject_id"], ["subjects.user_id", "subjects.id"]),
        ForeignKeyConstraint(["user_id", "audio_id"], ["audios.user_id", "audios.id"]),
        CheckConstraint("mode IN ('all', 'subject', 'class')", name="mode"),
        CheckConstraint(
            "mode <> 'all' OR (subject_id IS NULL AND audio_id IS NULL)", name="all_scope"
        ),
        CheckConstraint("mode <> 'subject' OR subject_id IS NOT NULL", name="subject_scope"),
        CheckConstraint("mode <> 'class' OR audio_id IS NOT NULL", name="class_scope"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    audio_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    title: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Message(TenantOwned, Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("user_id", "id"),
        ForeignKeyConstraint(
            ["user_id", "conversation_id"],
            ["conversations.user_id", "conversations.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("role IN ('user', 'assistant', 'system')", name="role"),
        CheckConstraint(
            "status IN ('streaming', 'completed', 'failed', 'cancelled')", name="status"
        ),
        Index(
            "messages_client_idem",
            "conversation_id",
            "client_message_id",
            unique=True,
            postgresql_where=text("client_message_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    client_message_id: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="completed")
    citas: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'"))
    model: Mapped[str | None] = mapped_column(Text)
    prompt_version: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = created_at()


class MessageSource(TenantOwned, Base):
    __tablename__ = "message_sources"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "message_id"], ["messages.user_id", "messages.id"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(["user_id", "audio_id"], ["audios.user_id", "audios.id"]),
        ForeignKeyConstraint(
            ["user_id", "transcript_id"], ["transcripts.user_id", "transcripts.id"]
        ),
        ForeignKeyConstraint(["user_id", "chunk_id"], ["chunks.user_id", "chunks.id"]),
        ForeignKeyConstraint(["user_id", "segment_id"], ["segments.user_id", "segments.id"]),
        CheckConstraint("kind IN ('transcript', 'calendar', 'task', 'general')", name="kind"),
        CheckConstraint(
            "kind <> 'transcript' OR (audio_id IS NOT NULL AND transcript_id IS NOT NULL "
            "AND transcript_version IS NOT NULL AND chunk_id IS NOT NULL)",
            name="transcript_source_complete",
        ),
        Index("message_sources_by_audio", "user_id", "audio_id"),
        Index("message_sources_by_message", "user_id", "message_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    message_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    audio_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    transcript_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    transcript_version: Mapped[int | None] = mapped_column(Integer)
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    segment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    delivered_at: Mapped[datetime] = created_at()

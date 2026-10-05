"""Modelo durable de chunking, embeddings y generaciones de índice (A2.1)."""

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantOwned, created_at, uuid_pk


class Chunk(TenantOwned, Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("user_id", "id"),
        UniqueConstraint("transcript_id", "index_version", "ordinal"),
        ForeignKeyConstraint(
            ["user_id", "audio_id"], ["audios.user_id", "audios.id"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["user_id", "transcript_id"],
            ["transcripts.user_id", "transcripts.id"],
            ondelete="CASCADE",
        ),
        Index("chunks_fts", "tsv", postgresql_using="gin"),
        Index("chunks_by_audio", "user_id", "audio_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    audio_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    transcript_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    transcript_version: Mapped[int] = mapped_column(Integer, nullable=False)
    index_version: Mapped[str] = mapped_column(Text, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    t_start: Mapped[float | None] = mapped_column()
    t_end: Mapped[float | None] = mapped_column()
    tsv: Mapped[Any | None] = mapped_column(TSVECTOR)


class ChunkSegment(Base):
    __tablename__ = "chunk_segments"
    __table_args__ = (
        PrimaryKeyConstraint("user_id", "chunk_id", "segment_id"),
        ForeignKeyConstraint(
            ["user_id", "chunk_id"], ["chunks.user_id", "chunks.id"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["user_id", "segment_id"], ["segments.user_id", "segments.id"], ondelete="CASCADE"
        ),
        Index("chunk_segments_by_segment", "user_id", "segment_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    chunk_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    segment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    overlap_span: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class Embedding(Base):
    __tablename__ = "embeddings"
    __table_args__ = (
        PrimaryKeyConstraint("user_id", "chunk_id", "index_version"),
        ForeignKeyConstraint(
            ["user_id", "chunk_id"], ["chunks.user_id", "chunks.id"], ondelete="CASCADE"
        ),
        Index(
            "embeddings_hnsw",
            "vector",
            postgresql_using="hnsw",
            postgresql_ops={"vector": "vector_cosine_ops"},
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    chunk_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    index_version: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    vector: Mapped[Any] = mapped_column(VECTOR(1536), nullable=False)
    created_at: Mapped[datetime] = created_at()


class IndexGeneration(TenantOwned, Base):
    __tablename__ = "index_generations"
    __table_args__ = (
        UniqueConstraint("user_id", "version"),
        CheckConstraint("status IN ('building', 'active', 'retired')", name="status"),
        Index(
            "index_generations_one_active",
            "user_id",
            unique=True,
            postgresql_where="status = 'active'",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    version: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()

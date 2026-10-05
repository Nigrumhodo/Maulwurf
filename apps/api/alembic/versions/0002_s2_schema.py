"""A2.1: esquema S2 tenant-aware para dedupe, RAG, outbox y chat.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "variant_confirmation_tokens",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("canonical_audio_id", sa.UUID(), nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column("subject_id", sa.UUID(), nullable=False),
        sa.Column("class_date", sa.Date(), nullable=False),
        sa.Column("class_timezone", sa.Text(), nullable=False),
        sa.Column("language_code", sa.Text(), nullable=False),
        sa.Column("metadata_digest", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("reserved_audio_id", sa.UUID()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "consumed_at IS NULL OR reserved_audio_id IS NOT NULL",
            name=op.f("ck_variant_confirmation_tokens_consumed_has_reservation"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name=op.f("fk_variant_confirmation_tokens_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "canonical_audio_id"],
            ["audios.user_id", "audios.id"],
            ondelete="CASCADE",
            name=op.f("fk_variant_confirmation_tokens_user_id_canonical_audio_id_audios"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "reserved_audio_id"],
            ["audios.user_id", "audios.id"],
            name=op.f("fk_variant_confirmation_tokens_user_id_reserved_audio_id_audios"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "subject_id"],
            ["subjects.user_id", "subjects.id"],
            name=op.f("fk_variant_confirmation_tokens_user_id_subject_id_subjects"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_variant_confirmation_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_variant_confirmation_tokens_token_hash")),
    )
    op.create_index(
        "variant_tokens_expiry",
        "variant_confirmation_tokens",
        ["expires_at"],
        postgresql_where=sa.text("consumed_at IS NULL"),
    )
    op.create_index(
        "variant_tokens_by_canonical",
        "variant_confirmation_tokens",
        ["user_id", "canonical_audio_id"],
    )

    op.create_table(
        "transcripts",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("audio_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("language_requested", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), server_default="nvidia-riva", nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("model_config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("client_version", sa.Text()),
        sa.Column("timestamp_precision", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("char_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "quality_flags",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
        sa.Column(
            "warnings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
        sa.Column("fts_config", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text()),
        sa.Column("topics", postgresql.ARRAY(sa.Text())),
        sa.Column("analysis_version", sa.Integer()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "timestamp_precision IN ('word', 'segment', 'none')",
            name=op.f("ck_transcripts_timestamp_precision"),
        ),
        sa.CheckConstraint(
            "char_count = char_length(text)", name=op.f("ck_transcripts_char_count_matches_text")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name=op.f("fk_transcripts_user_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "audio_id"],
            ["audios.user_id", "audios.id"],
            ondelete="CASCADE",
            name=op.f("fk_transcripts_user_id_audio_id_audios"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transcripts")),
        sa.UniqueConstraint("user_id", "id", name=op.f("uq_transcripts_user_id_id")),
        sa.UniqueConstraint("audio_id", "version", name=op.f("uq_transcripts_audio_id_version")),
    )
    op.create_table(
        "segments",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("transcript_id", sa.UUID(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("t_start", sa.Double()),
        sa.Column("t_end", sa.Double()),
        sa.CheckConstraint("char_start < char_end", name=op.f("ck_segments_char_range")),
        sa.CheckConstraint(
            "(t_start IS NULL AND t_end IS NULL) OR "
            "(t_start IS NOT NULL AND t_end IS NOT NULL AND 0 <= t_start AND t_start < t_end)",
            name=op.f("ck_segments_time_range"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name=op.f("fk_segments_user_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "transcript_id"],
            ["transcripts.user_id", "transcripts.id"],
            ondelete="CASCADE",
            name=op.f("fk_segments_user_id_transcript_id_transcripts"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_segments")),
        sa.UniqueConstraint("user_id", "id", name=op.f("uq_segments_user_id_id")),
        sa.UniqueConstraint(
            "transcript_id", "ordinal", name=op.f("uq_segments_transcript_id_ordinal")
        ),
    )
    op.create_table(
        "chunks",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("audio_id", sa.UUID(), nullable=False),
        sa.Column("transcript_id", sa.UUID(), nullable=False),
        sa.Column("transcript_version", sa.Integer(), nullable=False),
        sa.Column("index_version", sa.Text(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("t_start", sa.Double()),
        sa.Column("t_end", sa.Double()),
        sa.Column("tsv", postgresql.TSVECTOR()),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name=op.f("fk_chunks_user_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "audio_id"],
            ["audios.user_id", "audios.id"],
            ondelete="CASCADE",
            name=op.f("fk_chunks_user_id_audio_id_audios"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "transcript_id"],
            ["transcripts.user_id", "transcripts.id"],
            ondelete="CASCADE",
            name=op.f("fk_chunks_user_id_transcript_id_transcripts"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chunks")),
        sa.UniqueConstraint("user_id", "id", name=op.f("uq_chunks_user_id_id")),
        sa.UniqueConstraint(
            "transcript_id",
            "index_version",
            "ordinal",
            name=op.f("uq_chunks_transcript_id_index_version_ordinal"),
        ),
    )
    op.create_index("chunks_fts", "chunks", ["tsv"], postgresql_using="gin")
    op.create_index("chunks_by_audio", "chunks", ["user_id", "audio_id"])
    op.create_table(
        "chunk_segments",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("chunk_id", sa.UUID(), nullable=False),
        sa.Column("segment_id", sa.UUID(), nullable=False),
        sa.Column("overlap_span", postgresql.JSONB(astext_type=sa.Text())),
        sa.ForeignKeyConstraint(
            ["user_id", "chunk_id"],
            ["chunks.user_id", "chunks.id"],
            ondelete="CASCADE",
            name=op.f("fk_chunk_segments_user_id_chunk_id_chunks"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "segment_id"],
            ["segments.user_id", "segments.id"],
            ondelete="CASCADE",
            name=op.f("fk_chunk_segments_user_id_segment_id_segments"),
        ),
        sa.PrimaryKeyConstraint(
            "user_id", "chunk_id", "segment_id", name=op.f("pk_chunk_segments")
        ),
    )
    op.create_index("chunk_segments_by_segment", "chunk_segments", ["user_id", "segment_id"])
    op.create_table(
        "embeddings",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("chunk_id", sa.UUID(), nullable=False),
        sa.Column("index_version", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("vector", VECTOR(1536), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "chunk_id"],
            ["chunks.user_id", "chunks.id"],
            ondelete="CASCADE",
            name=op.f("fk_embeddings_user_id_chunk_id_chunks"),
        ),
        sa.PrimaryKeyConstraint("user_id", "chunk_id", "index_version", name=op.f("pk_embeddings")),
    )
    op.execute("CREATE INDEX embeddings_hnsw ON embeddings USING hnsw (vector vector_cosine_ops)")
    op.create_table(
        "index_generations",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('building', 'active', 'retired')", name=op.f("ck_index_generations_status")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name=op.f("fk_index_generations_user_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_index_generations")),
        sa.UniqueConstraint(
            "user_id", "version", name=op.f("uq_index_generations_user_id_version")
        ),
    )
    op.create_index(
        "index_generations_one_active",
        "index_generations",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_table(
        "processing_runs",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("audio_id", sa.UUID(), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("transcript_version", sa.Integer(), nullable=False),
        sa.Column("config_version", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text()),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_code", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("stage IN ('index', 'analyze')", name=op.f("ck_processing_runs_stage")),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')",
            name=op.f("ck_processing_runs_status"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name=op.f("fk_processing_runs_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "audio_id"],
            ["audios.user_id", "audios.id"],
            name=op.f("fk_processing_runs_user_id_audio_id_audios"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_processing_runs")),
        sa.UniqueConstraint("user_id", "id", name=op.f("uq_processing_runs_user_id_id")),
        sa.UniqueConstraint(
            "audio_id",
            "stage",
            "transcript_version",
            "config_version",
            name=op.f("uq_processing_runs_audio_id_stage_transcript_version_config_version"),
        ),
    )
    op.create_table(
        "conversations",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("subject_id", sa.UUID()),
        sa.Column("audio_id", sa.UUID()),
        sa.Column("title", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "mode IN ('all', 'subject', 'class')", name=op.f("ck_conversations_mode")
        ),
        sa.CheckConstraint(
            "mode <> 'all' OR (subject_id IS NULL AND audio_id IS NULL)",
            name=op.f("ck_conversations_all_scope"),
        ),
        sa.CheckConstraint(
            "mode <> 'subject' OR subject_id IS NOT NULL",
            name=op.f("ck_conversations_subject_scope"),
        ),
        sa.CheckConstraint(
            "mode <> 'class' OR audio_id IS NOT NULL", name=op.f("ck_conversations_class_scope")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name=op.f("fk_conversations_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "subject_id"],
            ["subjects.user_id", "subjects.id"],
            name=op.f("fk_conversations_user_id_subject_id_subjects"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "audio_id"],
            ["audios.user_id", "audios.id"],
            name=op.f("fk_conversations_user_id_audio_id_audios"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversations")),
        sa.UniqueConstraint("user_id", "id", name=op.f("uq_conversations_user_id_id")),
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), server_default="", nullable=False),
        sa.Column("client_message_id", sa.Text()),
        sa.Column("status", sa.Text(), server_default="completed", nullable=False),
        sa.Column(
            "citas",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
        sa.Column("model", sa.Text()),
        sa.Column("prompt_version", sa.Text()),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('user', 'assistant', 'system')", name=op.f("ck_messages_role")
        ),
        sa.CheckConstraint(
            "status IN ('streaming', 'completed', 'failed', 'cancelled')",
            name=op.f("ck_messages_status"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name=op.f("fk_messages_user_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "conversation_id"],
            ["conversations.user_id", "conversations.id"],
            ondelete="CASCADE",
            name=op.f("fk_messages_user_id_conversation_id_conversations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
        sa.UniqueConstraint("user_id", "id", name=op.f("uq_messages_user_id_id")),
    )
    op.create_index(
        "messages_client_idem",
        "messages",
        ["conversation_id", "client_message_id"],
        unique=True,
        postgresql_where=sa.text("client_message_id IS NOT NULL"),
    )
    op.create_table(
        "message_sources",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("message_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("audio_id", sa.UUID()),
        sa.Column("transcript_id", sa.UUID()),
        sa.Column("transcript_version", sa.Integer()),
        sa.Column("chunk_id", sa.UUID()),
        sa.Column("segment_id", sa.UUID()),
        sa.Column(
            "delivered_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('transcript', 'calendar', 'task', 'general')",
            name=op.f("ck_message_sources_kind"),
        ),
        sa.CheckConstraint(
            "kind <> 'transcript' OR (audio_id IS NOT NULL AND transcript_id IS NOT NULL "
            "AND transcript_version IS NOT NULL AND chunk_id IS NOT NULL)",
            name=op.f("ck_message_sources_transcript_source_complete"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name=op.f("fk_message_sources_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "message_id"],
            ["messages.user_id", "messages.id"],
            ondelete="CASCADE",
            name=op.f("fk_message_sources_user_id_message_id_messages"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "audio_id"],
            ["audios.user_id", "audios.id"],
            name=op.f("fk_message_sources_user_id_audio_id_audios"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "transcript_id"],
            ["transcripts.user_id", "transcripts.id"],
            name=op.f("fk_message_sources_user_id_transcript_id_transcripts"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "chunk_id"],
            ["chunks.user_id", "chunks.id"],
            name=op.f("fk_message_sources_user_id_chunk_id_chunks"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "segment_id"],
            ["segments.user_id", "segments.id"],
            name=op.f("fk_message_sources_user_id_segment_id_segments"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_message_sources")),
    )
    op.create_index("message_sources_by_audio", "message_sources", ["user_id", "audio_id"])
    op.create_index("message_sources_by_message", "message_sources", ["user_id", "message_id"])


def downgrade() -> None:
    op.drop_table("message_sources")
    op.drop_index("messages_client_idem", table_name="messages")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("processing_runs")
    op.drop_index("index_generations_one_active", table_name="index_generations")
    op.drop_table("index_generations")
    op.execute("DROP INDEX embeddings_hnsw")
    op.drop_table("embeddings")
    op.drop_table("chunk_segments")
    op.drop_index("chunks_by_audio", table_name="chunks")
    op.drop_index("chunks_fts", table_name="chunks")
    op.drop_table("chunks")
    op.drop_table("segments")
    op.drop_table("transcripts")
    op.drop_index("variant_tokens_by_canonical", table_name="variant_confirmation_tokens")
    op.drop_index("variant_tokens_expiry", table_name="variant_confirmation_tokens")
    op.drop_table("variant_confirmation_tokens")

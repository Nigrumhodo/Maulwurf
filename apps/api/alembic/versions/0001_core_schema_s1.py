"""A1.5: esquema núcleo S1 (identidad, sesión, materias) y shape S1 de ingesta/outbox.

Revision ID: 0001
Revises:
Create Date: 2026-09-26 20:22:51.442944
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:

    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("google_sub", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("email_verified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("timezone", sa.Text(), server_default="UTC", nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('active','deleting','deleted')", name=op.f("ck_users_status")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("google_sub", name=op.f("uq_users_google_sub")),
    )
    op.create_table(
        "google_credentials",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("access_token_enc", sa.LargeBinary(), nullable=False),
        sa.Column("refresh_token_enc", sa.LargeBinary(), nullable=True),
        sa.Column("key_version", sa.Integer(), nullable=False),
        sa.Column(
            "scopes", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False
        ),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), server_default="connected", nullable=False),
        sa.Column("last_refresh_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('connected','disconnected')", name=op.f("ck_google_credentials_status")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_google_credentials_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_google_credentials")),
    )
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("resource_type", sa.Text(), nullable=False),
        sa.Column("resource_id", sa.UUID(), nullable=False),
        sa.Column("resource_version", sa.Integer(), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("blocked_reason", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dedupe_key", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "(type IN ('index_requested', 'analyze_requested') AND ((enabled AND blocked_reason IS"
            " NULL) OR (NOT enabled AND blocked_reason IS NOT DISTINCT FROM 'cleanup_pending'))) "
            "OR (NOT type IN "
            "('index_requested', 'analyze_requested') AND enabled AND blocked_reason IS NULL)",
            name=op.f("ck_outbox_events_cleanup_gate"),
        ),
        sa.CheckConstraint(
            "blocked_reason IN ('cleanup_pending')", name=op.f("ck_outbox_events_blocked_reason")
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'dispatched', 'completed', 'failed', 'skipped')",
            name=op.f("ck_outbox_events_status"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_outbox_events_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox_events")),
        sa.UniqueConstraint(
            "type",
            "resource_id",
            "resource_version",
            "dedupe_key",
            name=op.f("uq_outbox_events_type_resource_id_resource_version_dedupe_key"),
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        "outbox_by_user",
        "outbox_events",
        ["user_id"],
        unique=False,
        postgresql_where=sa.text("status <> 'completed'"),
    )
    op.create_index(
        "outbox_pending",
        "outbox_events",
        ["status", "next_attempt_at"],
        unique=False,
        postgresql_where=sa.text("enabled AND status = 'pending'"),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("csrf_hash", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_sessions_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_sessions_token_hash")),
    )
    op.create_index("sessions_user", "sessions", ["user_id", "expires_at"], unique=False)
    op.create_table(
        "subjects",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("color", sa.Text(), server_default="#6366f1", nullable=False),
        sa.Column("teacher", sa.Text(), nullable=True),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_subjects_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_subjects")),
        sa.UniqueConstraint("user_id", "id", name=op.f("uq_subjects_user_id_id")),
    )
    op.create_index(
        "subjects_active_name",
        "subjects",
        ["user_id", sa.literal_column("lower(name)")],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "audios",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("subject_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("teacher", sa.Text(), nullable=True),
        sa.Column("sha256", sa.Text(), nullable=True),
        sa.Column("class_date", sa.Date(), nullable=False),
        sa.Column("class_timezone", sa.Text(), nullable=False),
        sa.Column("language_code", sa.Text(), nullable=False),
        sa.Column("original_bytes", sa.BigInteger(), nullable=True),
        sa.Column("duration_seconds", sa.Double(), nullable=True),
        sa.Column(
            "required_stages",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{index}'"),
            nullable=False,
        ),
        sa.Column("active_transcript_version", sa.Integer(), nullable=True),
        sa.Column("active_analysis_version", sa.Integer(), nullable=True),
        sa.Column("active_index_version", sa.Text(), nullable=True),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id", "subject_id"],
            ["subjects.user_id", "subjects.id"],
            name=op.f("fk_audios_user_id_subject_id_subjects"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_audios_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audios")),
        sa.UniqueConstraint("user_id", "id", name=op.f("uq_audios_user_id_id")),
    )
    op.create_index(
        "audios_dedupe_identity",
        "audios",
        ["user_id", "sha256", "language_code", "subject_id", "class_date", "class_timezone"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL AND sha256 IS NOT NULL"),
    )
    op.create_index(
        "audios_library",
        "audios",
        ["user_id", "subject_id", sa.literal_column("class_date DESC")],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "audios_variant_lookup",
        "audios",
        ["user_id", "sha256"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL AND sha256 IS NOT NULL"),
    )
    op.create_table(
        "ingestion_attempts",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("audio_id", sa.UUID(), nullable=False),
        sa.Column("privacy_notice_version", sa.Text(), nullable=False),
        sa.Column("cloud_processing_accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("third_party_voice_acknowledged_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("declared_providers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("owner_instance", sa.Text(), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("upload_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("receive_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("asr_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_bytes", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("expected_bytes", sa.BigInteger(), nullable=True),
        sa.Column("fragments_done", sa.Integer(), nullable=True),
        sa.Column("fragments_total", sa.Integer(), nullable=True),
        sa.Column("cleanup_status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("cleanup_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "cleanup_evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("audio_deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
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
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "cleanup_status IN ('pending', 'verified', 'failed')",
            name=op.f("ck_ingestion_attempts_cleanup_status"),
        ),
        sa.CheckConstraint(
            "status IN ('awaiting_upload', 'receiving', 'transcribing', "
            "'transcript_committed_cleanup_pending', 'succeeded', 'requires_reupload', 'rejected',"
            " 'cancelled')",
            name=op.f("ck_ingestion_attempts_status"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "audio_id"],
            ["audios.user_id", "audios.id"],
            name=op.f("fk_ingestion_attempts_user_id_audio_id_audios"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_ingestion_attempts_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ingestion_attempts")),
        sa.UniqueConstraint("user_id", "id", name=op.f("uq_ingestion_attempts_user_id_id")),
    )
    op.create_index(
        "attempts_by_audio", "ingestion_attempts", ["user_id", "audio_id"], unique=False
    )
    op.create_index(
        "attempts_one_active",
        "ingestion_attempts",
        ["audio_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('awaiting_upload', 'receiving', 'transcribing', "
            "'transcript_committed_cleanup_pending')"
        ),
    )


def downgrade() -> None:

    op.drop_index(
        "attempts_one_active",
        table_name="ingestion_attempts",
        postgresql_where=sa.text(
            "status IN ('awaiting_upload', 'receiving', 'transcribing', "
            "'transcript_committed_cleanup_pending')"
        ),
    )
    op.drop_index("attempts_by_audio", table_name="ingestion_attempts")
    op.drop_table("ingestion_attempts")
    op.drop_index(
        "audios_variant_lookup",
        table_name="audios",
        postgresql_where=sa.text("deleted_at IS NULL AND sha256 IS NOT NULL"),
    )
    op.drop_index(
        "audios_library", table_name="audios", postgresql_where=sa.text("deleted_at IS NULL")
    )
    op.drop_index(
        "audios_dedupe_identity",
        table_name="audios",
        postgresql_where=sa.text("deleted_at IS NULL AND sha256 IS NOT NULL"),
    )
    op.drop_table("audios")
    op.drop_index(
        "subjects_active_name",
        table_name="subjects",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_table("subjects")
    op.drop_index("sessions_user", table_name="sessions")
    op.drop_table("sessions")
    op.drop_index(
        "outbox_pending",
        table_name="outbox_events",
        postgresql_where=sa.text("enabled AND status = 'pending'"),
    )
    op.drop_index(
        "outbox_by_user",
        table_name="outbox_events",
        postgresql_where=sa.text("status <> 'completed'"),
    )
    op.drop_table("outbox_events")
    op.drop_table("google_credentials")
    op.drop_table("users")

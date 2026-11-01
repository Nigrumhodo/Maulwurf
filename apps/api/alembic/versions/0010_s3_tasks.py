"""A3.1: the complete M4 task tables — `tasks`, `task_sources` and `task_revisions`.

Builds on the frozen §M4 contract (L3.2, `app/schemas/analysis.py`): statuses, exclusive
`due_date`/`due_at`, `date_status`, `confidence_score` in [0, 1] and Unicode spans mapped to
`(char_start, char_end)`. The active row keeps the Calendar block
(`event_start_at`/`event_end_at` **or** `event_start_date`/`event_end_date_exclusive`, with
a later end), the separate `calendar_sync_status`, the optimistic-locking `version`, the
reminder `due_version`, and the nullable `calendar_etag` baseline J3.6 needs before a
conditional PATCH.

Deliberate boundaries:

- `task_evidence` (the literal quote beside each span) is A3.2's table; this migration
  creates only the provenance table A3.1 owns.
- `calendar_events.task_id` still has no composite FK: A3.8 owns that mirror and its read
  path deliberately stores task links before any task row is exposed. The link is not
  created here.
- Two inbox indexes, one per due representation (`(user_id, status, due_at)` and
  `(user_id, status, due_date)`), both partial on the non-null column. A single
  `COALESCE(date::timestamptz)` expression would depend on the session timezone and lose
  the index on one of the two shapes.

Row-level security follows A2.17/0005 and the J3.3/J3.7 pattern: `maulwurf_app` is strictly
tenant-bound, and `maulwurf_jobs` may work on one bound tenant or with the explicit
`app.cross_tenant='on'` switch (the Calendar sync job and the analysis worker). The ingest
role has no access to task data.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("tasks", "task_sources", "task_revisions")
APP_ROLE = "maulwurf_app"
JOBS_ROLE = "maulwurf_jobs"
SAFE_USER_SETTING = "NULLIF(current_setting('app.user_id', true), '')::uuid"
CROSS_TENANT = "NULLIF(current_setting('app.cross_tenant', true), '') = 'on'"


def _create_tasks() -> None:
    op.create_table(
        "tasks",
        sa.Column(
            "id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("audio_id", sa.UUID()),
        sa.Column("subject_id", sa.UUID()),
        sa.Column("source", sa.Text(), server_default="extraction", nullable=False),
        sa.Column("tipo", sa.Text(), nullable=False),
        sa.Column("titulo", sa.Text(), nullable=False),
        sa.Column("detalle", sa.Text()),
        sa.Column("due_date", sa.Date()),
        sa.Column("due_at", sa.DateTime(timezone=True)),
        sa.Column("timezone", sa.Text(), nullable=False),
        sa.Column("all_day", sa.Boolean(), nullable=False),
        sa.Column("date_status", sa.Text(), nullable=False),
        sa.Column("date_text", sa.Text()),
        sa.Column("date_reason", sa.Text()),
        sa.Column("confidence_score", sa.Double()),
        sa.Column("status", sa.Text(), server_default="needs_review", nullable=False),
        sa.Column(
            "calendar_sync_status", sa.Text(), server_default="not_requested", nullable=False
        ),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("due_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("event_start_at", sa.DateTime(timezone=True)),
        sa.Column("event_end_at", sa.DateTime(timezone=True)),
        sa.Column("event_start_date", sa.Date()),
        sa.Column("event_end_date_exclusive", sa.Date()),
        sa.Column("calendar_id", sa.Text()),
        sa.Column("calendar_event_id", sa.Text()),
        sa.Column("calendar_etag", sa.Text()),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("dismissed_at", sa.DateTime(timezone=True)),
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
        sa.CheckConstraint(
            "source IN ('extraction', 'chat', 'manual')",
            name=op.f("ck_tasks_source"),
        ),
        sa.CheckConstraint(
            "tipo IN ('examen', 'tarea', 'entrega', 'lectura', 'recordatorio', "
            "'recomendacion', 'proyecto')",
            name=op.f("ck_tasks_tipo"),
        ),
        sa.CheckConstraint(
            "date_status IN ('resolved', 'ambiguous', 'missing')",
            name=op.f("ck_tasks_date_status"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'needs_review', 'confirmed', 'dismissed', 'completed')",
            name=op.f("ck_tasks_status"),
        ),
        sa.CheckConstraint(
            "calendar_sync_status IN "
            "('not_requested', 'pending', 'synced', 'failed', 'blocked')",
            name=op.f("ck_tasks_calendar_sync_status"),
        ),
        sa.CheckConstraint(
            "confidence_score >= 0 AND confidence_score <= 1",
            name=op.f("ck_tasks_confidence_range"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_tasks_version")),
        sa.CheckConstraint("due_version >= 1", name=op.f("ck_tasks_due_version")),
        sa.CheckConstraint(
            "(date_status = 'resolved' AND all_day "
            "AND due_date IS NOT NULL AND due_at IS NULL) "
            "OR (date_status = 'resolved' AND NOT all_day "
            "AND due_at IS NOT NULL AND due_date IS NULL) "
            "OR (date_status IN ('ambiguous', 'missing') "
            "AND due_date IS NULL AND due_at IS NULL)",
            name=op.f("ck_tasks_date_contract"),
        ),
        sa.CheckConstraint(
            "(event_start_at IS NULL AND event_end_at IS NULL "
            "AND event_start_date IS NULL AND event_end_date_exclusive IS NULL) "
            "OR (event_start_at IS NOT NULL AND event_end_at IS NOT NULL "
            "AND event_start_date IS NULL AND event_end_date_exclusive IS NULL "
            "AND event_start_at < event_end_at) "
            "OR (event_start_at IS NULL AND event_end_at IS NULL "
            "AND event_start_date IS NOT NULL AND event_end_date_exclusive IS NOT NULL "
            "AND event_start_date < event_end_date_exclusive)",
            name=op.f("ck_tasks_calendar_block"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "audio_id"],
            ["audios.user_id", "audios.id"],
            name=op.f("fk_tasks_user_id_audio_id_audios"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "subject_id"],
            ["subjects.user_id", "subjects.id"],
            name=op.f("fk_tasks_user_id_subject_id_subjects"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name=op.f("fk_tasks_user_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tasks")),
        sa.UniqueConstraint("user_id", "id", name=op.f("uq_tasks_user_id_id")),
    )
    op.create_index("tasks_by_audio", "tasks", ["user_id", "audio_id"])
    op.create_index("tasks_by_subject", "tasks", ["user_id", "subject_id"])
    op.create_index(
        "tasks_inbox_due_at",
        "tasks",
        ["user_id", "status", "due_at"],
        postgresql_where=sa.text("due_at IS NOT NULL"),
    )
    op.create_index(
        "tasks_inbox_due_date",
        "tasks",
        ["user_id", "status", "due_date"],
        postgresql_where=sa.text("due_date IS NOT NULL"),
    )


def _create_task_sources() -> None:
    op.create_table(
        "task_sources",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("task_id", sa.UUID(), nullable=False),
        sa.Column("audio_id", sa.UUID(), nullable=False),
        sa.Column("transcript_id", sa.UUID(), nullable=False),
        sa.Column("transcript_version", sa.Integer(), nullable=False),
        sa.Column("segment_id", sa.UUID(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "char_start >= 0 AND char_start < char_end",
            name=op.f("ck_task_sources_char_range"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "task_id"],
            ["tasks.user_id", "tasks.id"],
            ondelete="CASCADE",
            name=op.f("fk_task_sources_user_id_task_id_tasks"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "audio_id"],
            ["audios.user_id", "audios.id"],
            ondelete="CASCADE",
            name=op.f("fk_task_sources_user_id_audio_id_audios"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "transcript_id"],
            ["transcripts.user_id", "transcripts.id"],
            name=op.f("fk_task_sources_user_id_transcript_id_transcripts"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "segment_id"],
            ["segments.user_id", "segments.id"],
            name=op.f("fk_task_sources_user_id_segment_id_segments"),
        ),
        sa.PrimaryKeyConstraint(
            "user_id",
            "task_id",
            "segment_id",
            "char_start",
            "char_end",
            name=op.f("pk_task_sources"),
        ),
    )
    op.create_index("task_sources_by_audio", "task_sources", ["user_id", "audio_id"])
    op.create_index(
        "task_sources_by_transcript", "task_sources", ["user_id", "transcript_id"]
    )
    op.create_index("task_sources_by_segment", "task_sources", ["user_id", "segment_id"])


def _create_task_revisions() -> None:
    op.create_table(
        "task_revisions",
        sa.Column(
            "id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("task_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID()),
        sa.Column("base_task_version", sa.Integer(), nullable=False),
        sa.Column("revision_type", sa.Text(), nullable=False),
        sa.Column("proposed_fields", postgresql.JSONB(), nullable=False),
        sa.Column(
            "evidence_validated", sa.Boolean(), server_default="false", nullable=False
        ),
        sa.Column(
            "status", sa.Text(), server_default="pending_confirmation", nullable=False
        ),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "revision_type IN ('reanalysis', 'edit_of_confirmed')",
            name=op.f("ck_task_revisions_revision_type"),
        ),
        sa.CheckConstraint(
            "status IN ('pending_confirmation', 'activated', 'discarded')",
            name=op.f("ck_task_revisions_status"),
        ),
        sa.CheckConstraint(
            "base_task_version >= 1", name=op.f("ck_task_revisions_base_task_version")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(proposed_fields) = 'object'",
            name=op.f("ck_task_revisions_proposed_fields"),
        ),
        sa.CheckConstraint(
            "(status = 'activated') = (activated_at IS NOT NULL)",
            name=op.f("ck_task_revisions_activated_at"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "task_id"],
            ["tasks.user_id", "tasks.id"],
            ondelete="CASCADE",
            name=op.f("fk_task_revisions_user_id_task_id_tasks"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "run_id"],
            ["processing_runs.user_id", "processing_runs.id"],
            name=op.f("fk_task_revisions_user_id_run_id_processing_runs"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name=op.f("fk_task_revisions_user_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_revisions")),
        sa.UniqueConstraint("user_id", "id", name=op.f("uq_task_revisions_user_id_id")),
        sa.UniqueConstraint(
            "task_id",
            "base_task_version",
            "revision_type",
            "run_id",
            name=op.f(
                "uq_task_revisions_task_id_base_task_version_revision_type_run_id"
            ),
        ),
    )
    op.create_index("task_revisions_by_task", "task_revisions", ["user_id", "task_id"])
    op.create_index("task_revisions_by_run", "task_revisions", ["user_id", "run_id"])


def _apply_tenant_policies(table: str) -> None:
    op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON "{table}"
          FOR ALL TO "{APP_ROLE}"
          USING (user_id = current_setting('app.user_id')::uuid)
          WITH CHECK (user_id = current_setting('app.user_id')::uuid)
        """
    )
    # The analysis worker and the Calendar sync job may be bound to one user or run with
    # the explicit cross-tenant switch the dispatcher/reconciler already use.
    op.execute(
        f"""
        CREATE POLICY jobs_tenant ON "{table}"
          FOR ALL TO "{JOBS_ROLE}"
          USING (user_id = {SAFE_USER_SETTING})
          WITH CHECK (user_id = {SAFE_USER_SETTING})
        """
    )
    op.execute(
        f"""
        CREATE POLICY jobs_cross_tenant ON "{table}"
          FOR ALL TO "{JOBS_ROLE}"
          USING ({CROSS_TENANT})
          WITH CHECK ({CROSS_TENANT})
        """
    )
    # 0005's ALTER DEFAULT PRIVILEGES already covered tables created afterwards; the
    # explicit grant keeps this migration self-contained on a restored cluster.
    op.execute(
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{table}" '
        f'TO "{APP_ROLE}", "{JOBS_ROLE}"'
    )


def upgrade() -> None:
    _create_tasks()
    _create_task_sources()
    _create_task_revisions()
    for table in TABLES:
        _apply_tenant_policies(table)


def downgrade() -> None:
    op.drop_table("task_revisions")
    op.drop_table("task_sources")
    op.drop_table("tasks")

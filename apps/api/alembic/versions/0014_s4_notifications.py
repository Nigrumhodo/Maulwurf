"""A4.1: Gmail/digest preferences on `settings` and the M8 `notifications` table.

`settings` already exists for Calendar (J3.3's `0006`, extended by J3.7's `0007`/`0008` and
closed by A3.8's `0013`). This revision extends that same per-user row; it deliberately does
not create a second settings table. The new columns describe intent only: Gmail opt-in,
daily/weekly digest switches, the digest local time and weekday, and the reminder
thresholds. Opt-in defaults to `false`, so an upgrade can never enable mail by accident, and
nothing here grants an OAuth scope or sends a message (J4.1 owns that).

`notifications` implements the frozen DISENO §2.9 / ESPECIFICACION §M8 shape:

- persistent dedupe with two database guarantees: the canonical `dedupe_key` text is
  UNIQUE, and `(user_id, type, task_id, due_version, occurrence)` is
  `UNIQUE NULLS NOT DISTINCT`, so a duplicate occurrence cannot be inserted even if a caller
  formatted the key differently, and task-less digests/test sends still dedupe;
- the closed status vocabulary, with lease/sent/error coherence CHECKs: only `sending` owns
  a lease, `sent` proves `sent_at`, `provider_message_id` only exists on `sent`, and a
  persisted `last_error_code` is a lower-snake-case token (never a provider payload, an
  address or a message body);
- `(user_id, task_id) -> tasks` is tenant-pinned and cascades: a deleted task removes its
  reminders. `SET NULL (task_id)` would leave a reminder without its task generation and two
  deleted tasks with the same occurrence would then collide under `NULLS NOT DISTINCT`; the
  delivery audit belongs to a task that still exists;
- `notifications_due` is the partial scheduler index, and `notifications_by_task` covers the
  composite foreign key (PostgreSQL does not index the referencing side, I-S2-AN-17).

Forced RLS follows A2.17/0005 and the A3.1/0011 pattern: `maulwurf_app` is strictly
tenant-bound; `maulwurf_jobs` may be bound to one user or use the explicit
`app.cross_tenant='on'` switch (the scheduler claims due rows for every tenant). The ingest
role gets no access to delivery data.

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "maulwurf_app"
JOBS_ROLE = "maulwurf_jobs"
SAFE_USER_SETTING = "NULLIF(current_setting('app.user_id', true), '')::uuid"
CROSS_TENANT = "NULLIF(current_setting('app.cross_tenant', true), '') = 'on'"
TABLE = "notifications"
FK_NAME = "fk_notifications_user_id_task_id_tasks"
INDEX_NAME = "notifications_by_task"
DUE_INDEX_NAME = "notifications_due"

# Applied in this order on upgrade and dropped in reverse on downgrade.
SETTINGS_COLUMNS = (
    "gmail_opt_in",
    "digest_daily_enabled",
    "digest_daily_time",
    "digest_weekly_enabled",
    "digest_weekday",
    "reminder_offsets_hours",
    "reminder_allday_offsets_days",
)
SETTINGS_CHECKS = (
    "ck_settings_digest_weekday",
    "ck_settings_reminder_offsets_hours",
    "ck_settings_reminder_allday_offsets_days",
)


def _extend_settings() -> None:
    """Extend the existing per-user row; defaults keep every upgraded user opted out."""
    op.add_column(
        "settings",
        sa.Column("gmail_opt_in", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "settings",
        sa.Column(
            "digest_daily_enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "settings",
        sa.Column(
            "digest_daily_time",
            sa.Time(),
            server_default=sa.text("'07:00'"),
            nullable=False,
        ),
    )
    op.add_column(
        "settings",
        sa.Column(
            "digest_weekly_enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "settings",
        sa.Column("digest_weekday", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "settings",
        sa.Column(
            "reminder_offsets_hours",
            postgresql.JSONB(),
            server_default=sa.text("'[48, 24, 2]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "settings",
        sa.Column(
            "reminder_allday_offsets_days",
            postgresql.JSONB(),
            server_default=sa.text("'[3, 1]'::jsonb"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "digest_weekday", "settings", "digest_weekday BETWEEN 0 AND 6"
    )
    op.create_check_constraint(
        "reminder_offsets_hours",
        "settings",
        "jsonb_typeof(reminder_offsets_hours) = 'array'",
    )
    op.create_check_constraint(
        "reminder_allday_offsets_days",
        "settings",
        "jsonb_typeof(reminder_allday_offsets_days) = 'array'",
    )


def _create_notifications() -> None:
    op.create_table(
        TABLE,
        sa.Column(
            "id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("task_id", sa.UUID()),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("occurrence", sa.Date()),
        sa.Column("due_version", sa.Integer()),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dedupe_key", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("provider_message_id", sa.Text()),
        sa.Column("last_error_code", sa.Text()),
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
            "type IN ('reminder_t48', 'reminder_t24', 'reminder_t2', "
            "'reminder_t3d', 'reminder_t1d', 'digest_daily', 'digest_weekly', 'test')",
            name=op.f("ck_notifications_type"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'sending', 'sent', 'failed', "
            "'delivery_unknown', 'cancelled')",
            name=op.f("ck_notifications_status"),
        ),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_notifications_attempts")),
        sa.CheckConstraint(
            "due_version IS NULL OR due_version >= 1",
            name=op.f("ck_notifications_due_version"),
        ),
        sa.CheckConstraint(
            "(type IN ('digest_daily', 'digest_weekly', 'test') "
            "AND task_id IS NULL AND due_version IS NULL) "
            "OR (type IN ('reminder_t48', 'reminder_t24', 'reminder_t2', "
            "'reminder_t3d', 'reminder_t1d') "
            "AND task_id IS NOT NULL AND due_version IS NOT NULL)",
            name=op.f("ck_notifications_type_task"),
        ),
        sa.CheckConstraint(
            "last_error_code IS NULL OR last_error_code ~ '^[a-z][a-z0-9_]{0,63}$'",
            name=op.f("ck_notifications_last_error_code"),
        ),
        sa.CheckConstraint(
            "NOT public.maulwurf_text_is_blank(dedupe_key)",
            name=op.f("ck_notifications_dedupe_key_not_blank"),
        ),
        sa.CheckConstraint(
            "(status = 'sending' AND lease_expires_at IS NOT NULL) "
            "OR (status IN ('pending', 'cancelled') AND lease_expires_at IS NULL) "
            "OR status IN ('sent', 'failed', 'delivery_unknown')",
            name=op.f("ck_notifications_lease"),
        ),
        sa.CheckConstraint(
            "(status = 'sent') = (sent_at IS NOT NULL)",
            name=op.f("ck_notifications_sent_at"),
        ),
        sa.CheckConstraint(
            "provider_message_id IS NULL OR status = 'sent'",
            name=op.f("ck_notifications_provider_message_id"),
        ),
        sa.CheckConstraint(
            "last_error_code IS NULL OR status IN ('failed', 'delivery_unknown')",
            name=op.f("ck_notifications_error_code"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name=op.f("fk_notifications_user_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
        sa.UniqueConstraint("dedupe_key", name=op.f("uq_notifications_dedupe_key")),
        sa.UniqueConstraint(
            "user_id",
            "type",
            "task_id",
            "due_version",
            "occurrence",
            name=op.f("uq_notifications_occurrence"),
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        DUE_INDEX_NAME,
        TABLE,
        ["status", "scheduled_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(INDEX_NAME, TABLE, ["user_id", "task_id"])
    # The tenant-pinned task link is added after the table. A deleted task removes its
    # reminders instead of leaving unlinked rows that would collide under NULLS NOT DISTINCT.
    op.create_foreign_key(
        FK_NAME,
        TABLE,
        "tasks",
        ["user_id", "task_id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )


def _apply_tenant_policies() -> None:
    op.execute(f'ALTER TABLE "{TABLE}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{TABLE}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON "{TABLE}"
          FOR ALL TO "{APP_ROLE}"
          USING (user_id = current_setting('app.user_id')::uuid)
          WITH CHECK (user_id = current_setting('app.user_id')::uuid)
        """
    )
    op.execute(
        f"""
        CREATE POLICY jobs_tenant ON "{TABLE}"
          FOR ALL TO "{JOBS_ROLE}"
          USING (user_id = {SAFE_USER_SETTING})
          WITH CHECK (user_id = {SAFE_USER_SETTING})
        """
    )
    op.execute(
        f"""
        CREATE POLICY jobs_cross_tenant ON "{TABLE}"
          FOR ALL TO "{JOBS_ROLE}"
          USING ({CROSS_TENANT})
          WITH CHECK ({CROSS_TENANT})
        """
    )
    # 0005's ALTER DEFAULT PRIVILEGES covers new tables, but the explicit grant keeps this
    # revision self-contained on a restored cluster.
    op.execute(
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{TABLE}" '
        f'TO "{APP_ROLE}", "{JOBS_ROLE}"'
    )


def upgrade() -> None:
    _extend_settings()
    _create_notifications()
    _apply_tenant_policies()


def downgrade() -> None:
    op.drop_table(TABLE)
    for name in SETTINGS_CHECKS:
        op.drop_constraint(op.f(name), "settings", type_="check")
    for column in reversed(SETTINGS_COLUMNS):
        op.drop_column("settings", column)

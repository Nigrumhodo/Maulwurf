"""J3.7: Calendar settings and the `calendar_events` mirror (A3.8 frozen shape).

A3.8 is not in `develop`, so J3.7 provisions the minimum the read/publication path needs,
faithful to DESIGN §2.8–§2.9:

- `settings` keeps the writing calendar, the explicitly selected reading calendars, and the
  coverage columns J3.7 writes after an atomic publication.
- `calendar_events` keeps one row per remote event keyed by
  `(user_id, calendar_id, event_id)`, with mutually exclusive instant/date pairs and the
  per-event `last_synced_at`. `task_id` has no composite FK because `tasks` (A3.1) is not in
  `develop`; the unique partial index for the one-to-one link is already in place.

Both tables get forced RLS and the same role split as 0005: `maulwurf_app` is tenant-bound;
`maulwurf_jobs` may read/write across tenants only with the explicit `app.cross_tenant='on'`
switch (a Calendar refresh worker). A3.8 remains unclaimed: this migration does not close it.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("settings", "calendar_events")
APP_ROLE = "maulwurf_app"
JOBS_ROLE = "maulwurf_jobs"


def _create_settings() -> None:
    op.create_table(
        "settings",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("calendar_write_calendar_id", sa.Text()),
        sa.Column(
            "calendar_read_calendar_ids",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("calendar_last_synced_at", sa.DateTime(timezone=True)),
        sa.Column("calendar_coverage_from", sa.DateTime(timezone=True)),
        sa.Column("calendar_coverage_to", sa.DateTime(timezone=True)),
        sa.Column(
            "calendar_coverage_calendar_ids",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
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
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name=op.f("fk_settings_user_id_users"),
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_settings")),
    )


def _create_calendar_events() -> None:
    op.create_table(
        "calendar_events",
        sa.Column(
            "id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("calendar_id", sa.Text(), nullable=False),
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), server_default="google", nullable=False),
        sa.Column("task_id", sa.UUID()),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True)),
        sa.Column("end_at", sa.DateTime(timezone=True)),
        sa.Column("start_date", sa.Date()),
        sa.Column("end_date_exclusive", sa.Date()),
        sa.Column("timezone", sa.Text()),
        sa.Column("etag", sa.Text()),
        sa.Column("remote_status", sa.Text(), server_default="confirmed", nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False),
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
            "source IN ('google', 'maulwurf')",
            name=op.f("ck_calendar_events_source"),
        ),
        sa.CheckConstraint(
            "(start_at IS NOT NULL AND end_at IS NOT NULL "
            "AND start_date IS NULL AND end_date_exclusive IS NULL) "
            "OR (start_date IS NOT NULL AND end_date_exclusive IS NOT NULL "
            "AND start_at IS NULL AND end_at IS NULL)",
            name=op.f("ck_calendar_events_block_pair"),
        ),
        sa.CheckConstraint(
            "start_at IS NULL OR start_at < end_at",
            name=op.f("ck_calendar_events_time_range"),
        ),
        sa.CheckConstraint(
            "start_date IS NULL OR start_date < end_date_exclusive",
            name=op.f("ck_calendar_events_date_range"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name=op.f("fk_calendar_events_user_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_calendar_events")),
        sa.UniqueConstraint(
            "user_id",
            "calendar_id",
            "event_id",
            name=op.f("uq_calendar_events_user_id_calendar_id_event_id"),
        ),
    )
    op.create_index(
        "calendar_events_by_window",
        "calendar_events",
        ["user_id", "start_at"],
    )
    op.create_index(
        "calendar_events_by_window_date",
        "calendar_events",
        ["user_id", "start_date"],
    )
    # A task has at most one linked event (DESIGN §2.8). The FK to `tasks` arrives with
    # A3.1/A3.8; the uniqueness of the link does not depend on that table existing.
    op.create_index(
        "calendar_events_one_link",
        "calendar_events",
        ["task_id"],
        unique=True,
        postgresql_where=sa.text("task_id IS NOT NULL"),
    )


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
    # The jobs role is cross-tenant by design; it may only widen with the explicit switch.
    op.execute(
        f"""
        CREATE POLICY jobs_tenant ON "{table}"
          FOR ALL TO "{JOBS_ROLE}"
          USING (user_id = NULLIF(current_setting('app.user_id', true), '')::uuid)
          WITH CHECK (user_id = NULLIF(current_setting('app.user_id', true), '')::uuid)
        """
    )
    op.execute(
        f"""
        CREATE POLICY jobs_cross_tenant ON "{table}"
          FOR ALL TO "{JOBS_ROLE}"
          USING (NULLIF(current_setting('app.cross_tenant', true), '') = 'on')
          WITH CHECK (NULLIF(current_setting('app.cross_tenant', true), '') = 'on')
        """
    )
    # 0005's ALTER DEFAULT PRIVILEGES already covers tables created afterwards; the explicit
    # grant keeps this migration self-contained if it is replayed on a restored cluster.
    op.execute(
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{table}" '
        f'TO "{APP_ROLE}", "{JOBS_ROLE}"'
    )


def upgrade() -> None:
    _create_settings()
    _create_calendar_events()
    for table in TABLES:
        _apply_tenant_policies(table)


def downgrade() -> None:
    for table in TABLES:
        for policy in ("tenant_isolation", "jobs_tenant", "jobs_cross_tenant"):
            op.execute(f'DROP POLICY IF EXISTS "{policy}" ON "{table}"')
        op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
    op.drop_index("calendar_events_one_link", table_name="calendar_events")
    op.drop_index("calendar_events_by_window_date", table_name="calendar_events")
    op.drop_index("calendar_events_by_window", table_name="calendar_events")
    op.drop_table("calendar_events")
    op.drop_table("settings")

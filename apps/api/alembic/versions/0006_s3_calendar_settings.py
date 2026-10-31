"""J3.3: per-user Calendar `settings` row (DISENO §2.9, A3.8 scope).

The single app-created "Maulwurf" secondary calendar is stored here per user. A3.8, the
owner of the full Calendar settings and the `calendar_events` mirror, is not in this
revision; this migration creates only the `settings` shape that J3.3 needs and A3.8 must
extend instead of creating a second table. S4 extends the same row with the Gmail/digest
preferences.

Row-level security follows A2.17: the API role only sees the bound tenant, and the worker
role may read the target calendar after binding the owner (`app.user_id`) or through the
explicit cross-tenant switch, exactly like the other tenant tables.

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

APP_ROLE = "maulwurf_app"
JOBS_ROLE = "maulwurf_jobs"
# Tables this migration brings under RLS; later migrations must add their own entries.
TENANT_TABLES = ("settings",)
USER_SETTING = "current_setting('app.user_id')::uuid"
SAFE_USER_SETTING = "NULLIF(current_setting('app.user_id', true), '')::uuid"
CROSS_TENANT = "NULLIF(current_setting('app.cross_tenant', true), '') = 'on'"


def upgrade() -> None:
    op.create_table(
        "settings",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("calendar_write_calendar_id", sa.Text(), nullable=True),
        sa.Column(
            "calendar_read_calendar_ids",
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
            name=op.f("fk_settings_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_settings")),
    )
    op.execute('ALTER TABLE "settings" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "settings" FORCE ROW LEVEL SECURITY')
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON "settings"
          FOR ALL TO "{APP_ROLE}"
          USING (user_id = {USER_SETTING})
          WITH CHECK (user_id = {USER_SETTING})
        """
    )
    # Workers (J3.4+) read the target calendar: after binding the task owner, or with the
    # explicit cross-tenant switch the dispatcher/reconciler already use.
    op.execute(
        f"""
        CREATE POLICY jobs_tenant ON "settings"
          FOR ALL TO "{JOBS_ROLE}"
          USING (user_id = {SAFE_USER_SETTING})
          WITH CHECK (user_id = {SAFE_USER_SETTING})
        """
    )
    op.execute(
        f"""
        CREATE POLICY jobs_cross_tenant ON "settings"
          FOR ALL TO "{JOBS_ROLE}"
          USING ({CROSS_TENANT})
          WITH CHECK ({CROSS_TENANT})
        """
    )
    # 0005 set default privileges for the migration role, but grant explicitly as well so
    # this table never depends on the order in which the roles were prepared.
    for role in (APP_ROLE, JOBS_ROLE):
        op.execute(
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON "settings" TO "{role}"'
        )


def downgrade() -> None:
    op.drop_table("settings")

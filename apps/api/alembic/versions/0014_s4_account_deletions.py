"""A4.4: observable account deletion and its remote-work residual records.

`DELETE /me` creates one `account_deletions` row and returns its id. The row has **no FK to
`users`** because it must outlive the account it reports; the session that started the
deletion points at it through `sessions.deletion_operation_id` so that, once the account is
tombstoned, that session can still read the operation and log out while every other session
is revoked.

`google_remote_operations` records the explicit Google deletions (`delete_event`,
`delete_calendar`) and any cleanup failure. Its `user_id` is an opaque UUID without a FK for
the same reason: the remote ids, the authorization, the status and the redacted error remain
observable after the purge. This resolves the DISENO §2.8 discrepancy in favour of the
privacy contract (the illustrative SQL's `ON DELETE CASCADE` contradicted the prose that
says the rows survive).

Both tables force RLS. The API role is bound to the tenant by `former_user_id` / `user_id`;
the worker role may bind a tenant or use the explicit cross-tenant switch, like the other
S3/S4 tenant tables. The downgrade removes the tables and the session link; it cannot restore
a purged account.

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "maulwurf_app"
JOBS_ROLE = "maulwurf_jobs"
USER_SETTING = "current_setting('app.user_id')::uuid"
SAFE_USER_SETTING = "NULLIF(current_setting('app.user_id', true), '')::uuid"
CROSS_TENANT = "NULLIF(current_setting('app.cross_tenant', true), '') = 'on'"
SESSIONS_FK = "fk_sessions_deletion_operation_id_account_deletions"
# Tables this migration brings under RLS; the unit guard reads this tuple.
TABLES = ("account_deletions", "google_remote_operations")


def _policy(table: str, tenant_column: str) -> None:
    op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON "{table}"
          FOR ALL TO "{APP_ROLE}"
          USING ({tenant_column} = {USER_SETTING})
          WITH CHECK ({tenant_column} = {USER_SETTING})
        """
    )
    op.execute(
        f"""
        CREATE POLICY jobs_tenant ON "{table}"
          FOR ALL TO "{JOBS_ROLE}"
          USING ({tenant_column} = {SAFE_USER_SETTING})
          WITH CHECK ({tenant_column} = {SAFE_USER_SETTING})
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
    for role in (APP_ROLE, JOBS_ROLE):
        op.execute(
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{table}" TO "{role}"'
        )


def upgrade() -> None:
    op.create_table(
        "account_deletions",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("former_user_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column(
            "delete_events", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','completed','completed_with_remote_failures')",
            name="ck_account_deletions_status",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_account_deletions"),
        sa.UniqueConstraint(
            "former_user_id", name="uq_account_deletions_former_user_id"
        ),
    )
    op.create_index("account_deletions_expiry", "account_deletions", ["expires_at"])

    op.add_column(
        "sessions",
        sa.Column("deletion_operation_id", sa.UUID(), nullable=True),
    )
    op.create_index("sessions_by_deletion", "sessions", ["deletion_operation_id"])
    op.create_foreign_key(
        SESSIONS_FK,
        "sessions",
        "account_deletions",
        ["deletion_operation_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "google_remote_operations",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("calendar_id", sa.Text(), nullable=True),
        sa.Column("event_id", sa.Text(), nullable=True),
        sa.Column(
            "authorized", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('delete_event','delete_calendar','account_cleanup')",
            name="ck_google_remote_operations_kind",
        ),
        sa.CheckConstraint(
            "status IN ('pending','running','succeeded','failed')",
            name="ck_google_remote_operations_status",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_google_remote_operations"),
        sa.UniqueConstraint(
            "user_id",
            "kind",
            "calendar_id",
            "event_id",
            name="uq_google_remote_operations_identity",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        "google_remote_operations_by_user",
        "google_remote_operations",
        ["user_id", "status"],
    )

    _policy("account_deletions", "former_user_id")
    _policy("google_remote_operations", "user_id")


def downgrade() -> None:
    op.drop_index(
        "google_remote_operations_by_user", table_name="google_remote_operations"
    )
    op.drop_table("google_remote_operations")
    op.drop_constraint(SESSIONS_FK, "sessions", type_="foreignkey")
    op.drop_index("sessions_by_deletion", table_name="sessions")
    op.drop_column("sessions", "deletion_operation_id")
    op.drop_index("account_deletions_expiry", table_name="account_deletions")
    op.drop_table("account_deletions")

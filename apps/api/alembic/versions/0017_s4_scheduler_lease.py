"""J4.2: single active scheduler role elected by a database lease.

The scheduler computes due occurrences every minute. More than one scheduler process may be
running (a rolling deploy, a retry after a crash), so the M8 contract's "single active role"
cannot rely on the Compose replica count. This revision adds the global
`scheduler_leases` row that holds the election:

- `name` is the role being elected (`notification_scheduler` today). It is the primary key,
  so the row exists at most once per role;
- `holder` identifies the process, `fencing_token` grows whenever a different holder takes
  over, and `lease_expires_at` bounds the election. A live lease can only be renewed by its
  own holder; only an expired one can be taken over;
- `heartbeat_at` records the last renewal and the CHECK rejects a lease that ends before it.

The table has no tenant and no row-level security: it stores infrastructure state, never a
user's data. `0005`'s default privileges would grant it to the API role too, so the upgrade
revokes the API role and keeps the worker/scheduler role (`maulwurf_jobs`) only. The ingest
role never sees it.

Downgrade drops the table; a scheduler restarts without a lease and elects itself again.

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "scheduler_leases"
APP_ROLE = "maulwurf_app"
JOBS_ROLE = "maulwurf_jobs"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("holder", sa.Text(), nullable=False),
        sa.Column(
            "fencing_token",
            sa.BigInteger(),
            server_default="1",
            nullable=False,
        ),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "heartbeat_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
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
        sa.CheckConstraint(
            "NOT public.maulwurf_text_is_blank(name)",
            name=op.f("ck_scheduler_leases_name_not_blank"),
        ),
        sa.CheckConstraint(
            "NOT public.maulwurf_text_is_blank(holder)",
            name=op.f("ck_scheduler_leases_holder_not_blank"),
        ),
        sa.CheckConstraint(
            "fencing_token >= 1",
            name=op.f("ck_scheduler_leases_fencing_token"),
        ),
        sa.CheckConstraint(
            "lease_expires_at > heartbeat_at",
            name=op.f("ck_scheduler_leases_lease_window"),
        ),
        sa.PrimaryKeyConstraint("name", name=op.f("pk_scheduler_leases")),
    )
    # `0005` granted new tables to the API and jobs roles by default. Scheduling is a
    # worker/scheduler responsibility: the API must not read or rewrite the election.
    op.execute(f'REVOKE ALL PRIVILEGES ON TABLE "{TABLE}" FROM "{APP_ROLE}"')
    op.execute(
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "{TABLE}" TO "{JOBS_ROLE}"'
    )


def downgrade() -> None:
    op.drop_table(TABLE)

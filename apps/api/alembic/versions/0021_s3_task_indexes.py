"""A3.3: one pending revision per task and a keyset index for the inbox.

The review inbox must never keep two `pending_confirmation` proposals for the same task:
the first PATCH of a confirmed task retires the previous proposal before inserting its
replacement, and a partial unique index enforces the invariant under concurrency. The
migration retires pre-existing duplicates first, keeping the newest proposal, so an
environment that exercised the old behavior upgrades instead of failing. The cleanup
temporarily disables forced RLS because the migration runs as the table owner.

`tasks_by_user_created` covers the `(created_at DESC, id DESC)` keyset page for one user.

The downgrade removes both guards; it cannot restore revision statuses discarded by the
upgrade, because the older state was the bug.

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-08
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _retire_duplicate_pending_revisions() -> None:
    op.execute('ALTER TABLE "task_revisions" NO FORCE ROW LEVEL SECURITY')
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY user_id, task_id
                       ORDER BY created_at DESC, id DESC
                   ) AS position
            FROM task_revisions
            WHERE status = 'pending_confirmation'
        )
        UPDATE task_revisions AS revision
        SET status = 'discarded'
        FROM ranked
        WHERE revision.id = ranked.id
          AND ranked.position > 1
        """
    )
    op.execute('ALTER TABLE "task_revisions" FORCE ROW LEVEL SECURITY')


def upgrade() -> None:
    _retire_duplicate_pending_revisions()
    op.create_index(
        "task_revisions_one_pending",
        "task_revisions",
        ["user_id", "task_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending_confirmation'"),
    )
    op.create_index(
        "tasks_by_user_created",
        "tasks",
        ["user_id", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("tasks_by_user_created", table_name="tasks")
    op.drop_index("task_revisions_one_pending", table_name="task_revisions")

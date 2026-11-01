"""A4.2: audit sequence for manual notification repetitions.

A4.1 froze the natural identity `(user_id, type, task_id, due_version, occurrence)` and
made it `UNIQUE NULLS NOT DISTINCT`. A manual test send or a warned resend is a **new
audited occurrence** of the same reminder, so it cannot reuse that exact tuple: inserting it
would violate the constraint, and mutating the original `delivery_unknown` row is forbidden
by the M8 contract.

This revision adds `occurrence_seq`:

- `0` is the scheduled/automatic occurrence, so the scheduler's dedupe is unchanged.
- A manual repetition takes the next positive number inside the same frozen identity
  (`max + 1` under a per-identity advisory lock in `services.notification_center`).
- Both the natural unique key and the canonical `dedupe_key` include the sequence, so two
  manual rows can never collide while the automatic one still dedupes by construction.

No table is recreated, no policy changes and no row is rewritten: existing rows default to
`0`, which is exactly the A4.1 meaning they already had. Downgrade is only safe while no
manual repetition exists: the old five-column key cannot represent two rows of the same
occurrence, so a populated `occurrence_seq > 0` fails loudly instead of deleting audit data.

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "notifications"
UNIQUE_CONSTRAINT = "uq_notifications_occurrence"
CHECK = "occurrence_seq"
COLUMNS_BEFORE = ("user_id", "type", "task_id", "due_version", "occurrence")
COLUMNS_AFTER = (*COLUMNS_BEFORE, "occurrence_seq")


def _create_occurrence_key(columns: Sequence[str]) -> None:
    rendered = ", ".join(columns)
    op.execute(
        f'ALTER TABLE "{TABLE}" ADD CONSTRAINT "{UNIQUE_CONSTRAINT}" '
        f"UNIQUE NULLS NOT DISTINCT ({rendered})"
    )


def upgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column("occurrence_seq", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_check_constraint(CHECK, TABLE, "occurrence_seq >= 0")
    op.execute(f'ALTER TABLE "{TABLE}" DROP CONSTRAINT "{UNIQUE_CONSTRAINT}"')
    _create_occurrence_key(COLUMNS_AFTER)


def downgrade() -> None:
    op.execute(f'ALTER TABLE "{TABLE}" DROP CONSTRAINT "{UNIQUE_CONSTRAINT}"')
    _create_occurrence_key(COLUMNS_BEFORE)
    op.drop_constraint(CHECK, TABLE, type_="check")
    op.drop_column(TABLE, "occurrence_seq")

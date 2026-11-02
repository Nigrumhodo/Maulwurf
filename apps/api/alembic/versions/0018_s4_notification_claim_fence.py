"""J4.2: persist the scheduler fence on a claimed notification occurrence.

`0017` elects one scheduler role and grows `scheduler_leases.fencing_token` on every
takeover, but the token stayed in the election row: a `notifications` row moved to `sending`
could not prove which election claimed it, so a stale scheduler or a slow sender could not
be distinguished from the current role before calling Gmail.

This revision adds `notifications.claimed_by_fence`:

- the scheduler writes the fence of the election that performed the claim, in the same
  atomic statement that moves the row `pending -> sending`;
- a renewal by the same holder keeps the fence, so normal minute ticks stay stable;
- a terminal row keeps the fence of the attempt that ended it, exactly like
  `lease_expires_at`, so the decision remains explainable;
- the column is nullable because `pending`, `cancelled` and rows created before this
  revision have no claim. The CHECK rejects a non-positive fence instead of inventing a
  sentinel.

Contract for J4.1 (Gmail): before delivering a claimed occurrence, the sender must compare
`claimed_by_fence` with the live `scheduler_leases.fencing_token` (or the fence carried by
the queued work item). A mismatch means the claim belongs to a superseded election and must
not be sent under that authority.

No table is created and no policy or privilege changes: the column inherits the
`notifications` grants and forced RLS from `0014`; the ingest role still has no access.
Downgrade drops the CHECK and the column.

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "notifications"
COLUMN = "claimed_by_fence"
CHECK = "claimed_by_fence"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column(COLUMN, sa.BigInteger(), nullable=True))
    op.create_check_constraint(
        CHECK,
        TABLE,
        f"{COLUMN} IS NULL OR {COLUMN} >= 1",
    )


def downgrade() -> None:
    op.drop_constraint(CHECK, TABLE, type_="check")
    op.drop_column(TABLE, COLUMN)

"""A2.20: fencing token for the API `processing_runs` ledger.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-30

`claim_run`, `finish_run` and the reconciler used to address a run by `id` only, so a worker
that stayed silent past `LEASE` could overwrite the terminal state the reconciler had already
written. The token is monotonic per run: every successful claim takes the next value, and
completion/activation are compare-and-set on `(id, fencing_token, status='running')`.

Existing rows start at `0`; the next claim moves them to `1`, exactly like a new row. The
server default stays so `pending` rows created by the retry path (which are not claims yet)
do not need to know about the ledger.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "processing_runs",
        sa.Column(
            "fencing_token",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("processing_runs", "fencing_token")

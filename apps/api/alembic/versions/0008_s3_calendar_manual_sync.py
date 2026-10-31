"""J3.8: per-user manual Calendar refresh cooldown (bounded Google quota).

`POST /calendar/sync` re-reads the complete -7/+60 window of every selected calendar on
each call. Without a floor, a double click or a script with a valid session can spend the
project's Calendar API quota; guardrail 10 requires an explicit limit for Google
integrations.

`settings` gains `calendar_last_manual_sync_at`: the start of the last accepted manual
refresh. It is the rate-limit anchor only. `calendar_last_synced_at` keeps its J3.7 meaning
(the last complete publication) so a failed attempt still counts against the cooldown
without ever being reported as fresh coverage.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "settings",
        sa.Column("calendar_last_manual_sync_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_column("settings", "calendar_last_manual_sync_at")

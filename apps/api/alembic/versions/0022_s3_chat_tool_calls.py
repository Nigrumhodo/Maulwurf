"""D3.8: persist the chat tool provenance on the assistant message.

The chat may call the read-only tools `get_calendar_events`, `get_pending_tasks` and
`propose_task`. The UI must still distinguish "from your calendar/tasks" and show a
proposal after a history reload, so the assistant row keeps a small JSONB list with the
tool name, its success flag and the non-sensitive payload (the proposal payload includes
the task id and `needs_review` status; it never includes transcript text, audio, tokens or
provider payloads).

`message_sources` keeps its transcript-only meaning; the tool list is per message metadata
and does not change the deletion/provenance queries.

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column(
            "tool_calls",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("messages", "tool_calls")

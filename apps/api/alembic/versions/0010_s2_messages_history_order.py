"""A2.12: supporting index for conversation history and listing reads.

`GET /chat/conversations/{id}/messages` pages by
`(created_at DESC, role rank DESC, id DESC)` and `GET /chat/conversations` aggregates
`max(messages.created_at)` per conversation. The existing `messages_by_conversation` index
only covered `(user_id, conversation_id)`, so PostgreSQL had to sort or scan the heap for both
reads. This revision replaces it with `(user_id, conversation_id, created_at, id)`: the
leading prefix still covers the composite FK, and the trailing keys serve the history order
and the per-conversation maximum. The index is declared ascending because the history query
sorts every key descending; PostgreSQL reads the same index backwards.

The role tie-break remains a query-level `CASE` (the reviewer's requested behaviour, used for
legacy rows whose user and assistant messages share one transaction timestamp). It is not an
indexed expression: new turns write an explicit one-microsecond offset, and equal-timestamp
rows are rare within one conversation, so the sort stays bounded to the small tie group.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "messages_by_conversation_order"
PREVIOUS_INDEX_NAME = "messages_by_conversation"


def upgrade() -> None:
    # Create the replacement before dropping the old index: readers never lose their FK
    # lookup path while the migration runs.
    op.create_index(
        INDEX_NAME,
        "messages",
        ["user_id", "conversation_id", "created_at", "id"],
    )
    op.drop_index(PREVIOUS_INDEX_NAME, table_name="messages")


def downgrade() -> None:
    op.create_index(
        PREVIOUS_INDEX_NAME,
        "messages",
        ["user_id", "conversation_id"],
    )
    op.drop_index(INDEX_NAME, table_name="messages")

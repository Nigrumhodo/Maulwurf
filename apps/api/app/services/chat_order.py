"""Deterministic ordering for persisted chat messages (A2.12, M7).

A user message and its assistant row are inserted in one transaction, so both can carry the
same `created_at` (the transaction timestamp). UUIDs are random, so ordering by
`(created_at, id)` alone cannot guarantee that the question is returned before its answer.
The role rank is the tie-breaker in the paginated history, its keyset cursor and the RAG
history query; `RAGService.prepare` additionally writes strictly increasing timestamps for
new turns, so only legacy same-timestamp rows depend on the rank.
"""

from datetime import timedelta

from sqlalchemy import case
from sqlalchemy.sql.elements import ColumnElement

from app.models import Message

ROLE_ORDER = ("user", "system", "assistant")
ROLE_RANK = {role: rank for rank, role in enumerate(ROLE_ORDER)}
# A turn's assistant row is one microsecond newer than its question. PostgreSQL keeps
# microsecond precision, so the pair is totally ordered without a schema change.
ASSISTANT_CREATED_AT_OFFSET = timedelta(microseconds=1)


def role_rank(role: str) -> int:
    """Rank used by the cursor; unknown roles sort last and are rejected by `decode_cursor`."""
    return ROLE_RANK.get(role, len(ROLE_ORDER))


def message_role_rank() -> ColumnElement[int]:
    """SQL expression matching `role_rank` for the `messages.role` column."""
    return case(
        *((Message.role == role, rank) for role, rank in ROLE_RANK.items()),
        else_=len(ROLE_ORDER),
    )

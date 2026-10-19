"""L2.3: backfill chunks.tsv for rows written before the lexical vector was materialized.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-18

A2.1 created `chunks.tsv` and its GIN index (`chunks_fts`) but nothing populated the column;
L2.3 makes `indexing._persist_draft` write it with the document's `transcripts.fts_config`.
This data backfill covers the chunks inserted before that change so the lexical search branch
does not silently skip them. It is idempotent (`WHERE c.tsv IS NULL`) and re-runnable.

Downgrade is intentionally a no-op: the migration changes data, not schema, and nulling `tsv`
again would break the lexical branch. Recomputing it is cheap and deterministic.

Single statement, not batched: S2 carries no real corpus (D3-Texto is open), the predicate is
`tsv IS NULL` so a re-run after a partial failure only touches the remaining rows, and Alembic
runs the whole revision in one transaction anyway. If the table ever grows large enough to make
one `UPDATE` a locking problem, split it into a batched, chunk-keyed loop (keyset on `chunks.id`)
in a dedicated ops migration rather than tuning this one.

Note on numbering: this takes the `0003` slot. A2.18 (unclaimed) expected `0003_*.py` for the
schema-integrity migration; it now needs `0004`.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BACKFILL_TSV = """
UPDATE chunks AS c
SET tsv = to_tsvector(t.fts_config::regconfig, c.content)
FROM transcripts AS t
WHERE t.id = c.transcript_id
  AND t.user_id = c.user_id
  AND c.tsv IS NULL
"""


def upgrade() -> None:
    op.execute(_BACKFILL_TSV)


def downgrade() -> None:
    pass

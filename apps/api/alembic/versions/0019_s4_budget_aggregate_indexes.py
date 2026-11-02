"""J4.8: supporting indexes for the cross-tenant monthly budget aggregates.

`/internal/metrics` reads the provider budgets in one transaction as the jobs role, so the
queries cannot use the tenant-first indexes that start with `user_id`. Every budget read
filters a UTC month range and each one would otherwise scan the whole heap on every scrape:

- `messages`: input + output tokens by `created_at`;
- `transcripts`: the committed classes of the month by `created_at`, paired with the audio
  id so the aggregate can count each class once;
- `notifications`: sent deliveries by `status` + `sent_at`;
- `outbox_events`: `sync_task_event` writes by `type` + `created_at`;
- `google_remote_operations`: recorded operations by `created_at`.

The revision is additive: it creates five secondary indexes and touches no column, policy,
grant or row. It does not introduce a price, quota or capacity number.

The builds use `CREATE INDEX CONCURRENTLY` inside an autocommit block: a plain
`CREATE INDEX` runs inside the migration transaction, holds a SHARE lock on the table for
the whole build and blocks chat, ingest and outbox writes on a live deployment.
CONCURRENTLY takes only SHARE UPDATE EXCLUSIVE. The trade-off is that it cannot run inside
a transaction, so a failure between indexes commits the ones already built and leaves the
version at `0018` (an interrupted build can also leave an invalid index behind). Each
index is therefore dropped first with `DROP INDEX CONCURRENTLY IF EXISTS`: a retry
converges instead of failing with "already exists", at the cost of rebuilding the indexes
that a previous attempt had finished. Downgrade drops the indexes the same way.

The session-scoped advisory lock in `alembic/env.py` outlives the autocommit block on
purpose: a transaction-scoped lock would be released by this commit and a second replica
could start the same migration.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MESSAGES_INDEX = "messages_by_created_at"
TRANSCRIPTS_INDEX = "transcripts_by_created_at"
NOTIFICATIONS_INDEX = "notifications_by_status_sent_at"
OUTBOX_INDEX = "outbox_events_by_type_created_at"
GOOGLE_INDEX = "google_remote_operations_by_created_at"

INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (MESSAGES_INDEX, "messages", ("created_at",)),
    (TRANSCRIPTS_INDEX, "transcripts", ("created_at", "audio_id")),
    (NOTIFICATIONS_INDEX, "notifications", ("status", "sent_at")),
    (OUTBOX_INDEX, "outbox_events", ("type", "created_at")),
    (GOOGLE_INDEX, "google_remote_operations", ("created_at",)),
)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for name, table, columns in INDEXES:
            op.drop_index(
                name,
                table_name=table,
                postgresql_concurrently=True,
                if_exists=True,
            )
            op.create_index(name, table, list(columns), postgresql_concurrently=True)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name, table, _columns in reversed(INDEXES):
            op.drop_index(
                name,
                table_name=table,
                postgresql_concurrently=True,
                if_exists=True,
            )

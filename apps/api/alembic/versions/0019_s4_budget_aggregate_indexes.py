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
grant or row. It does not introduce a price, quota or capacity number. Downgrade drops the
indexes by name.
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


def upgrade() -> None:
    op.create_index(MESSAGES_INDEX, "messages", ["created_at"])
    op.create_index(TRANSCRIPTS_INDEX, "transcripts", ["created_at", "audio_id"])
    op.create_index(NOTIFICATIONS_INDEX, "notifications", ["status", "sent_at"])
    op.create_index(OUTBOX_INDEX, "outbox_events", ["type", "created_at"])
    op.create_index(GOOGLE_INDEX, "google_remote_operations", ["created_at"])


def downgrade() -> None:
    op.drop_index(GOOGLE_INDEX, table_name="google_remote_operations")
    op.drop_index(OUTBOX_INDEX, table_name="outbox_events")
    op.drop_index(NOTIFICATIONS_INDEX, table_name="notifications")
    op.drop_index(TRANSCRIPTS_INDEX, table_name="transcripts")
    op.drop_index(MESSAGES_INDEX, table_name="messages")

"""Account deletion operation and the remote work it leaves behind (A4.4).

`account_deletions` is the residual record `GET /me/deletion/{id}` reads after the profile and
every tenant row are gone. It deliberately has **no FK to `users`**: the row must survive the
purge it reports. It stores only the former opaque user id, the outcome, the explicit
authorization and the redacted error, never a profile, text or token.

`google_remote_operations` records the authorized Google work. Its `user_id` is also an
opaque UUID without a FK for the same reason: the remote IDs, authorization and redacted
error must remain observable after the account purge. It resolves the DISENO §2.8
discrepancy (the prose says the rows survive the purge while the illustrative SQL declared
`ON DELETE CASCADE`) in favour of the privacy contract.

Both tables are tenant-scoped with forced RLS for the API and worker roles. The tenant key
is `former_user_id` / `user_id`; see migration `0014`.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, uuid_pk

# `running` is the atomic claim between two executions; `claimed_at` bounds that claim so a
# crashed process can be resumed instead of leaving the operation stuck.
DELETION_STATUSES = ("pending", "running", "completed", "completed_with_remote_failures")
REMOTE_KINDS = ("delete_event", "delete_calendar", "account_cleanup")
REMOTE_STATUSES = ("pending", "running", "succeeded", "failed")


def _in(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"


class AccountDeletion(Base):
    __tablename__ = "account_deletions"
    __table_args__ = (
        UniqueConstraint("former_user_id", name="uq_account_deletions_former_user_id"),
        CheckConstraint(_in("status", DELETION_STATUSES), name="status"),
        Index("account_deletions_expiry", "expires_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    # No FK: this row outlives the account it describes.
    former_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    delete_events: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    # Redacted codes only; never a provider payload, email, title or transcript.
    error_summary: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = created_at()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Execution claim: one caller runs the remote step and the purge; a stale claim is
    # reclaimable after the lease expires so a crash does not strand the operation.
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")


class GoogleRemoteOperation(Base):
    __tablename__ = "google_remote_operations"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "kind",
            "calendar_id",
            "event_id",
            name="uq_google_remote_operations_identity",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(_in("kind", REMOTE_KINDS), name="kind"),
        CheckConstraint(_in("status", REMOTE_STATUSES), name="status"),
        Index("google_remote_operations_by_user", "user_id", "status"),
        # The identifiers are purged after the same residual window as `account_deletions`.
        Index("google_remote_operations_expiry", "expires_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    # No FK: unlike the credential, this residual record survives the purge.
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    calendar_id: Mapped[str | None] = mapped_column(Text)
    event_id: Mapped[str | None] = mapped_column(Text)
    authorized: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error_code: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # A remote id must not outlive the residual window: the retention job deletes the row.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

"""M8 notification occurrences: statuses, delivery lease and persistent dedupe (A4.1).

One row is one **audited occurrence** (a reminder threshold, a digest or a test send), never
a mutable "last notification" per task. The frozen shape is DISENO §2.9 / ESPECIFICACION §M8:

- `status` is a closed vocabulary and has no implicit retries. An expired `sending` lease or
  a lost provider response ends `delivery_unknown`, which is terminal: Gmail offers no
  exactly-once delivery, so the row is never pushed back to `pending` and the system never
  retries it automatically. A manual resend is a new audited row.
- `lease_expires_at` belongs to `sending`; `pending` and `cancelled` can never carry one.
  Terminal rows keep the lease of the attempt that ended them so the uncertainty stays
  explainable.
- `sent_at` exists exactly for `sent`; `provider_message_id` is only meaningful there. A
  `failed` or `delivery_unknown` row carries a **redacted error code**, constrained to a
  lower-snake-case token so a provider payload, an address or a message body can never be
  persisted by accident (`services.notifications` maps an unknown value to `unknown`).
- Dedupe is persistent and lives in the database. `dedupe_key` is the canonical rendering of
  `(user_id, type, task_id, due_version, occurrence)` and has its own UNIQUE constraint; the
  natural tuple keeps the frozen `UNIQUE NULLS NOT DISTINCT` rule, so a repeated occurrence
  cannot be inserted even if a caller formatted the text differently. Digests and test sends
  have no task, which is why the tuple uses NULLS NOT DISTINCT rather than the default
  null-distinct semantics.
- `(user_id, task_id) -> tasks` is tenant-pinned and cascades. A deleted task removes its
  reminders: keeping them would either null the NOT NULL tenant/occurrence identity (the
  frozen `SET NULL` sketch does not survive a composite FK) or make two deleted tasks collide
  under `UNIQUE NULLS NOT DISTINCT`. The delivery audit belongs to a task that still exists;
  account deletion already cascades from `users`.
- `notifications_due` serves the scheduler's due claim; `notifications_by_task` is the
  non-partial covering index the composite FK needs (I-S2-AN-17).

The table carries forced row-level security for the API and worker roles, exactly like the
S3 task tables: the API is tenant-bound and the scheduler may be bound to one user or use
the explicit `app.cross_tenant` switch. The ingest role never reads delivery data.
"""

import uuid
from datetime import date, datetime
from typing import Final

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantOwned, created_at, updated_at, uuid_pk

# Closed vocabularies of DISENO §2.9 / M8. They are the single source for the CHECKs and
# for `services.notifications`; tests fail if the model and the migration drift apart.
NOTIFICATION_TYPES: Final = (
    "reminder_t48",
    "reminder_t24",
    "reminder_t2",
    "reminder_t3d",
    "reminder_t1d",
    "digest_daily",
    "digest_weekly",
    "test",
)
REMINDER_TYPES: Final = NOTIFICATION_TYPES[:5]
DIGEST_TYPES: Final = ("digest_daily", "digest_weekly")
NOTIFICATION_STATUSES: Final = (
    "pending",
    "sending",
    "sent",
    "failed",
    "delivery_unknown",
    "cancelled",
)
TERMINAL_NOTIFICATION_STATUSES: Final = ("sent", "failed", "delivery_unknown", "cancelled")

# A persisted error is a token, never free text: lower snake case, at most 64 characters.
# This is the database-level redaction guarantee for `last_error_code`.
ERROR_CODE_PATTERN: Final = r"^[a-z][a-z0-9_]{0,63}$"


def _in(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"


class Notification(TenantOwned, Base):
    """One audited delivery occurrence of the M8 reminder/digest pipeline."""

    __tablename__ = "notifications"
    __table_args__ = (
        # The tenant-pinned task link. A task purge removes its reminders instead of leaving
        # unlinked rows (see the module docstring).
        ForeignKeyConstraint(
            ["user_id", "task_id"],
            ["tasks.user_id", "tasks.id"],
            ondelete="CASCADE",
            name="fk_notifications_user_id_task_id_tasks",
        ),
        # The canonical identity as text, plus the frozen natural key. Both live in the
        # database: `dedupe_key` uniqueness is the ticket contract, and the tuple keeps the
        # NULLS NOT DISTINCT semantics that also dedupe task-less digests and test sends.
        UniqueConstraint("dedupe_key", name="uq_notifications_dedupe_key"),
        UniqueConstraint(
            "user_id",
            "type",
            "task_id",
            "due_version",
            "occurrence",
            name="uq_notifications_occurrence",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(_in("type", NOTIFICATION_TYPES), name="type"),
        CheckConstraint(_in("status", NOTIFICATION_STATUSES), name="status"),
        CheckConstraint("attempts >= 0", name="attempts"),
        CheckConstraint("due_version IS NULL OR due_version >= 1", name="due_version"),
        # Digests/test sends never belong to a task; a reminder always carries its task and
        # its due generation. The task FK cascades, so no unlinked reminder can exist.
        CheckConstraint(
            "(type IN ('digest_daily', 'digest_weekly', 'test') "
            "AND task_id IS NULL AND due_version IS NULL) "
            "OR (type IN ('reminder_t48', 'reminder_t24', 'reminder_t2', "
            "'reminder_t3d', 'reminder_t1d') "
            "AND task_id IS NOT NULL AND due_version IS NOT NULL)",
            name="type_task",
        ),
        CheckConstraint(
            f"last_error_code IS NULL OR last_error_code ~ '{ERROR_CODE_PATTERN}'",
            name="last_error_code",
        ),
        CheckConstraint(
            "NOT public.maulwurf_text_is_blank(dedupe_key)",
            name="dedupe_key_not_blank",
        ),
        # Only a live send owns a lease. An expired `sending` row is rewritten to
        # `delivery_unknown`; terminal rows may keep the lease that ended them.
        CheckConstraint(
            "(status = 'sending' AND lease_expires_at IS NOT NULL) "
            "OR (status IN ('pending', 'cancelled') AND lease_expires_at IS NULL) "
            "OR status IN ('sent', 'failed', 'delivery_unknown')",
            name="lease",
        ),
        # `sent` must prove when and through which provider message; no other state may
        # claim a delivery.
        CheckConstraint(
            "(status = 'sent') = (sent_at IS NOT NULL)", name="sent_at"
        ),
        CheckConstraint(
            "provider_message_id IS NULL OR status = 'sent'", name="provider_message_id"
        ),
        CheckConstraint(
            "last_error_code IS NULL OR status IN ('failed', 'delivery_unknown')",
            name="error_code",
        ),
        # Scheduler claim: due rows only, ordered by `scheduled_at`.
        Index(
            "notifications_due",
            "status",
            "scheduled_at",
            postgresql_where=text("status = 'pending'"),
        ),
        # Covering index for the composite FKs (users through the `user_id` prefix, tasks
        # through the full pair); PostgreSQL does not index the referencing side.
        Index("notifications_by_task", "user_id", "task_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    # Nullable: a digest or a test send is not tied to a task.
    task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    type: Mapped[str] = mapped_column(Text, nullable=False)
    # Target local date of the occurrence. It is part of the persistent dedupe identity and
    # the base for the digest's "one per user and local date" rule.
    occurrence: Mapped[date | None] = mapped_column(Date)
    # The task's reminder generation at scheduling time; a rescheduled/edited task bumps it
    # so obsolete occurrences are cancelled instead of being delivered (A4.3).
    due_version: Mapped[int | None] = mapped_column(Integer)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Canonical (user_id, type, task_id, due_version, occurrence) rendering.
    dedupe_key: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_message_id: Mapped[str | None] = mapped_column(Text)
    last_error_code: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()

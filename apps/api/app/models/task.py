"""M4 task proposals, their corpus sources and their pending revisions (A3.1).

`tasks` is the active row the user sees: the task lifecycle (`status`) and the Calendar
delivery (`calendar_sync_status`) are deliberately separate columns, so a task can be
confirmed while the Calendar write is `blocked` without lying about either. `scheduled` is
never a task status; rescheduling lives in the Calendar block
(`event_start_at`/`event_end_at` or the exclusive all-day pair), which may stay empty until
confirmation. `version` is the optimistic-locking token for PATCH/confirm and `due_version`
is the reminder generation.

The database enforces the parts the service layer must not be able to break:

- `date_status=resolved` carries exactly one of `due_date` (all-day) or `due_at` (instant);
  `ambiguous`/`missing` carry neither. No invented midnight is possible.
- `confidence_score`, when present, stays in [0, 1] (an uncalibrated signal, never a
  permission to auto-schedule).
- The Calendar block is either empty, an instant pair, or an all-day exclusive pair, with
  the end strictly after the start.
- Status vocabularies are closed sets (no `scheduled`) and revisions keep
  `proposed_fields` plus a `pending_confirmation` state instead of mutating the task.

`task_sources` stores the corpus provenance (class/transcript/segment plus Unicode span); a
manual task has zero rows. The literal evidence with its verbatim quote (`task_evidence`) is
A3.2's table: this revision creates only the shape A3.1 owns.

`task_sources` and `task_revisions` declare `user_id` directly instead of using
`TenantOwned`: the source table's primary key is composite and has no `id` for the generic
tenant repository, and every revision is reached through its task. Both still carry the
composite tenant FK, so a row can never point at another user's task.
"""

import uuid
from datetime import date, datetime
from typing import Any, Final

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Double,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantOwned, created_at, updated_at, uuid_pk

# Closed vocabularies of DISENO §2.8 / M4. They are the source of truth for the CHECKs and
# for the services that validate requests; `scheduled` is intentionally absent.
TASK_STATUSES: Final = ("pending", "needs_review", "confirmed", "dismissed", "completed")
CALENDAR_SYNC_STATUSES: Final = (
    "not_requested",
    "pending",
    "synced",
    "failed",
    "blocked",
)
DATE_STATUSES: Final = ("resolved", "ambiguous", "missing")
TASK_ORIGINS: Final = ("extraction", "chat", "manual")
ACTIVITY_TYPES: Final = (
    "examen",
    "tarea",
    "entrega",
    "lectura",
    "recordatorio",
    "recomendacion",
    "proyecto",
)
REVISION_TYPES: Final = ("reanalysis", "edit_of_confirmed")
REVISION_STATUSES: Final = ("pending_confirmation", "activated", "discarded")


def _in(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"


def _date_contract() -> str:
    """A resolved date is exactly one coherent pair; unresolved states carry none."""
    return (
        "(date_status = 'resolved' AND all_day "
        "AND due_date IS NOT NULL AND due_at IS NULL) "
        "OR (date_status = 'resolved' AND NOT all_day "
        "AND due_at IS NOT NULL AND due_date IS NULL) "
        "OR (date_status IN ('ambiguous', 'missing') "
        "AND due_date IS NULL AND due_at IS NULL)"
    )


def _calendar_block() -> str:
    """Empty, an instant pair, or an all-day pair; the end is always later."""
    return (
        "(event_start_at IS NULL AND event_end_at IS NULL "
        "AND event_start_date IS NULL AND event_end_date_exclusive IS NULL) "
        "OR (event_start_at IS NOT NULL AND event_end_at IS NOT NULL "
        "AND event_start_date IS NULL AND event_end_date_exclusive IS NULL "
        "AND event_start_at < event_end_at) "
        "OR (event_start_at IS NULL AND event_end_at IS NULL "
        "AND event_start_date IS NOT NULL AND event_end_date_exclusive IS NOT NULL "
        "AND event_start_date < event_end_date_exclusive)"
    )


class Task(TenantOwned, Base):
    __tablename__ = "tasks"
    __table_args__ = (
        # Target of the tenant-aware FKs from sources, revisions and the Calendar mirror.
        UniqueConstraint("user_id", "id"),
        ForeignKeyConstraint(["user_id", "audio_id"], ["audios.user_id", "audios.id"]),
        ForeignKeyConstraint(["user_id", "subject_id"], ["subjects.user_id", "subjects.id"]),
        CheckConstraint(_in("source", TASK_ORIGINS), name="source"),
        CheckConstraint(_in("tipo", ACTIVITY_TYPES), name="tipo"),
        CheckConstraint(_in("date_status", DATE_STATUSES), name="date_status"),
        CheckConstraint(_in("status", TASK_STATUSES), name="status"),
        CheckConstraint(
            _in("calendar_sync_status", CALENDAR_SYNC_STATUSES), name="calendar_sync_status"
        ),
        CheckConstraint(
            "confidence_score >= 0 AND confidence_score <= 1", name="confidence_range"
        ),
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint("due_version >= 1", name="due_version"),
        CheckConstraint(_date_contract(), name="date_contract"),
        CheckConstraint(_calendar_block(), name="calendar_block"),
        # Supporting indexes for the two composite FKs (PostgreSQL does not index the
        # referencing side). The inbox indexes are partial and cannot serve them.
        Index("tasks_by_audio", "user_id", "audio_id"),
        Index("tasks_by_subject", "user_id", "subject_id"),
        # One index per due representation: `date` and `timestamptz` are never mixed in an
        # expression, whose meaning would depend on the session timezone.
        Index(
            "tasks_inbox_due_at",
            "user_id",
            "status",
            "due_at",
            postgresql_where=text("due_at IS NOT NULL"),
        ),
        Index(
            "tasks_inbox_due_date",
            "user_id",
            "status",
            "due_date",
            postgresql_where=text("due_date IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    # Nullable: a manual or chat proposal is not tied to a class.
    audio_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # Denormalized for inbox filters; the composite FK keeps it inside the tenant.
    subject_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default="extraction")
    tipo: Mapped[str] = mapped_column(Text, nullable=False)
    titulo: Mapped[str] = mapped_column(Text, nullable=False)
    detalle: Mapped[str | None] = mapped_column(Text)
    # Exactly one of the pair when `date_status='resolved'` (see `date_contract`).
    due_date: Mapped[date | None] = mapped_column(Date)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Class timezone snapshot; it is never re-derived from the server or the upload.
    timezone: Mapped[str] = mapped_column(Text, nullable=False)
    all_day: Mapped[bool] = mapped_column(Boolean, nullable=False)
    date_status: Mapped[str] = mapped_column(Text, nullable=False)
    date_text: Mapped[str | None] = mapped_column(Text)
    date_reason: Mapped[str | None] = mapped_column(Text)
    confidence_score: Mapped[float | None] = mapped_column(Double)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="needs_review"
    )
    calendar_sync_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="not_requested"
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    due_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    # Calendar block (M5): empty until confirmation, then one of the two exclusive pairs.
    event_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    event_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    event_start_date: Mapped[date | None] = mapped_column(Date)
    event_end_date_exclusive: Mapped[date | None] = mapped_column(Date)
    calendar_id: Mapped[str | None] = mapped_column(Text)
    calendar_event_id: Mapped[str | None] = mapped_column(Text)
    # ETag of the last successful Calendar write: the baseline a conditional PATCH must
    # match. NULL fails closed (no write) until an adopt/review (J3.6).
    calendar_etag: Mapped[str | None] = mapped_column(Text)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class TaskSource(Base):
    """Corpus provenance of a proposal: one row per class transcript span.

    A manual task uses zero rows; an extraction or corpus-grounded chat proposal must
    persist at least one before it is published (A3.2 validates that rule). The span uses
    Unicode code points, like `task_evidence`, and is tenant-pinned through the composite
    FKs.
    """

    __tablename__ = "task_sources"
    __table_args__ = (
        PrimaryKeyConstraint("user_id", "task_id", "segment_id", "char_start", "char_end"),
        ForeignKeyConstraint(
            ["user_id", "task_id"], ["tasks.user_id", "tasks.id"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["user_id", "audio_id"], ["audios.user_id", "audios.id"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["user_id", "transcript_id"], ["transcripts.user_id", "transcripts.id"]
        ),
        ForeignKeyConstraint(
            ["user_id", "segment_id"], ["segments.user_id", "segments.id"]
        ),
        CheckConstraint("char_start >= 0 AND char_start < char_end", name="char_range"),
        # Supporting index for every composite FK (DISENO §2.1.8); the primary key cannot
        # serve transcript/segment lookups.
        Index("task_sources_by_audio", "user_id", "audio_id"),
        Index("task_sources_by_transcript", "user_id", "transcript_id"),
        Index("task_sources_by_segment", "user_id", "segment_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    task_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    audio_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    transcript_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    transcript_version: Mapped[int] = mapped_column(Integer, nullable=False)
    segment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)


class TaskRevision(TenantOwned, Base):
    """A pending change to a confirmed task (edit or reanalysis), never an active write.

    `proposed_fields` keeps the candidate values as jsonb; the active columns of `tasks`
    are not touched until `confirm` promotes exactly one revision in the same transaction
    that bumps `tasks.version` (A3.4). `base_task_version` is the task version the proposal
    was computed from, so a stale revision is rejected instead of silently winning.

    The unique key includes `run_id`; it is NULL for `edit_of_confirmed`, and PostgreSQL's
    default NULL-distinct semantics let a user edit twice at the same base version while a
    repeated reanalysis of the same run cannot duplicate the revision.
    """

    __tablename__ = "task_revisions"
    __table_args__ = (
        UniqueConstraint("user_id", "id"),
        ForeignKeyConstraint(
            ["user_id", "task_id"], ["tasks.user_id", "tasks.id"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["user_id", "run_id"], ["processing_runs.user_id", "processing_runs.id"]
        ),
        UniqueConstraint("task_id", "base_task_version", "revision_type", "run_id"),
        CheckConstraint(_in("revision_type", REVISION_TYPES), name="revision_type"),
        CheckConstraint(_in("status", REVISION_STATUSES), name="status"),
        CheckConstraint("base_task_version >= 1", name="base_task_version"),
        # `proposed_fields` is the map of candidate fields; a JSON scalar/null would be a
        # revision with nothing to promote (SQLAlchemy serializes a Python None as JSON
        # null here, so the NOT NULL column alone would not catch it).
        CheckConstraint("jsonb_typeof(proposed_fields) = 'object'", name="proposed_fields"),
        # An activated revision must record when it was promoted; a pending/discarded one
        # must not claim it. Confirm (A3.4) sets both values together.
        CheckConstraint(
            "(status = 'activated') = (activated_at IS NOT NULL)", name="activated_at"
        ),
        Index("task_revisions_by_task", "user_id", "task_id"),
        Index("task_revisions_by_run", "user_id", "run_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    task_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    base_task_version: Mapped[int] = mapped_column(Integer, nullable=False)
    revision_type: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_fields: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    evidence_validated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="pending_confirmation"
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()

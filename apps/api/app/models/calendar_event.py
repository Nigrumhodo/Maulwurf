"""Local Google Calendar mirror (A3.8 shape, provisioned by J3.7).

One row per remote event with the frozen `(user_id, calendar_id, event_id)` key. Timed events
keep instants; all-day events keep a date pair with an exclusive end. The two pairs are
mutually exclusive, exactly like the confirmed task block (M5), so the dashboard never has to
guess whether a row is an instant or a whole day.

A3.8 completes the link to A3.1's tasks: `(user_id, task_id)` references
`tasks (user_id, id)`, so an event can only point at a task of the same tenant. Deleting the
task (for example the class purge) drops only the link (`ON DELETE SET NULL (task_id)`); the
mirrored event and its ETag survive. `calendar_events_one_link` still guarantees one linked
event per task, and `calendar_events_by_task` gives the composite FK a non-partial index.

`remote_status` keeps what the last read observed: Google's own status (`confirmed`,
`tentative`, …) for a returned event, `missing` for a task-linked row the read did not
return, and `cancelled` for a task-linked row Google reported cancelled. The two local values
are only written on linked rows; a later read that returns the event rewrites the status.
An empty ETag is unknown, never a conditional-write baseline (`etag_not_blank`).
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantOwned, created_at, updated_at, uuid_pk


class CalendarEvent(TenantOwned, Base):
    __tablename__ = "calendar_events"
    __table_args__ = (
        UniqueConstraint("user_id", "calendar_id", "event_id"),
        CheckConstraint("source IN ('google', 'maulwurf')", name="source"),
        CheckConstraint("calendar_id <> ''", name="calendar_id_not_blank"),
        CheckConstraint("event_id <> ''", name="event_id_not_blank"),
        CheckConstraint("remote_status <> ''", name="remote_status_not_blank"),
        CheckConstraint("etag IS NULL OR etag <> ''", name="etag_not_blank"),
        CheckConstraint(
            "(start_at IS NOT NULL AND end_at IS NOT NULL "
            "AND start_date IS NULL AND end_date_exclusive IS NULL) "
            "OR (start_date IS NOT NULL AND end_date_exclusive IS NOT NULL "
            "AND start_at IS NULL AND end_at IS NULL)",
            name="block_pair",
        ),
        CheckConstraint("start_at IS NULL OR start_at < end_at", name="time_range"),
        CheckConstraint(
            "start_date IS NULL OR start_date < end_date_exclusive", name="date_range"
        ),
        Index("calendar_events_by_window", "user_id", "start_at"),
        Index("calendar_events_by_window_date", "user_id", "start_date"),
        Index("calendar_events_by_task", "user_id", "task_id"),
        Index(
            "calendar_events_one_link",
            "task_id",
            unique=True,
            postgresql_where=text("task_id IS NOT NULL"),
        ),
        ForeignKeyConstraint(
            ["user_id", "task_id"],
            ["tasks.user_id", "tasks.id"],
            name="fk_calendar_events_user_id_task_id_tasks",
            ondelete="SET NULL (task_id)",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    calendar_id: Mapped[str] = mapped_column(Text, nullable=False)
    event_id: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default="google")
    task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date_exclusive: Mapped[date | None] = mapped_column(Date)
    timezone: Mapped[str | None] = mapped_column(Text)
    etag: Mapped[str | None] = mapped_column(Text)
    remote_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="confirmed"
    )
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()

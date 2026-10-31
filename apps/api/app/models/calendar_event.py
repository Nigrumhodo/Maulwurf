"""Local Google Calendar mirror (A3.8 shape, provisioned by J3.7).

One row per remote event with the frozen `(user_id, calendar_id, event_id)` key. Timed events
keep instants; all-day events keep a date pair with an exclusive end. The two pairs are
mutually exclusive, exactly like the confirmed task block (M5), so the dashboard never has to
guess whether a row is an instant or a whole day.

`tasks` (A3.1) is not in `develop`, so `task_id` is deliberately a plain column without the
composite foreign key of the design document; the unique partial index for "one linked event
per task" is already in place for when A3.1 lands. J3.7 publishes `source='google'` rows and
never links tasks: the link belongs to the write path (J3.4/A3.8).
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
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
        Index(
            "calendar_events_one_link",
            "task_id",
            unique=True,
            postgresql_where=text("task_id IS NOT NULL"),
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

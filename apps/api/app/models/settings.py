"""Per-user Calendar settings (A3.8 shape, provisioned by J3.7).

`settings` persists the writing calendar and the explicitly selected reading calendars. The
coverage columns are added by J3.7: they record which absolute window and which calendar set
the last *atomic* publication covered, so `GET /calendar/events` can be honest about a stale
or partial mirror instead of presenting it as complete. The A3.8 owner still owns the rest of
the settings contract; this is only the frozen shape J3.7 needs.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, updated_at


class CalendarSettings(Base):
    __tablename__ = "settings"

    # One row per user: `user_id` is both the tenant key and the primary key.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    calendar_write_calendar_id: Mapped[str | None] = mapped_column(Text)
    calendar_read_calendar_ids: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'")
    )
    # J3.7 coverage/freshness: the last fully published window and the calendar set it read.
    calendar_last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    calendar_coverage_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    calendar_coverage_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    calendar_coverage_calendar_ids: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'")
    )
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()

"""Per-user Calendar settings (J3.3; shape frozen by DISENO §2.9 / A3.8).

`settings` is keyed by `user_id`: one row per user, created lazily when the Calendar
integration is connected. Only the Calendar columns exist today; S4 extends this same row
with the Gmail/digest preferences (it does not create a second settings table).

J3.7 adds the coverage columns: they record which absolute window and which calendar set the
last *atomic* publication covered, so `GET /calendar/events` can be honest about a stale or
partial mirror instead of presenting it as complete.

The row is not `TenantOwned` because the tenant column is the primary key, like
`google_credentials`. Every query filters `user_id` explicitly and the table is protected by
forced row-level security for the API and worker roles (migration `0006`).
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, updated_at


class CalendarSettings(Base):
    """Target calendar of the app-created "Maulwurf" secondary calendar."""

    __tablename__ = "settings"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    # Remote Google calendar ID of the single app-created calendar. NULL until the first
    # successful connect; it is never a user-selected calendar (the primary is untouched).
    calendar_write_calendar_id: Mapped[str | None] = mapped_column(Text)
    # Reading calendars selected by the user. Empty means no calendar is read: J3.7
    # returns no events and reports `no_read_calendars`. The app-created calendar is a
    # write target, never an implicit read source.
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

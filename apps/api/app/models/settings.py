"""Per-user Calendar and notification preferences (J3.3/A3.8/A4.1; DISENO §2.9).

`settings` is keyed by `user_id`: one row per user, created lazily when the Calendar
integration is connected. A4.1 extends this same row with the Gmail opt-in, the digest
preferences and the reminder thresholds; there is deliberately no second settings table.

The notification preferences only describe **intent**. Gmail remains opt-in
(`gmail_opt_in=false` by default) and nothing here grants a scope, sends mail or enables a
digest by itself: J4.1 requires the incremental `gmail.send` grant and J4.3/J4.6 read these
values when they schedule occurrences.

J3.7 adds the coverage columns: they record which absolute window and which calendar set the
last *atomic* publication covered, so `GET /calendar/events` can be honest about a stale or
partial mirror instead of presenting it as complete.

The row is not `TenantOwned` because the tenant column is the primary key, like
`google_credentials`. Every query filters `user_id` explicitly and the table is protected by
forced row-level security for the API and worker roles (migration `0006`).

A3.8 closes the shape with CHECKs: selected and coverage calendar ids are never NULL or blank
inside the arrays, the write calendar is never blank, and coverage is either empty or one
complete window (`from < to`, at least one calendar, `last_synced_at` present). A partial
publication can therefore never be presented as covered. "Blank" means empty or only ASCII
whitespace: spaces, tabs, newlines, carriage returns, form feeds and vertical tabs
(`btrim(value, E' \\t\\n\\r\\f' || chr(11)) = ''`); the array checks call the `IMMUTABLE`
helpers created by migration `0013` because a CHECK cannot contain `unnest()`.
"""

import uuid
from datetime import datetime, time

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    Time,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, updated_at


class CalendarSettings(Base):
    """Target calendar of the app-created "Maulwurf" secondary calendar."""

    __tablename__ = "settings"
    __table_args__ = (
        CheckConstraint(
            "calendar_write_calendar_id IS NULL "
            "OR NOT public.maulwurf_text_is_blank(calendar_write_calendar_id)",
            name="write_calendar_not_blank",
        ),
        CheckConstraint(
            "array_position(calendar_read_calendar_ids, NULL) IS NULL",
            name="read_calendars_no_null",
        ),
        CheckConstraint(
            "NOT public.maulwurf_text_array_has_blank(calendar_read_calendar_ids)",
            name="read_calendars_no_blank",
        ),
        CheckConstraint(
            "array_position(calendar_coverage_calendar_ids, NULL) IS NULL",
            name="coverage_calendars_no_null",
        ),
        CheckConstraint(
            "NOT public.maulwurf_text_array_has_blank(calendar_coverage_calendar_ids)",
            name="coverage_calendars_no_blank",
        ),
        CheckConstraint(
            "(calendar_coverage_from IS NULL AND calendar_coverage_to IS NULL "
            "AND cardinality(calendar_coverage_calendar_ids) = 0 "
            "AND calendar_last_synced_at IS NULL) "
            "OR (calendar_coverage_from IS NOT NULL AND calendar_coverage_to IS NOT NULL "
            "AND calendar_coverage_from < calendar_coverage_to "
            "AND cardinality(calendar_coverage_calendar_ids) > 0 "
            "AND calendar_last_synced_at IS NOT NULL)",
            name="coverage_window",
        ),
        # A4.1: digest weekday stays in the closed 0–6 range the §2.9 contract freezes, and
        # both threshold lists are JSON arrays, never a scalar or an object.
        CheckConstraint("digest_weekday BETWEEN 0 AND 6", name="digest_weekday"),
        CheckConstraint(
            "jsonb_typeof(reminder_offsets_hours) = 'array'",
            name="reminder_offsets_hours",
        ),
        CheckConstraint(
            "jsonb_typeof(reminder_allday_offsets_days) = 'array'",
            name="reminder_allday_offsets_days",
        ),
    )

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
    # J3.8 manual refresh cooldown: the start of the last accepted `POST /calendar/sync`.
    # It is deliberately not `calendar_last_synced_at`: a failed attempt still counts against
    # the per-user Google quota, and freshness must keep reporting the last complete read.
    calendar_last_manual_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    calendar_coverage_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    calendar_coverage_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    calendar_coverage_calendar_ids: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'")
    )
    # --- A4.1: Gmail/digest preferences and reminder thresholds -----------------------
    # Opt-in only: false until the user enables Gmail in `/settings` and J4.1 completes the
    # incremental `gmail.send` grant. No mail is sent from this flag alone.
    gmail_opt_in: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    digest_daily_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    # Local wall-clock time of the daily digest; the scheduler resolves it in the user's zone.
    digest_daily_time: Mapped[time] = mapped_column(
        Time, nullable=False, server_default=text("'07:00'")
    )
    digest_weekly_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    # 0–6 per DISENO §2.9. M8 fixes the weekly digest on Sunday; J4.6 resolves how the
    # frozen range maps to that day when it reads the preference.
    digest_weekday: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    # Timed reminders: hours before the instant (T-48/T-24/T-2 by default). All-day
    # reminders: days before the local day (T-3d/T-1d). Both are JSON arrays; A4.3/J4.3 own
    # the interpretation and the cancellation of obsolete occurrences.
    reminder_offsets_hours: Mapped[list[int]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[48, 24, 2]'::jsonb")
    )
    reminder_allday_offsets_days: Mapped[list[int]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[3, 1]'::jsonb")
    )
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()

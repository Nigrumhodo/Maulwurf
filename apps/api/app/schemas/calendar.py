"""Response contract for `GET /calendar/events` (J3.7, S3.md §1.3 and DISENO §3.8).

The endpoint reads only the local mirror. `last_synced_at`, `coverage_ok` and the
closed-set `coverage_notice` are additive to the design example: without them the dashboard
and the `get_calendar_events` tool cannot tell a complete window from a stale one.

Timed events carry `start_at`/`end_at` (instants); all-day events carry the exclusive
`start_date`/`end_date_exclusive` pair. The two pairs are never mixed.
"""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.services.calendar_sync import CoverageNotice


class CalendarEventOut(BaseModel):
    id: str
    calendar_id: str
    event_id: str
    title: str
    source: str
    start_at: datetime | None
    end_at: datetime | None
    start_date: date | None
    end_date_exclusive: date | None
    timezone: str | None
    remote_status: str
    last_synced_at: datetime


class CalendarEventsOut(BaseModel):
    # `from`/`to` are reserved words in Python; the wire aliases keep the frozen §1.3 names.
    model_config = ConfigDict(populate_by_name=True)

    events: list[CalendarEventOut]
    date_from: date = Field(alias="from", serialization_alias="from")
    date_to: date = Field(alias="to", serialization_alias="to")
    last_synced_at: datetime | None
    coverage_ok: bool
    coverage_notice: CoverageNotice | None
    stale: bool

"""Response contracts for the Calendar endpoints (J3.7/J3.8, S3.md §1.3 and DISENO §3.8).

`GET /calendar/events` reads only the local mirror. `last_synced_at`, `coverage_ok` and the
closed-set `coverage_notice` are additive to the design example: without them the dashboard
and the `get_calendar_events` tool cannot tell a complete window from a stale one.

Timed events carry `start_at`/`end_at` (instants); all-day events carry the exclusive
`start_date`/`end_date_exclusive` pair. The two pairs are never mixed. `remote_status` is
Google's status for a returned event, or the mirror's own `missing`/`cancelled` marker on a
row linked to a task that the last read did not return or reported cancelled.

`GET /calendar/conflicts` keeps M5's two independent lists: `time_conflicts` only pairs
timed events whose intervals overlap, while `deadline_clashes` groups two or more active
task deadlines on the same user-local day. An all-day event and a deadline are never time
conflicts. `POST /calendar/sync` answers `202` with the covered window and the publication
counts; the client re-reads the mirror through `GET /calendar/events`.
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


class ConflictEventOut(BaseModel):
    event_id: str
    calendar_id: str
    title: str
    start_at: datetime
    end_at: datetime


class TimeConflictOut(BaseModel):
    a: ConflictEventOut
    b: ConflictEventOut


class DeadlineItemOut(BaseModel):
    task_id: str
    tipo: str


class DeadlineClashOut(BaseModel):
    date: date
    items: list[DeadlineItemOut]


class CalendarConflictsOut(BaseModel):
    # Same wire aliases as `GET /calendar/events`; the envelope is additive to DISENO §3.8
    # and is what D3.6 needs to show freshness next to the conflicts.
    model_config = ConfigDict(populate_by_name=True)

    time_conflicts: list[TimeConflictOut]
    deadline_clashes: list[DeadlineClashOut]
    date_from: date = Field(alias="from", serialization_alias="from")
    date_to: date = Field(alias="to", serialization_alias="to")
    last_synced_at: datetime | None
    coverage_ok: bool
    coverage_notice: CoverageNotice | None
    stale: bool


class CalendarSyncOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    date_from: date = Field(alias="from", serialization_alias="from")
    date_to: date = Field(alias="to", serialization_alias="to")
    calendars: list[str]
    inserted: int
    removed: int
    last_synced_at: datetime

"""Payloads the chat tools hand back.

They are the task and calendar projections, not ad-hoc dicts.
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.schemas.tasks import PendingTaskItem


class ToolErrorPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str


class ProposalPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    source: str
    status: str
    calendar_sync_status: str
    evidence: Literal["corpus", "general_suggestion"]


class PendingTasksPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["tasks"] = "tasks"
    tasks: list[PendingTaskItem]


class ToolCalendarEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calendar_id: str
    event_id: str
    title: str
    start_at: datetime | None
    end_at: datetime | None
    start_date: date | None
    end_date_exclusive: date | None


class CalendarToolPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["calendar"] = "calendar"
    date_from: date
    date_to: date
    coverage_ok: bool
    stale: bool
    coverage_notice: str | None
    events: list[ToolCalendarEvent]


ToolPayload = ToolErrorPayload | ProposalPayload | PendingTasksPayload | CalendarToolPayload

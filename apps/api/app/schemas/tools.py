"""Payloads the chat tools hand back.

They are the task and calendar projections, not ad-hoc dicts.
"""

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

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


class ToolCallOut(BaseModel):
    """One tool call recorded with an assistant message (D3.8).

    The payload is the closed tool projection, not model output: calendar events, pending
    tasks, a proposal (`task_id` + `needs_review`) or a fixed error code. It never carries
    transcript text, audio, tokens or provider payloads.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    ok: bool
    payload: dict[str, Any]


def parse_tool_calls(value: object) -> list[ToolCallOut]:
    """Validate the persisted JSONB list; a malformed entry is skipped, not fatal."""
    if not isinstance(value, list):
        return []
    parsed: list[ToolCallOut] = []
    for item in value:
        try:
            parsed.append(ToolCallOut.model_validate(item))
        except ValidationError:
            continue
    return parsed


def dump_tool_calls(records: Sequence[ToolCallOut]) -> list[dict[str, Any]]:
    return [record.model_dump(mode="json") for record in records]

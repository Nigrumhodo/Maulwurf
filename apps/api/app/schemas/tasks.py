"""M4 task API shapes (A3.3, DISENO 3.7).

These are the HTTP shapes, not the frozen LLM contract in `schemas.analysis`. The list and
detail routes share `TaskOut` so the inbox and the future editor cannot drift.
"""

from datetime import date, datetime
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class TaskSubjectOut(BaseModel):
    id: str
    name: str


class TaskSourceOut(BaseModel):
    audio_id: str
    transcript_version: int
    segment_id: str
    char_start: int
    char_end: int


class TaskEvidenceOut(BaseModel):
    segment_id: str
    char_start: int
    char_end: int
    quote: str
    t_start: float | None
    t_end: float | None


class TaskEventBlockOut(BaseModel):
    start_at: datetime | None
    end_at: datetime | None
    start_date: date | None
    end_date_exclusive: date | None
    duration_default_editable: bool = True


class TaskEventBlockPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_at: AwareDatetime | None = None
    end_at: AwareDatetime | None = None
    start_date: date | None = None
    end_date_exclusive: date | None = None


class PendingRevisionOut(BaseModel):
    id: str
    base_task_version: int
    status: str
    proposed_fields: dict[str, Any] = Field(default_factory=dict)


class TaskOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    audio_id: str | None
    subject: TaskSubjectOut | None
    source: str
    tipo: str
    titulo: str
    detalle: str | None
    due_date: date | None
    due_at: datetime | None
    timezone: str
    all_day: bool
    date_status: str
    date_text: str | None
    date_reason: str | None
    confidence_score: float | None
    status: str
    calendar_sync_status: str
    version: int
    due_version: int
    event_block: TaskEventBlockOut
    sources: list[TaskSourceOut] = Field(default_factory=list)
    evidence: list[TaskEvidenceOut] = Field(default_factory=list)
    pending_revision: PendingRevisionOut | None = None


class TaskListOut(BaseModel):
    items: list[TaskOut]
    next_cursor: str | None = None


class TaskPatch(BaseModel):
    """Partial task edit; the block is replaced as a whole when present."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    tipo: str | None = None
    titulo: str | None = Field(default=None, min_length=1, max_length=200)
    detalle: str | None = Field(default=None, max_length=2000)
    due_date: date | None = None
    due_at: AwareDatetime | None = None
    timezone: str | None = None
    all_day: bool | None = None
    date_status: str | None = None
    date_text: str | None = None
    date_reason: str | None = None
    confidence_score: float | None = Field(default=None, ge=0, le=1)
    event_block: TaskEventBlockPatch | None = None


class TaskVersionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)


class TaskUpdateOut(BaseModel):
    task: TaskOut
    revision_id: str | None = None

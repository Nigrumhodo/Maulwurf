"""M4 task API shapes (A3.3, DISENO §3.7).

These are the HTTP shapes, not the frozen LLM contract in `schemas.analysis`. The list,
detail, create, patch and confirm routes share `TaskOut`, so the inbox, the editor and the
confirmation preview cannot drift.

Input validation mirrors the database contract before a row is written: `due_date` and
`due_at` are mutually exclusive, `date_status` decides which one is required, the Calendar
block is empty or one exclusive pair with a later end, and every source span is a
non-empty Unicode slice with a quote of exactly its length. The service revalidates the
merged result of a PATCH against the active row, because a partial body cannot see it.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.core.validation import is_iana_timezone
from app.schemas.analysis import ActivityType, DateStatus

_STRICT = ConfigDict(extra="forbid", str_strip_whitespace=True)
_NO_STRIP = ConfigDict(extra="forbid")


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


class PendingRevisionOut(BaseModel):
    id: str
    base_task_version: int
    proposed_fields: dict[str, Any]
    status: str


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


class TaskSourceIn(BaseModel):
    """One validated provenance span of a new proposal.

    The client never sends `audio_id`, `transcript_id` or `transcript_version`: the service
    derives them from the persisted segment and transcript, so a forged class or version
    cannot reach `task_sources`.
    """

    model_config = _NO_STRIP

    segment_id: uuid.UUID
    char_start: int = Field(ge=0)
    char_end: int = Field(gt=0)
    quote: str = Field(min_length=1)

    @model_validator(mode="after")
    def _span(self) -> Self:
        if self.char_end <= self.char_start:
            raise ValueError("char_end debe ser mayor que char_start (rango vacío o invertido)")
        if len(self.quote) != self.char_end - self.char_start:
            raise ValueError("la cita debe medir char_end - char_start")
        if not self.quote.strip():
            raise ValueError("la cita no puede estar vacía")
        return self


class TaskEventBlockIn(BaseModel):
    """The Calendar block before confirmation: empty or exactly one exclusive pair."""

    model_config = _NO_STRIP

    start_at: AwareDatetime | None = None
    end_at: AwareDatetime | None = None
    start_date: date | None = None
    end_date_exclusive: date | None = None

    @model_validator(mode="after")
    def _block(self) -> Self:
        validate_event_block(
            start_at=self.start_at,
            end_at=self.end_at,
            start_date=self.start_date,
            end_date_exclusive=self.end_date_exclusive,
        )
        return self


def validate_event_block(
    *,
    start_at: datetime | None,
    end_at: datetime | None,
    start_date: date | None,
    end_date_exclusive: date | None,
) -> None:
    instant = start_at is not None or end_at is not None
    all_day = start_date is not None or end_date_exclusive is not None
    if instant and all_day:
        raise ValueError("el bloque de agenda no puede mezclar horas y días completos")
    if instant:
        if start_at is None or end_at is None:
            raise ValueError("el bloque con hora exige start_at y end_at")
        if start_at >= end_at:
            raise ValueError("end_at debe ser posterior a start_at")
    if all_day:
        if start_date is None or end_date_exclusive is None:
            raise ValueError("el bloque de día completo exige start_date y end_date_exclusive")
        if start_date >= end_date_exclusive:
            raise ValueError("end_date_exclusive debe ser posterior a start_date")


def validate_date_fields(
    *,
    due_date: date | None,
    due_at: datetime | None,
    timezone: str,
    all_day: bool,
    date_status: DateStatus,
) -> None:
    if not is_iana_timezone(timezone):
        raise ValueError("zona horaria IANA no válida")
    if date_status is DateStatus.RESOLVED:
        if (due_date is None) == (due_at is None):
            raise ValueError("date_status=resolved exige exactamente uno de due_date o due_at")
        if all_day != (due_date is not None):
            raise ValueError("all_day=true exige due_date; all_day=false exige due_at")
    elif due_date is not None or due_at is not None:
        raise ValueError("date_status ambiguous/missing exige due_date y due_at nulos")


class TaskCreate(BaseModel):
    """Manual, chat or extraction proposal accepted from an authenticated client.

    `manual` has no class and no sources; `extraction` requires at least one validated
    source; `chat` may be a general suggestion with none. The service applies the same
    rule through `validate_provenance`, so an evidence-free suggestion can never be stored
    as an extraction.
    """

    model_config = _STRICT

    source: str = Field(pattern=r"^(extraction|chat|manual)$")
    tipo: ActivityType
    titulo: str = Field(min_length=1, max_length=200)
    detalle: str | None = Field(default=None, max_length=2000)
    due_date: date | None = None
    due_at: AwareDatetime | None = None
    timezone: str = Field(min_length=1, max_length=64)
    all_day: bool = False
    date_status: DateStatus = DateStatus.MISSING
    date_reason: str | None = Field(default=None, max_length=500)
    confidence_score: float | None = Field(default=None, ge=0, le=1)
    subject_id: uuid.UUID | None = None
    audio_id: uuid.UUID | None = None
    sources: list[TaskSourceIn] = Field(default_factory=list)
    event_block: TaskEventBlockIn | None = None

    @model_validator(mode="after")
    def _date_rules(self) -> Self:
        if "date_status" not in self.model_fields_set and (
            self.due_date is not None or self.due_at is not None
        ):
            self.date_status = DateStatus.RESOLVED
        if "all_day" not in self.model_fields_set:
            self.all_day = self.due_date is not None
        validate_date_fields(
            due_date=self.due_date,
            due_at=self.due_at,
            timezone=self.timezone,
            all_day=self.all_day,
            date_status=self.date_status,
        )
        if self.source == "manual" and self.sources:
            raise ValueError("una tarea manual no cita evidencia del corpus")
        if self.source == "manual" and self.audio_id is not None:
            raise ValueError("una tarea manual no pertenece a una clase")
        if self.source == "extraction" and not self.sources:
            raise ValueError("una tarea de extracción exige al menos una fuente")
        if self.source == "chat" and not self.sources and self.audio_id is not None:
            raise ValueError("una sugerencia general no pertenece a una clase")
        seen: set[tuple[uuid.UUID, int, int]] = set()
        for span in self.sources:
            key = (span.segment_id, span.char_start, span.char_end)
            if key in seen:
                raise ValueError("la evidencia contiene un span duplicado")
            seen.add(key)
        return self


class TaskPatch(BaseModel):
    """Partial edit. `version` is mandatory; sources/evidence are not editable here.

    Omitted fields are untouched; explicit `null` clears a nullable field. The service
    merges the body over the active row and validates the result as one task.
    """

    model_config = _STRICT

    version: int = Field(ge=1)
    tipo: ActivityType | None = None
    titulo: str | None = Field(default=None, min_length=1, max_length=200)
    detalle: str | None = Field(default=None, max_length=2000)
    due_date: date | None = None
    due_at: AwareDatetime | None = None
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    all_day: bool | None = None
    date_status: DateStatus | None = None
    date_reason: str | None = Field(default=None, max_length=500)
    confidence_score: float | None = Field(default=None, ge=0, le=1)
    subject_id: uuid.UUID | None = None
    event_block: TaskEventBlockIn | None = None

    @field_validator("timezone")
    @classmethod
    def _timezone(cls, value: str | None) -> str | None:
        if value is not None and not is_iana_timezone(value):
            raise ValueError("zona horaria IANA no válida")
        return value

    @model_validator(mode="after")
    def _patch_rules(self) -> Self:
        for name in ("tipo", "titulo", "timezone", "all_day", "date_status"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} no puede ser nulo")
        if (
            "due_date" in self.model_fields_set
            and "due_at" in self.model_fields_set
            and self.due_date is not None
            and self.due_at is not None
        ):
            raise ValueError("due_date y due_at son mutuamente excluyentes")
        if self.date_status in {DateStatus.AMBIGUOUS, DateStatus.MISSING} and (
            self.due_date is not None or self.due_at is not None
        ):
            raise ValueError("date_status ambiguous/missing exige due_date y due_at nulos")
        return self


class TaskConfirmBody(BaseModel):
    model_config = _STRICT

    expected_version: int = Field(ge=1)
    revision_id: uuid.UUID | None = None


class TaskVersionBody(BaseModel):
    model_config = _STRICT

    expected_version: int = Field(ge=1)


class TaskConfirmOut(BaseModel):
    task: TaskOut
    notice: str | None = None


class TaskLifecycleOut(BaseModel):
    task: TaskOut
    cancelled_reminders: int
    calendar_event_kept: bool
    notice: str | None = None

"""Backend orchestrator for the three F2 chat tools (L3.6).

The allowlist is `get_calendar_events`, `get_pending_tasks` and `propose_task`. The
tenant is the authenticated user, never a field the model sends. Ranges and payloads
are validated before any read. `propose_task` creates an inbox proposal (`source=chat`)
and never a Calendar write or an email. A proposal with no validated source is marked
`general_suggestion`; it is not stored as an extraction.

Rounds and the time budget are provisional until a measured cap replaces them. Four
rounds and 15 seconds are the defaults this module enforces.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Audio, Task, TaskSource
from app.schemas.analysis import ActivityType
from app.services.analysis import (
    AnalysisPayloadError,
    mention_from_payload,
    parse_extraction_payload,
)
from app.services.analysis_validation import (
    AnalysisValidationError,
    load_class_transcript,
    validate_analysis,
)
from app.services.calendar_sync import CalendarService, CalendarWindowInvalid
from app.services.date_resolution import DateResolutionError, MentionKind, resolve_due

MAX_TOOL_ROUNDS = 4
TOOL_BUDGET_SECONDS = 15.0
_ALLOWED = frozenset({"get_calendar_events", "get_pending_tasks", "propose_task"})
_PENDING_STATUSES = ("needs_review", "pending")
_STRICT = ConfigDict(extra="forbid")


class ToolError(Exception):
    """A tool call the backend refuses. The message is fixed and carries no payload."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: Mapping[str, Any]


@dataclass(frozen=True)
class ToolResult:
    name: str
    ok: bool
    payload: dict[str, Any]


class _CalendarArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    date_from: date | None = Field(default=None, alias="from")
    date_to: date | None = Field(default=None, alias="to")


class _EmptyArgs(BaseModel):
    model_config = _STRICT


class _SourceArg(BaseModel):
    model_config = _STRICT

    audio_id: UUID
    segment_id: UUID
    inicio_char: int = Field(ge=0)
    fin_char: int = Field(gt=0)
    cita_textual: str = Field(min_length=1)


class _ProposeArgs(BaseModel):
    model_config = _STRICT

    titulo: str = Field(min_length=1, max_length=200)
    tipo: ActivityType
    detalle: str | None = Field(default=None, max_length=2000)
    timezone: str
    mencion: dict[str, Any]
    sources: list[_SourceArg] = Field(default_factory=list)


def admission_error(
    call: ToolCall,
    *,
    index: int,
    started: datetime,
    now: datetime,
    max_rounds: int = MAX_TOOL_ROUNDS,
    budget_seconds: float = TOOL_BUDGET_SECONDS,
) -> str | None:
    """Reject a call the orchestrator must not run. `None` means it may proceed."""
    if index >= max_rounds:
        return "tool_rounds_exceeded"
    if (now - started).total_seconds() > budget_seconds:
        return "tool_budget_exceeded"
    if call.name not in _ALLOWED:
        return "tool_not_allowed"
    if "user_id" in call.arguments:
        return "tenant_rejected"
    return None


class ToolOrchestrator:
    """Run a bounded batch of tool calls for one authenticated user."""

    def __init__(
        self,
        db: AsyncSession,
        *,
        max_rounds: int = MAX_TOOL_ROUNDS,
        budget_seconds: float = TOOL_BUDGET_SECONDS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._db = db
        self._max_rounds = max_rounds
        self._budget_seconds = budget_seconds
        self._clock = clock

    async def execute(
        self,
        calls: Sequence[ToolCall],
        *,
        user_id: UUID,
        now: datetime,
    ) -> list[ToolResult]:
        results: list[ToolResult] = []
        for index, call in enumerate(calls):
            current = now if self._clock is None else self._clock()
            code = admission_error(
                call,
                index=index,
                started=now,
                now=current,
                max_rounds=self._max_rounds,
                budget_seconds=self._budget_seconds,
            )
            if code is not None:
                results.append(_rejected(call.name, code))
                continue
            try:
                payload = await self._dispatch(call, user_id=user_id, now=now)
            except ToolError as exc:
                results.append(_rejected(call.name, exc.code))
                continue
            results.append(ToolResult(name=call.name, ok=True, payload=payload))
        return results

    async def _dispatch(
        self, call: ToolCall, *, user_id: UUID, now: datetime
    ) -> dict[str, Any]:
        if call.name == "get_calendar_events":
            return await self._calendar(call.arguments, user_id=user_id, now=now)
        if call.name == "get_pending_tasks":
            _parse(_EmptyArgs, call.arguments)
            return await self._pending(user_id)
        if call.name == "propose_task":
            return await self._propose(call.arguments, user_id=user_id, now=now)
        raise ToolError("tool_not_allowed", "the tool is not in the allowlist")

    async def _calendar(
        self, arguments: Mapping[str, Any], *, user_id: UUID, now: datetime
    ) -> dict[str, Any]:
        parsed = _parse(_CalendarArgs, arguments)
        try:
            page = await CalendarService(self._db).page(
                user_id=user_id,
                date_from=parsed.date_from,
                date_to=parsed.date_to,
                now=now,
            )
        except CalendarWindowInvalid as exc:
            raise ToolError("window_invalid", "the calendar range is not valid") from exc
        return {
            "source": "calendar",
            "from": page.date_from.isoformat(),
            "to": page.date_to.isoformat(),
            "coverage_ok": page.coverage_ok,
            "stale": page.stale,
            "coverage_notice": page.coverage_notice,
            "events": [
                {
                    "calendar_id": event.calendar_id,
                    "event_id": event.event_id,
                    "title": event.title,
                    "start_at": None if event.start_at is None else event.start_at.isoformat(),
                    "end_at": None if event.end_at is None else event.end_at.isoformat(),
                    "start_date": (
                        None if event.start_date is None else event.start_date.isoformat()
                    ),
                    "end_date_exclusive": (
                        None
                        if event.end_date_exclusive is None
                        else event.end_date_exclusive.isoformat()
                    ),
                }
                for event in page.events
            ],
        }

    async def _pending(self, user_id: UUID) -> dict[str, Any]:
        rows = (
            await self._db.scalars(
                select(Task)
                .where(Task.user_id == user_id, Task.status.in_(_PENDING_STATUSES))
                .order_by(Task.created_at)
            )
        ).all()
        return {
            "source": "tasks",
            "tasks": [
                {
                    "id": str(task.id),
                    "titulo": task.titulo,
                    "tipo": task.tipo,
                    "status": task.status,
                    "date_status": task.date_status,
                    "due_date": None if task.due_date is None else task.due_date.isoformat(),
                    "due_at": None if task.due_at is None else task.due_at.isoformat(),
                    "source": task.source,
                }
                for task in rows
            ],
        }

    async def _propose(
        self, arguments: Mapping[str, Any], *, user_id: UUID, now: datetime
    ) -> dict[str, Any]:
        parsed = _parse(_ProposeArgs, arguments)
        if not parsed.sources:
            return await self._suggest(parsed, user_id=user_id, now=now)
        audio_ids = {source.audio_id for source in parsed.sources}
        if len(audio_ids) != 1:
            raise ToolError("sources_invalid", "a proposal cites one class")
        audio_id = audio_ids.pop()
        audio = (
            await self._db.execute(
                select(Audio).where(
                    Audio.user_id == user_id,
                    Audio.id == audio_id,
                    Audio.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if audio is None or audio.active_transcript_version is None:
            raise ToolError("sources_invalid", "the cited class is not available")
        view = await load_class_transcript(
            self._db,
            user_id=user_id,
            audio_id=audio_id,
            transcript_version=audio.active_transcript_version,
        )
        raw = json.dumps(
            {
                "items": [
                    {
                        "tipo": parsed.tipo,
                        "titulo": parsed.titulo,
                        "detalle": parsed.detalle,
                        "timezone": audio.class_timezone,
                        "all_day": False,
                        "date_status": "missing",
                        "mencion": parsed.mencion,
                        "fuente": {
                            "spans": [
                                {
                                    "segment_id": str(source.segment_id),
                                    "inicio_char": source.inicio_char,
                                    "fin_char": source.fin_char,
                                    "cita_textual": source.cita_textual,
                                }
                                for source in parsed.sources
                            ]
                        },
                    }
                ],
                "resumen_clase": "Chat proposal.",
                "temas": ["chat"],
            }
        )
        try:
            extracted = parse_extraction_payload(
                raw,
                class_date=audio.class_date,
                class_timezone=audio.class_timezone,
                now=now,
            )
        except AnalysisPayloadError as exc:
            raise ToolError("schema_invalid", "the tool arguments are not valid") from exc
        if not extracted.result.items:
            raise ToolError("mention_invalid", "the date mention is not usable")
        try:
            validate_analysis(extracted.result, view)
        except AnalysisValidationError as exc:
            raise ToolError(
                "evidence_rejected", "the cited evidence does not match the class"
            ) from exc
        item = extracted.result.items[0]
        task = Task(
            user_id=user_id,
            audio_id=audio_id,
            subject_id=audio.subject_id,
            source="chat",
            tipo=item.tipo,
            titulo=item.titulo,
            detalle=item.detalle,
            due_date=item.due_date,
            due_at=item.due_at,
            timezone=item.timezone,
            all_day=item.all_day,
            date_status=item.date_status.value,
            date_reason=extracted.reasons[0],
            confidence_score=item.confidence_score,
            status="needs_review",
            calendar_sync_status="not_requested",
        )
        self._db.add(task)
        await self._db.flush()
        for span in item.fuente.spans:
            self._db.add(
                TaskSource(
                    user_id=user_id,
                    task_id=task.id,
                    audio_id=audio_id,
                    transcript_id=view.transcript_id,
                    transcript_version=view.transcript_version,
                    segment_id=span.segment_id,
                    char_start=span.inicio_char,
                    char_end=span.fin_char,
                )
            )
        await self._db.flush()
        return _proposal_payload(task, evidence="corpus")

    async def _suggest(
        self, parsed: _ProposeArgs, *, user_id: UUID, now: datetime
    ) -> dict[str, Any]:
        try:
            mention = mention_from_payload(parsed.mencion)
        except DateResolutionError as exc:
            raise ToolError("mention_invalid", "the date mention is not usable") from exc
        if mention.kind in {MentionKind.DAY_OFFSET, MentionKind.WEEKDAY}:
            raise ToolError(
                "mention_needs_class",
                "a suggestion without evidence cannot use a class-relative date",
            )
        try:
            resolved = resolve_due(
                mention,
                class_date=date(2020, 1, 6),
                class_timezone=parsed.timezone,
                now=now,
            )
        except DateResolutionError as exc:
            raise ToolError("mention_invalid", "the date mention is not usable") from exc
        task = Task(
            user_id=user_id,
            source="chat",
            tipo=parsed.tipo,
            titulo=parsed.titulo,
            detalle=parsed.detalle,
            due_date=resolved.due_date,
            due_at=resolved.due_at,
            timezone=resolved.timezone,
            all_day=resolved.all_day if resolved.date_status.value == "resolved" else False,
            date_status=resolved.date_status.value,
            date_reason=resolved.reason,
            status="needs_review",
            calendar_sync_status="not_requested",
        )
        self._db.add(task)
        await self._db.flush()
        return _proposal_payload(task, evidence="general_suggestion")


def _proposal_payload(task: Task, *, evidence: str) -> dict[str, Any]:
    return {
        "task_id": str(task.id),
        "source": task.source,
        "status": task.status,
        "calendar_sync_status": task.calendar_sync_status,
        "evidence": evidence,
    }


def _parse[T: BaseModel](model: type[T], arguments: Mapping[str, Any]) -> T:
    try:
        return model.model_validate(arguments)
    except ValidationError as exc:
        raise ToolError("schema_invalid", "the tool arguments are not valid") from exc


def _rejected(name: str, code: str) -> ToolResult:
    return ToolResult(name=name, ok=False, payload={"code": code})

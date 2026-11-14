"""Typed columns of a task proposal.

`ProposalFields` is what reanalysis may propose: the activity and its due date, never
the Calendar block or the subject. `TaskSnapshot` is the active row those columns sit
on, plus the subject and the event block.

A revision is JSON. `overlay` replaces only keys that are present, so an analysis
payload that omits the block cannot clear a block the user already set. `date_text`
in that JSON is ignored and rendered again from the due columns. An explicit JSON
null clears a nullable column.

`same_as` compares instants in UTC and compares `date_text` as stored on each side.
A merged snapshot carries the freshly rendered text, so a stale stored `date_text`
still counts as a change.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, cast

from app.core.errors import validation_failed
from app.models import Task
from app.schemas.analysis import DateStatus
from app.schemas.tasks import TaskCreate, TaskPatch
from app.services.date_resolution import render_due_text


def _same_instant(left: datetime | None, right: datetime | None) -> bool:
    if left is None or right is None:
        return left is right
    return left.astimezone(UTC) == right.astimezone(UTC)


def _iso_date(value: date | None) -> str | None:
    return value.isoformat() if isinstance(value, date) else None


def _iso_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _decode_date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        raise ValueError("revision date is not a date")
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise ValueError("revision date is not a date")


def _decode_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise ValueError("revision instant is not a datetime")


def _decode_uuid(value: object) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str):
        return uuid.UUID(value)
    raise ValueError("revision subject is not an id")


@dataclass(frozen=True)
class ProposalFields:
    """The columns a reanalysis may propose. The Calendar block is not one of them."""

    tipo: str
    titulo: str
    detalle: str | None
    due_date: date | None
    due_at: datetime | None
    timezone: str
    all_day: bool
    date_status: str
    confidence_score: float | None
    date_reason: str | None

    @classmethod
    def from_task(cls, task: Task) -> ProposalFields:
        return cls(
            tipo=task.tipo,
            titulo=task.titulo,
            detalle=task.detalle,
            due_date=task.due_date,
            due_at=task.due_at,
            timezone=task.timezone,
            all_day=task.all_day,
            date_status=task.date_status,
            confidence_score=task.confidence_score,
            date_reason=task.date_reason,
        )

    def rendered_date_text(self) -> str | None:
        return render_due_text(
            due_date=self.due_date,
            due_at=self.due_at,
            timezone=self.timezone,
            all_day=self.all_day,
            date_status=self.date_status,
        )

    def same_as(self, other: ProposalFields) -> bool:
        return (
            self.tipo == other.tipo
            and self.titulo == other.titulo
            and self.detalle == other.detalle
            and self.due_date == other.due_date
            and _same_instant(self.due_at, other.due_at)
            and self.timezone == other.timezone
            and self.all_day == other.all_day
            and self.date_status == other.date_status
            and self.confidence_score == other.confidence_score
            and self.date_reason == other.date_reason
        )

    def due_changed(self, other: ProposalFields) -> bool:
        return (
            self.due_date != other.due_date
            or not _same_instant(self.due_at, other.due_at)
            or self.timezone != other.timezone
            or self.all_day != other.all_day
            or self.date_status != other.date_status
        )

    def apply(self, task: Task) -> None:
        """Write the proposal columns. The subject and the Calendar block stay put."""
        task.tipo = self.tipo
        task.titulo = self.titulo
        task.detalle = self.detalle
        task.due_date = self.due_date
        task.due_at = self.due_at
        task.timezone = self.timezone
        task.all_day = self.all_day
        task.date_status = self.date_status
        task.date_reason = self.date_reason
        task.confidence_score = self.confidence_score
        task.date_text = self.rendered_date_text()

    def to_payload(self) -> dict[str, object]:
        """JSON stored on an analysis revision. The block and the subject are absent."""
        return {
            "tipo": self.tipo,
            "titulo": self.titulo,
            "detalle": self.detalle,
            "due_date": _iso_date(self.due_date),
            "due_at": _iso_datetime(self.due_at),
            "timezone": self.timezone,
            "all_day": self.all_day,
            "date_status": self.date_status,
            "date_text": self.rendered_date_text(),
            "confidence_score": self.confidence_score,
            "date_reason": self.date_reason,
        }


@dataclass(frozen=True)
class TaskSnapshot:
    """The active task columns a PATCH or a confirmation may write."""

    proposal: ProposalFields
    subject_id: uuid.UUID | None
    event_start_at: datetime | None
    event_end_at: datetime | None
    event_start_date: date | None
    event_end_date_exclusive: date | None
    date_text: str | None

    @classmethod
    def from_task(cls, task: Task) -> TaskSnapshot:
        return cls(
            proposal=ProposalFields.from_task(task),
            subject_id=task.subject_id,
            event_start_at=task.event_start_at,
            event_end_at=task.event_end_at,
            event_start_date=task.event_start_date,
            event_end_date_exclusive=task.event_end_date_exclusive,
            date_text=task.date_text,
        )

    @classmethod
    def from_create(cls, body: TaskCreate, *, subject_id: uuid.UUID | None) -> TaskSnapshot:
        block = body.event_block
        proposal = ProposalFields(
            tipo=body.tipo,
            titulo=body.titulo,
            detalle=body.detalle,
            due_date=body.due_date,
            due_at=body.due_at,
            timezone=body.timezone,
            all_day=body.all_day,
            date_status=body.date_status.value,
            confidence_score=body.confidence_score,
            date_reason=body.date_reason,
        )
        return cls(
            proposal=proposal,
            subject_id=subject_id,
            event_start_at=None if block is None else block.start_at,
            event_end_at=None if block is None else block.end_at,
            event_start_date=None if block is None else block.start_date,
            event_end_date_exclusive=None if block is None else block.end_date_exclusive,
            date_text=proposal.rendered_date_text(),
        )

    def same_as(self, other: TaskSnapshot) -> bool:
        return (
            self.proposal.same_as(other.proposal)
            and self.subject_id == other.subject_id
            and _same_instant(self.event_start_at, other.event_start_at)
            and _same_instant(self.event_end_at, other.event_end_at)
            and self.event_start_date == other.event_start_date
            and self.event_end_date_exclusive == other.event_end_date_exclusive
            and self.date_text == other.date_text
        )

    def due_changed(self, other: TaskSnapshot) -> bool:
        return self.proposal.due_changed(other.proposal)

    def apply(self, task: Task) -> None:
        self.proposal.apply(task)
        task.subject_id = self.subject_id
        task.event_start_at = self.event_start_at
        task.event_end_at = self.event_end_at
        task.event_start_date = self.event_start_date
        task.event_end_date_exclusive = self.event_end_date_exclusive

    def to_payload(self) -> dict[str, object]:
        """JSON stored on an HTTP revision. Subject and block keys are always present."""
        payload = self.proposal.to_payload()
        payload["subject_id"] = None if self.subject_id is None else str(self.subject_id)
        payload["event_start_at"] = _iso_datetime(self.event_start_at)
        payload["event_end_at"] = _iso_datetime(self.event_end_at)
        payload["event_start_date"] = _iso_date(self.event_start_date)
        payload["event_end_date_exclusive"] = _iso_date(self.event_end_date_exclusive)
        return payload

    def overlay(self, payload: Mapping[str, Any]) -> TaskSnapshot:
        """Apply a revision JSON object without filling in keys it does not carry.

        :raises ValueError: a present date, instant, or subject is not decodable.
        """
        proposal = self.proposal
        tipo = _decode_str(payload, "tipo", proposal.tipo)
        titulo = _decode_str(payload, "titulo", proposal.titulo)
        detalle = _decode_optional_str(payload, "detalle", proposal.detalle)
        due_date = (
            _decode_date(payload["due_date"]) if "due_date" in payload else proposal.due_date
        )
        due_at = _decode_datetime(payload["due_at"]) if "due_at" in payload else proposal.due_at
        timezone = _decode_str(payload, "timezone", proposal.timezone)
        all_day = _decode_bool(payload, "all_day", proposal.all_day)
        date_status = _decode_str(payload, "date_status", proposal.date_status)
        date_reason = _decode_optional_str(payload, "date_reason", proposal.date_reason)
        confidence_score = _decode_confidence(payload, proposal.confidence_score)
        subject_id = (
            _decode_uuid(payload["subject_id"]) if "subject_id" in payload else self.subject_id
        )
        event_start_at = (
            _decode_datetime(payload["event_start_at"])
            if "event_start_at" in payload
            else self.event_start_at
        )
        event_end_at = (
            _decode_datetime(payload["event_end_at"])
            if "event_end_at" in payload
            else self.event_end_at
        )
        event_start_date = (
            _decode_date(payload["event_start_date"])
            if "event_start_date" in payload
            else self.event_start_date
        )
        event_end_date_exclusive = (
            _decode_date(payload["event_end_date_exclusive"])
            if "event_end_date_exclusive" in payload
            else self.event_end_date_exclusive
        )
        merged = ProposalFields(
            tipo=tipo,
            titulo=titulo,
            detalle=detalle,
            due_date=due_date,
            due_at=due_at,
            timezone=timezone,
            all_day=all_day,
            date_status=date_status,
            confidence_score=confidence_score,
            date_reason=date_reason,
        )
        return TaskSnapshot(
            proposal=merged,
            subject_id=subject_id,
            event_start_at=event_start_at,
            event_end_at=event_end_at,
            event_start_date=event_start_date,
            event_end_date_exclusive=event_end_date_exclusive,
            date_text=merged.rendered_date_text(),
        )

    def merge(self, body: TaskPatch) -> TaskSnapshot:
        """Merge a partial PATCH over this row.

        Omitted fields stay. An explicit null clears a nullable field. A due edit that
        does not send `date_status` implies the resolution: one due representation means
        resolved, and clearing both means the date is missing again.
        """
        fields_set = body.model_fields_set - {"version"}
        proposal = self.proposal
        # The schema rejects null for these. The cast preserves a value that reached
        # merge anyway, which the old dict path wrote through unchanged.
        tipo = cast(str, body.tipo) if "tipo" in fields_set else proposal.tipo
        titulo = cast(str, body.titulo) if "titulo" in fields_set else proposal.titulo
        detalle = body.detalle if "detalle" in fields_set else proposal.detalle
        due_date = body.due_date if "due_date" in fields_set else proposal.due_date
        due_at = body.due_at if "due_at" in fields_set else proposal.due_at
        timezone = cast(str, body.timezone) if "timezone" in fields_set else proposal.timezone
        all_day = cast(bool, body.all_day) if "all_day" in fields_set else proposal.all_day
        date_reason = body.date_reason if "date_reason" in fields_set else proposal.date_reason
        confidence_score = (
            body.confidence_score
            if "confidence_score" in fields_set
            else proposal.confidence_score
        )
        subject_id = body.subject_id if "subject_id" in fields_set else self.subject_id
        date_status = proposal.date_status
        if "date_status" in fields_set:
            if body.date_status is None:  # pragma: no cover - the schema rejects this
                raise validation_failed({"date_status": "date_status no puede ser nulo"})
            date_status = body.date_status.value
        elif "due_date" in fields_set or "due_at" in fields_set:
            if due_date is not None and due_at is None:
                date_status = DateStatus.RESOLVED.value
                all_day = True
            elif due_at is not None and due_date is None:
                date_status = DateStatus.RESOLVED.value
                all_day = False
            else:
                date_status = DateStatus.MISSING.value
                all_day = False

        event_start_at = self.event_start_at
        event_end_at = self.event_end_at
        event_start_date = self.event_start_date
        event_end_date_exclusive = self.event_end_date_exclusive
        if "event_block" in fields_set:
            block = body.event_block
            if block is None:
                event_start_at = None
                event_end_at = None
                event_start_date = None
                event_end_date_exclusive = None
            else:
                event_start_at = block.start_at
                event_end_at = block.end_at
                event_start_date = block.start_date
                event_end_date_exclusive = block.end_date_exclusive

        merged = ProposalFields(
            tipo=tipo,
            titulo=titulo,
            detalle=detalle,
            due_date=due_date,
            due_at=due_at,
            timezone=timezone,
            all_day=all_day,
            date_status=date_status,
            confidence_score=confidence_score,
            date_reason=date_reason,
        )
        return TaskSnapshot(
            proposal=merged,
            subject_id=subject_id,
            event_start_at=event_start_at,
            event_end_at=event_end_at,
            event_start_date=event_start_date,
            event_end_date_exclusive=event_end_date_exclusive,
            date_text=merged.rendered_date_text(),
        )


def needs_review_task(
    *,
    user_id: uuid.UUID,
    source: str,
    fields: ProposalFields | TaskSnapshot,
    audio_id: uuid.UUID | None = None,
    subject_id: uuid.UUID | None = None,
) -> Task:
    """A new inbox row. Status and sync status are fixed; the caller fills evidence.

    `ProposalFields` writes the activity only. `subject_id` is the argument, and the
    Calendar block stays empty. A `TaskSnapshot` writes the subject and the block too,
    and the `subject_id` argument is not used.
    """
    task = Task(
        user_id=user_id,
        audio_id=audio_id,
        source=source,
        status="needs_review",
        calendar_sync_status="not_requested",
    )
    if isinstance(fields, TaskSnapshot):
        fields.apply(task)
        return task
    task.subject_id = subject_id
    fields.apply(task)
    return task


def _decode_str(payload: Mapping[str, Any], key: str, current: str) -> str:
    if key not in payload:
        return current
    value = payload[key]
    if isinstance(value, str):
        return value
    raise ValueError(f"revision {key} is not text")


def _decode_optional_str(payload: Mapping[str, Any], key: str, current: str | None) -> str | None:
    if key not in payload:
        return current
    value = payload[key]
    if value is None or isinstance(value, str):
        return value
    raise ValueError(f"revision {key} is not text")


def _decode_bool(payload: Mapping[str, Any], key: str, current: bool) -> bool:
    if key not in payload:
        return current
    value = payload[key]
    if isinstance(value, bool):
        return value
    raise ValueError(f"revision {key} is not a boolean")


def _decode_confidence(payload: Mapping[str, Any], current: float | None) -> float | None:
    if "confidence_score" not in payload:
        return current
    value = payload["confidence_score"]
    if value is None or isinstance(value, int | float) and not isinstance(value, bool):
        return cast(float | None, value)
    raise ValueError("revision confidence_score is not a number")

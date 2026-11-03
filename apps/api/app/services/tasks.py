"""Tenant-scoped M4 task reads (A3.3 read slice).

The active task row is the source of truth. Evidence is reconstructed from the provenance
pair `task_sources` + immutable `segments` until A3.2 persists a separate `task_evidence`
table; the response never accepts a quote, timestamp or class id from the model.
"""

import base64
import binascii
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, cast

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError, not_found, validation_failed
from app.core.validation import is_iana_timezone
from app.models import Segment, Subject, Task, TaskRevision, TaskSource
from app.models.task import ACTIVITY_TYPES, DATE_STATUSES, TASK_STATUSES
from app.schemas.tasks import (
    PendingRevisionOut,
    TaskEventBlockOut,
    TaskEventBlockPatch,
    TaskEvidenceOut,
    TaskListOut,
    TaskOut,
    TaskPatch,
    TaskSourceOut,
    TaskSubjectOut,
    TaskUpdateOut,
)

TASK_CURSOR_VERSION = 1
_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200
_MAX_CURSOR_LENGTH = 512
_CURSOR_INVALID = "cursor inválido o malformado"


@dataclass(frozen=True)
class TaskCursor:
    created_at: datetime
    task_id: uuid.UUID


def _encode_cursor(cursor: TaskCursor) -> str:
    payload = json.dumps(
        {
            "v": TASK_CURSOR_VERSION,
            "t": cursor.created_at.astimezone(UTC).isoformat(),
            "i": str(cursor.task_id),
        },
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def _decode_cursor(value: str) -> TaskCursor:
    if not value or len(value) > _MAX_CURSOR_LENGTH:
        raise validation_failed({"cursor": _CURSOR_INVALID})
    try:
        padded = value + "=" * (-len(value) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        data = json.loads(raw)
    except (ValueError, TypeError, binascii.Error, UnicodeError) as exc:
        raise validation_failed({"cursor": _CURSOR_INVALID}) from exc
    if not isinstance(data, dict):
        raise validation_failed({"cursor": _CURSOR_INVALID})
    try:
        if data.get("v") != TASK_CURSOR_VERSION:
            raise ValueError("unsupported cursor")
        created_at = datetime.fromisoformat(str(data["t"]))
        task_id = uuid.UUID(str(data["i"]))
    except (ValueError, TypeError, KeyError) as exc:
        raise validation_failed({"cursor": _CURSOR_INVALID}) from exc
    if created_at.tzinfo is None:
        raise validation_failed({"cursor": _CURSOR_INVALID})
    return TaskCursor(created_at.astimezone(UTC), task_id)


def _validate_status(status: str | None) -> None:
    if status is not None and status not in TASK_STATUSES:
        raise validation_failed(
            {"status": f"debe ser uno de: {', '.join(TASK_STATUSES)}"}
        )


def _due_conditions(
    stmt: Any, due_from: date | None, due_to: date | None
) -> Any:
    if due_from is not None:
        start = datetime.combine(due_from, time.min, tzinfo=UTC)
        stmt = stmt.where(or_(Task.due_date >= due_from, Task.due_at >= start))
    if due_to is not None:
        end = datetime.combine(due_to + timedelta(days=1), time.min, tzinfo=UTC)
        stmt = stmt.where(or_(Task.due_date <= due_to, Task.due_at < end))
    return stmt


def _event_block(task: Task) -> TaskEventBlockOut:
    return TaskEventBlockOut(
        start_at=task.event_start_at,
        end_at=task.event_end_at,
        start_date=task.event_start_date,
        end_date_exclusive=task.event_end_date_exclusive,
        duration_default_editable=True,
    )


def _task_out(
    task: Task,
    *,
    subject: Subject | None,
    sources: list[TaskSource],
    evidence: list[TaskEvidenceOut],
    pending: TaskRevision | None,
    segment_ordinals: dict[uuid.UUID, int],
) -> TaskOut:
    ordered_sources = sorted(
        sources,
        key=lambda row: (
            segment_ordinals.get(row.segment_id, 0),
            row.char_start,
            row.char_end,
        ),
    )
    return TaskOut(
        id=str(task.id),
        audio_id=str(task.audio_id) if task.audio_id is not None else None,
        subject=(
            TaskSubjectOut(id=str(subject.id), name=subject.name)
            if subject is not None
            else None
        ),
        source=task.source,
        tipo=task.tipo,
        titulo=task.titulo,
        detalle=task.detalle,
        due_date=task.due_date,
        due_at=task.due_at,
        timezone=task.timezone,
        all_day=task.all_day,
        date_status=task.date_status,
        date_text=task.date_text,
        date_reason=task.date_reason,
        confidence_score=task.confidence_score,
        status=task.status,
        calendar_sync_status=task.calendar_sync_status,
        version=task.version,
        due_version=task.due_version,
        event_block=_event_block(task),
        sources=[
            TaskSourceOut(
                audio_id=str(row.audio_id),
                transcript_version=row.transcript_version,
                segment_id=str(row.segment_id),
                char_start=row.char_start,
                char_end=row.char_end,
            )
            for row in ordered_sources
        ],
        evidence=evidence,
        pending_revision=(
            PendingRevisionOut(
                id=str(pending.id),
                base_task_version=pending.base_task_version,
                status=pending.status,
                proposed_fields=pending.proposed_fields or {},
            )
            if pending is not None
            else None
        ),
    )


async def _assemble(
    db: AsyncSession, user_id: uuid.UUID, tasks: list[Task]
) -> list[TaskOut]:
    if not tasks:
        return []
    task_ids = [task.id for task in tasks]
    subject_ids = {task.subject_id for task in tasks if task.subject_id is not None}

    subjects = {
        subject.id: subject
        for subject in (
            await db.scalars(
                select(Subject).where(
                    Subject.user_id == user_id, Subject.id.in_(subject_ids)
                )
            )
        ).all()
    }
    source_rows = (
        await db.scalars(
            select(TaskSource)
            .where(TaskSource.user_id == user_id, TaskSource.task_id.in_(task_ids))
            .order_by(TaskSource.char_start, TaskSource.char_end)
        )
    ).all()
    source_by_task: dict[uuid.UUID, list[TaskSource]] = {}
    for row in source_rows:
        source_by_task.setdefault(row.task_id, []).append(row)

    segment_rows = (
        await db.execute(
            select(Segment, TaskSource)
            .join(
                TaskSource,
                and_(
                    TaskSource.user_id == Segment.user_id,
                    TaskSource.transcript_id == Segment.transcript_id,
                    TaskSource.segment_id == Segment.id,
                ),
            )
            .where(TaskSource.user_id == user_id, TaskSource.task_id.in_(task_ids))
        )
    ).all()
    evidence_by_task: dict[uuid.UUID, list[tuple[int, int, TaskEvidenceOut]]] = {}
    segment_ordinals: dict[uuid.UUID, int] = {}
    for segment, source in segment_rows:
        segment_ordinals[segment.id] = segment.ordinal
        if source.char_start < 0 or source.char_end > len(segment.text):
            continue
        quote = segment.text[source.char_start : source.char_end]
        if len(quote) != source.char_end - source.char_start:
            continue
        evidence_by_task.setdefault(source.task_id, []).append(
            (
                segment.ordinal,
                source.char_start,
                TaskEvidenceOut(
                    segment_id=str(segment.id),
                    char_start=source.char_start,
                    char_end=source.char_end,
                    quote=quote,
                    t_start=segment.t_start,
                    t_end=segment.t_end,
                ),
            )
        )

    revisions = (
        await db.scalars(
            select(TaskRevision)
            .where(
                TaskRevision.user_id == user_id,
                TaskRevision.task_id.in_(task_ids),
                TaskRevision.status == "pending_confirmation",
            )
            .order_by(TaskRevision.created_at.desc(), TaskRevision.id.desc())
        )
    ).all()
    pending_by_task: dict[uuid.UUID, TaskRevision] = {}
    for revision in revisions:
        pending_by_task.setdefault(revision.task_id, revision)

    out: list[TaskOut] = []
    for task in tasks:
        evidence = sorted(
            evidence_by_task.get(task.id, []),
            key=lambda item: (item[0], item[1]),
        )
        out.append(
            _task_out(
                task,
                subject=(
                    subjects.get(task.subject_id)
                    if task.subject_id is not None
                    else None
                ),
                sources=source_by_task.get(task.id, []),
                evidence=[item[2] for item in evidence],
                pending=pending_by_task.get(task.id),
                segment_ordinals=segment_ordinals,
            )
        )
    return out


async def list_tasks(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    status: str | None = None,
    subject_id: uuid.UUID | None = None,
    due_from: date | None = None,
    due_to: date | None = None,
    limit: int = _DEFAULT_LIMIT,
    cursor: str | None = None,
) -> TaskListOut:
    _validate_status(status)
    if limit < 1 or limit > _MAX_LIMIT:
        raise validation_failed({"limit": f"debe estar entre 1 y {_MAX_LIMIT}"})
    stmt = select(Task).where(Task.user_id == user_id)
    if status is not None:
        stmt = stmt.where(Task.status == status)
    if subject_id is not None:
        stmt = stmt.where(Task.subject_id == subject_id)
    stmt = _due_conditions(stmt, due_from, due_to)
    if cursor is not None:
        position = _decode_cursor(cursor)
        stmt = stmt.where(
            or_(
                Task.created_at < position.created_at,
                and_(
                    Task.created_at == position.created_at,
                    Task.id < position.task_id,
                ),
            )
        )
    stmt = stmt.order_by(Task.created_at.desc(), Task.id.desc()).limit(limit + 1)
    rows = list((await db.scalars(stmt)).all())
    has_more = len(rows) > limit
    page = rows[:limit]
    items = await _assemble(db, user_id, page)
    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = _encode_cursor(TaskCursor(last.created_at, last.id))
    return TaskListOut(items=items, next_cursor=next_cursor)


async def get_task(db: AsyncSession, user_id: uuid.UUID, task_id: uuid.UUID) -> TaskOut:
    task = cast(
        Task | None,
        await db.scalar(
            select(Task).where(Task.user_id == user_id, Task.id == task_id)
        ),
    )
    if task is None:
        raise not_found()
    return (await _assemble(db, user_id, [task]))[0]
_PATCH_STATUSES = frozenset({"needs_review", "pending", "confirmed"})
_DUE_FIELDS = frozenset({"due_date", "due_at", "date_status", "all_day", "timezone"})


def _patch_value(patch: TaskPatch, field: str, current: Any) -> Any:
    return getattr(patch, field) if field in patch.model_fields_set else current


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    return value


def _block_values(
    task: Task, patch: TaskPatch
) -> tuple[datetime | None, datetime | None, date | None, date | None]:
    if "event_block" not in patch.model_fields_set:
        return (
            task.event_start_at,
            task.event_end_at,
            task.event_start_date,
            task.event_end_date_exclusive,
        )
    block = patch.event_block or TaskEventBlockPatch()
    return block.start_at, block.end_at, block.start_date, block.end_date_exclusive


def _validate_patch(
    task: Task, patch: TaskPatch
) -> dict[str, Any]:
    changes = patch.model_fields_set - {"version"}
    if not changes:
        raise validation_failed({"fields": "envía al menos un campo para editar"})

    tipo = _patch_value(patch, "tipo", task.tipo)
    titulo = _patch_value(patch, "titulo", task.titulo)
    detalle = _patch_value(patch, "detalle", task.detalle)
    date_status = _patch_value(patch, "date_status", task.date_status)
    due_date = _patch_value(patch, "due_date", task.due_date)
    due_at = _patch_value(patch, "due_at", task.due_at)
    date_text = _patch_value(patch, "date_text", task.date_text)
    date_reason = _patch_value(patch, "date_reason", task.date_reason)
    confidence_score = _patch_value(patch, "confidence_score", task.confidence_score)
    timezone = _patch_value(patch, "timezone", task.timezone)
    all_day = _patch_value(patch, "all_day", task.all_day)
    start_at, end_at, start_date, end_date = _block_values(task, patch)

    if tipo not in ACTIVITY_TYPES:
        raise validation_failed({"tipo": "tipo de actividad no válido"})
    if not isinstance(titulo, str) or titulo.strip() == "":
        raise validation_failed({"titulo": "el título no puede estar vacío"})
    if not isinstance(timezone, str) or not is_iana_timezone(timezone):
        raise validation_failed({"timezone": "zona horaria IANA no válida"})
    if not isinstance(all_day, bool):
        raise validation_failed({"all_day": "all_day debe ser booleano"})
    if date_status not in DATE_STATUSES:
        raise validation_failed({"date_status": "estado de fecha no válido"})

    if date_status == "resolved":
        if (due_date is None) == (due_at is None):
            raise validation_failed(
                {"due_date": "resolved exige exactamente due_date o due_at"}
            )
        if all_day != (due_date is not None):
            raise validation_failed(
                {"all_day": "all_day debe coincidir con la representación de la fecha"}
            )
        if due_at is not None and due_at.tzinfo is None:
            raise validation_failed({"due_at": "due_at debe incluir zona horaria"})
    elif due_date is not None or due_at is not None:
        raise validation_failed(
            {"date_status": "ambiguous/missing no admiten due_date ni due_at"}
        )

    timed = start_at is not None or end_at is not None
    all_day_block = start_date is not None or end_date is not None
    if timed and all_day_block:
        raise validation_failed({"event_block": "no mezcles bloque con hora y todo el día"})
    if timed:
        if start_at is None or end_at is None:
            raise validation_failed({"event_block": "el bloque con hora necesita inicio y fin"})
        if start_at.tzinfo is None or end_at.tzinfo is None:
            raise validation_failed({"event_block": "el bloque con hora debe incluir zona"})
        if end_at <= start_at:
            raise validation_failed({"event_block": "el fin debe ser posterior al inicio"})
    elif all_day_block:
        if start_date is None or end_date is None:
            raise validation_failed({"event_block": "el bloque de día necesita inicio y fin"})
        if end_date <= start_date:
            raise validation_failed({"event_block": "el fin debe ser posterior al inicio"})

    return {
        "tipo": tipo,
        "titulo": titulo,
        "detalle": detalle,
        "date_status": date_status,
        "due_date": due_date,
        "due_at": due_at,
        "date_text": date_text,
        "date_reason": date_reason,
        "confidence_score": confidence_score,
        "timezone": timezone,
        "all_day": all_day,
        "start_at": start_at,
        "end_at": end_at,
        "start_date": start_date,
        "end_date": end_date,
    }


def _proposed_fields(patch: TaskPatch, values: dict[str, Any]) -> dict[str, Any]:
    proposed: dict[str, Any] = {}
    for field in patch.model_fields_set - {"version"}:
        if field == "event_block":
            block = patch.event_block or TaskEventBlockPatch()
            proposed[field] = {
                "start_at": _json_value(block.start_at),
                "end_at": _json_value(block.end_at),
                "start_date": _json_value(block.start_date),
                "end_date_exclusive": _json_value(block.end_date_exclusive),
            }
        else:
            proposed[field] = _json_value(values.get(field, getattr(patch, field)))
    return proposed


async def patch_task(
    db: AsyncSession, user_id: uuid.UUID, task_id: uuid.UUID, patch: TaskPatch
) -> TaskUpdateOut:
    task = cast(
        Task | None,
        await db.scalar(
            select(Task)
            .where(Task.user_id == user_id, Task.id == task_id)
            .with_for_update()
        ),
    )
    if task is None:
        raise not_found()
    if task.version != patch.version:
        raise ApiError(
            409,
            "version_conflict",
            "La tarea cambió desde que la cargaste; recarga antes de continuar.",
            {"current_version": task.version, "current_status": task.status},
        )
    if task.status not in _PATCH_STATUSES:
        raise ApiError(
            409,
            "invalid_transition",
            "Esta tarea ya no se puede editar en su estado actual.",
            {"current_status": task.status, "attempted_status": task.status},
        )

    values = _validate_patch(task, patch)
    if task.status == "confirmed":
        revision = TaskRevision(
            user_id=user_id,
            task_id=task.id,
            base_task_version=task.version,
            revision_type="edit_of_confirmed",
            proposed_fields=_proposed_fields(patch, values),
            evidence_validated=True,
            status="pending_confirmation",
        )
        db.add(revision)
        await db.flush()
        updated = await _assemble(db, user_id, [task])
        return TaskUpdateOut(task=updated[0], revision_id=str(revision.id))

    changes = patch.model_fields_set - {"version"}
    task.tipo = values["tipo"]
    task.titulo = values["titulo"]
    task.detalle = values["detalle"]
    task.date_status = values["date_status"]
    task.due_date = values["due_date"]
    task.due_at = values["due_at"]
    task.date_text = values["date_text"]
    task.date_reason = values["date_reason"]
    task.confidence_score = values["confidence_score"]
    task.timezone = values["timezone"]
    task.all_day = values["all_day"]
    task.event_start_at = values["start_at"]
    task.event_end_at = values["end_at"]
    task.event_start_date = values["start_date"]
    task.event_end_date_exclusive = values["end_date"]
    task.version += 1
    if changes & _DUE_FIELDS:
        task.due_version += 1
    await db.flush()
    updated = await _assemble(db, user_id, [task])
    return TaskUpdateOut(task=updated[0], revision_id=None)

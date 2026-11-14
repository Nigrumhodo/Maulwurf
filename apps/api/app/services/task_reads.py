"""M4 task reads: keyset listing and detail (A3.3).

The active `tasks` row is the only source of truth. Evidence is rebuilt from
`task_sources`, `task_evidence` and the immutable segment text, never from a quote
sent by a client. Writes in `services.tasks` use `_load_task` and `_assemble` so a
mutation returns the same shape as a read.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import not_found, validation_failed
from app.models import Segment, Subject, Task, TaskRevision, TaskSource
from app.models.task import TASK_STATUSES
from app.schemas.tasks import (
    PendingRevisionOut,
    TaskEventBlockOut,
    TaskEvidenceOut,
    TaskListOut,
    TaskOut,
    TaskSourceOut,
    TaskSubjectOut,
)
from app.services.task_evidence import load_task_evidence_batch

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
        raise validation_failed({"status": f"debe ser uno de: {', '.join(TASK_STATUSES)}"})


def _task_local_day(column: Any) -> Any:
    """The task's local calendar day, using its persisted IANA timezone snapshot."""
    return func.date(func.timezone(Task.timezone, column))


def _due_conditions(stmt: Any, due_from: date | None, due_to: date | None) -> Any:
    if due_from is not None:
        stmt = stmt.where(
            or_(Task.due_date >= due_from, _task_local_day(Task.due_at) >= due_from)
        )
    if due_to is not None:
        stmt = stmt.where(or_(Task.due_date <= due_to, _task_local_day(Task.due_at) <= due_to))
    return stmt


def _event_block(task: Task) -> TaskEventBlockOut:
    return TaskEventBlockOut(
        start_at=task.event_start_at,
        end_at=task.event_end_at,
        start_date=task.event_start_date,
        end_date_exclusive=task.event_end_date_exclusive,
        duration_default_editable=True,
    )


def _pending_revision_out(revision: TaskRevision | None) -> PendingRevisionOut | None:
    if revision is None:
        return None
    return PendingRevisionOut(
        id=str(revision.id),
        base_task_version=revision.base_task_version,
        proposed_fields=dict(revision.proposed_fields),
        status=revision.status,
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
        pending_revision=_pending_revision_out(pending),
    )


async def _assemble(db: AsyncSession, user_id: uuid.UUID, tasks: list[Task]) -> list[TaskOut]:
    if not tasks:
        return []
    task_ids = [task.id for task in tasks]
    subject_ids = {task.subject_id for task in tasks if task.subject_id is not None}

    subjects = {
        subject.id: subject
        for subject in (
            await db.scalars(
                select(Subject).where(Subject.user_id == user_id, Subject.id.in_(subject_ids))
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

    segment_ordinals = {
        segment_id: ordinal
        for segment_id, ordinal in (
            await db.execute(
                select(Segment.id, Segment.ordinal).where(
                    Segment.user_id == user_id,
                    Segment.id.in_({row.segment_id for row in source_rows}),
                )
            )
        ).all()
    }

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

    stored_by_task = await load_task_evidence_batch(
        db,
        user_id=user_id,
        task_versions={task.id: task.version for task in tasks},
    )
    out: list[TaskOut] = []
    for task in tasks:
        stored = stored_by_task.get(task.id, ())
        evidence = [
            TaskEvidenceOut(
                segment_id=str(item.evidence.segment_id),
                char_start=item.evidence.inicio_char,
                char_end=item.evidence.fin_char,
                quote=item.evidence.quote,
                t_start=item.evidence.t_start,
                t_end=item.evidence.t_end,
            )
            for item in stored
        ]
        out.append(
            _task_out(
                task,
                subject=(
                    subjects.get(task.subject_id) if task.subject_id is not None else None
                ),
                sources=source_by_task.get(task.id, []),
                evidence=evidence,
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
    if due_from is not None and due_to is not None and due_from > due_to:
        raise validation_failed({"due_from": "no puede ser posterior a due_to"})

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
    task = await _load_task(db, user_id, task_id)
    return (await _assemble(db, user_id, [task]))[0]


async def _load_task(
    db: AsyncSession, user_id: uuid.UUID, task_id: uuid.UUID, *, for_update: bool = False
) -> Task:
    # `populate_existing` keeps a lifecycle bulk update from being hidden by an instance
    # already in the identity map (the request session may have loaded it).
    stmt = (
        select(Task)
        .where(Task.user_id == user_id, Task.id == task_id)
        .execution_options(populate_existing=True)
    )
    if for_update:
        stmt = stmt.with_for_update()
    task = (await db.execute(stmt)).scalar_one_or_none()
    if task is None:
        raise not_found()
    return task

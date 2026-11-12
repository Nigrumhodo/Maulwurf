"""M4 task endpoints: reads, proposals, edits, confirmation and lifecycle (A3.3/A3.4).

The active `tasks` row is the only source of truth. Reads reconstruct evidence from
`task_sources` + `task_evidence` + the immutable segment text (A3.2), never from a quote
sent by a client. Writes are tenant-scoped and use `version` as the optimistic-locking
token:

- `POST /tasks` accepts `manual` (zero sources), `extraction` (at least one validated
  source) and `chat` (zero or more; zero is an honest general suggestion). Every span is
  validated against the persisted corpus before the task is published.
- `PATCH` edits `pending`/`needs_review` tasks in place and turns an edit of a `confirmed`
  task into a `pending_confirmation` revision, without touching the active columns or
  Google. `completed`/`dismissed` are terminal.
- `confirm` validates the resolved date, the Calendar block and the evidence, promotes at
  most one revision and bumps `version` in the same transaction that creates or reuses one
  `sync_task_event` outbox row. Without a ready Calendar integration the task is still
  confirmed but stays `calendar_sync_status='blocked'` and no event is written; a later
  reconnect (J3.3) or a repeat confirmation resumes it.
- `dismiss`/`complete` reuse the J3.6 lifecycle adapter, cancel unsent reminders and keep
  the linked Calendar event with a notice.

Every effective version bump copies the active evidence rows to the new task version, so
`load_task_evidence` keeps finding the quote for the active version.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError, not_found, validation_failed
from app.core.validation import is_iana_timezone
from app.models import (
    Audio,
    CalendarSettings,
    GoogleCredential,
    OutboxEvent,
    Segment,
    Subject,
    Task,
    TaskEvidence,
    TaskRevision,
    TaskSource,
    Transcript,
)
from app.models.task import TASK_STATUSES
from app.schemas.analysis import DateStatus
from app.schemas.tasks import (
    PendingRevisionOut,
    TaskCreate,
    TaskEventBlockIn,
    TaskEventBlockOut,
    TaskEvidenceOut,
    TaskListOut,
    TaskOut,
    TaskPatch,
    TaskSourceOut,
    TaskSubjectOut,
    validate_date_fields,
    validate_event_block,
)
from app.services.analysis_windows import EvidenceSpan
from app.services.date_resolution import render_due_text
from app.services.oauth_google import missing_calendar_scopes
from app.services.outbox import SYNC_TASK_EVENT
from app.services.revisions import (
    discard_pending_revisions,
    record_pending_revision,
)
from app.services.task_evidence import (
    ProvenanceInvalid,
    load_task_evidence,
    load_task_evidence_batch,
    persist_task_spans,
    validate_provenance,
)
from app.services.task_lifecycle import TaskLifecycle, TaskLifecycleResult
from app.services.task_lifecycle_store import SqlTaskLifecycleStore

TASK_CURSOR_VERSION = 1
_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200
_MAX_CURSOR_LENGTH = 512
_CURSOR_INVALID = "cursor inválido o malformado"
CALENDAR_SYNC_BLOCKED_NOTICE = "calendar_sync_blocked"

_DATE_KEYS = frozenset({"due_date", "event_start_date", "event_end_date_exclusive"})
_DATETIME_KEYS = frozenset({"due_at", "event_start_at", "event_end_at"})
_REVISION_KEYS = frozenset(
    {
        "tipo",
        "titulo",
        "detalle",
        "due_date",
        "due_at",
        "timezone",
        "all_day",
        "date_status",
        "date_reason",
        "confidence_score",
        "subject_id",
        "event_start_at",
        "event_end_at",
        "event_start_date",
        "event_end_date_exclusive",
    }
)
_FIELD_KEYS = tuple(_REVISION_KEYS | {"date_text"})


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


def ensure_version(current_version: int, current_status: str, expected_version: int) -> None:
    """The single optimistic-locking decision shared by PATCH/confirm/lifecycle."""
    if current_version != expected_version:
        raise ApiError(
            409,
            "version_conflict",
            "La tarea cambió desde que la cargaste; recarga antes de continuar.",
            {"current_version": current_version, "current_status": current_status},
        )


def _invalid_transition(task: Task, target: str) -> ApiError:
    return ApiError(
        409,
        "invalid_transition",
        "La operación no es válida para el estado actual de esta tarea.",
        {"current_status": task.status, "attempted_status": target},
    )


def _evidence_invalid() -> ApiError:
    return ApiError(
        422,
        "evidence_invalid",
        "La evidencia no se puede reconstruir desde el corpus de la clase.",
        {"field": "sources"},
    )


def _invalid_revision() -> ApiError:
    return ApiError(
        422,
        "validation_failed",
        "La revisión propuesta no es válida.",
        {"field": "proposed_fields"},
    )


def _date_unresolved(reason: str) -> ApiError:
    return ApiError(
        422,
        "date_unresolved",
        "Confirma una fecha resuelta y un bloque de agenda válido antes de continuar.",
        {"reason": reason},
    )


def _same_instant(left: datetime | None, right: datetime | None) -> bool:
    if left is None or right is None:
        return left is right
    return left.astimezone(UTC) == right.astimezone(UTC)


def _block_values(block: TaskEventBlockIn | None) -> dict[str, Any]:
    if block is None:
        return {
            "event_start_at": None,
            "event_end_at": None,
            "event_start_date": None,
            "event_end_date_exclusive": None,
        }
    return {
        "event_start_at": block.start_at,
        "event_end_at": block.end_at,
        "event_start_date": block.start_date,
        "event_end_date_exclusive": block.end_date_exclusive,
    }


def _current_fields(task: Task) -> dict[str, Any]:
    return {
        "tipo": task.tipo,
        "titulo": task.titulo,
        "detalle": task.detalle,
        "due_date": task.due_date,
        "due_at": task.due_at,
        "timezone": task.timezone,
        "all_day": task.all_day,
        "date_status": task.date_status,
        "date_text": task.date_text,
        "date_reason": task.date_reason,
        "confidence_score": task.confidence_score,
        "subject_id": task.subject_id,
        "event_start_at": task.event_start_at,
        "event_end_at": task.event_end_at,
        "event_start_date": task.event_start_date,
        "event_end_date_exclusive": task.event_end_date_exclusive,
    }


def _fields_equal(left: dict[str, Any], right: dict[str, Any]) -> bool:
    for key in _FIELD_KEYS:
        if key in {"due_at", "event_start_at", "event_end_at"}:
            if not _same_instant(left[key], right[key]):
                return False
        elif left[key] != right[key]:
            return False
    return True


def _due_changed(before: dict[str, Any], after: dict[str, Any]) -> bool:
    return (
        before["due_date"] != after["due_date"]
        or not _same_instant(before["due_at"], after["due_at"])
        or before["timezone"] != after["timezone"]
        or before["all_day"] != after["all_day"]
        or before["date_status"] != after["date_status"]
    )


def _render_date_text(fields: dict[str, Any]) -> str | None:
    return render_due_text(
        due_date=fields["due_date"],
        due_at=fields["due_at"],
        timezone=fields["timezone"],
        all_day=fields["all_day"],
        date_status=fields["date_status"],
    )


def _validate_fields(fields: dict[str, Any]) -> None:
    try:
        validate_date_fields(
            due_date=fields["due_date"],
            due_at=fields["due_at"],
            timezone=fields["timezone"],
            all_day=fields["all_day"],
            date_status=DateStatus(fields["date_status"]),
        )
    except ValueError as exc:
        raise validation_failed({"date_status": str(exc)}) from exc
    try:
        validate_event_block(
            start_at=fields["event_start_at"],
            end_at=fields["event_end_at"],
            start_date=fields["event_start_date"],
            end_date_exclusive=fields["event_end_date_exclusive"],
        )
    except ValueError as exc:
        raise validation_failed({"event_block": str(exc)}) from exc
    if not is_iana_timezone(fields["timezone"]):
        raise validation_failed({"timezone": "zona horaria IANA no válida"})


async def _validate_subject(
    db: AsyncSession, user_id: uuid.UUID, subject_id: uuid.UUID | None
) -> None:
    if subject_id is None:
        return
    found = await db.scalar(
        select(Subject.id).where(
            Subject.user_id == user_id,
            Subject.id == subject_id,
            Subject.deleted_at.is_(None),
        )
    )
    if found is None:
        raise validation_failed({"subject_id": "la materia no existe o está eliminada"})


def _apply_fields(task: Task, fields: dict[str, Any]) -> None:
    for key in _REVISION_KEYS:
        setattr(task, key, fields[key])
    task.date_text = _render_date_text(fields)


def _iso_date(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, date) else None


def _iso_datetime(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _revision_payload(fields: dict[str, Any]) -> dict[str, Any]:
    subject_id = fields["subject_id"]
    return {
        "tipo": fields["tipo"],
        "titulo": fields["titulo"],
        "detalle": fields["detalle"],
        "due_date": _iso_date(fields["due_date"]),
        "due_at": _iso_datetime(fields["due_at"]),
        "timezone": fields["timezone"],
        "all_day": fields["all_day"],
        "date_status": fields["date_status"],
        "date_text": _render_date_text(fields),
        "date_reason": fields["date_reason"],
        "confidence_score": fields["confidence_score"],
        "subject_id": str(subject_id) if subject_id is not None else None,
        "event_start_at": _iso_datetime(fields["event_start_at"]),
        "event_end_at": _iso_datetime(fields["event_end_at"]),
        "event_start_date": _iso_date(fields["event_start_date"]),
        "event_end_date_exclusive": _iso_date(fields["event_end_date_exclusive"]),
    }


def _coerce_revision(payload: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for key in _REVISION_KEYS:
        if key not in payload:
            continue
        value = payload[key]
        try:
            if key in _DATE_KEYS and isinstance(value, str):
                value = date.fromisoformat(value)
            elif key in _DATETIME_KEYS and isinstance(value, str):
                value = datetime.fromisoformat(value)
            elif key == "subject_id" and isinstance(value, str):
                value = uuid.UUID(value)
        except ValueError as exc:
            raise _invalid_revision() from exc
        fields[key] = value
    return fields


def _merge_patch(before: dict[str, Any], body: TaskPatch) -> dict[str, Any]:
    merged = dict(before)
    fields_set = body.model_fields_set - {"version"}

    if "event_block" in fields_set:
        merged.update(_block_values(body.event_block))
    for key in ("tipo", "titulo", "detalle", "date_reason", "confidence_score", "subject_id"):
        if key in fields_set:
            merged[key] = getattr(body, key)
    if "due_date" in fields_set:
        merged["due_date"] = body.due_date
    if "due_at" in fields_set:
        merged["due_at"] = body.due_at
    if "timezone" in fields_set:
        merged["timezone"] = body.timezone
    if "all_day" in fields_set:
        merged["all_day"] = body.all_day

    if "date_status" in fields_set:
        if body.date_status is None:  # pragma: no cover - the schema rejects this
            raise validation_failed({"date_status": "date_status no puede ser nulo"})
        merged["date_status"] = body.date_status.value
    elif "due_date" in fields_set or "due_at" in fields_set:
        # A partial date edit carries the implied resolution: one due representation means
        # resolved, clearing both means the date is missing again.
        if merged["due_date"] is not None and merged["due_at"] is None:
            merged["date_status"] = DateStatus.RESOLVED.value
            merged["all_day"] = True
        elif merged["due_at"] is not None and merged["due_date"] is None:
            merged["date_status"] = DateStatus.RESOLVED.value
            merged["all_day"] = False
        else:
            merged["date_status"] = DateStatus.MISSING.value
            merged["all_day"] = False

    merged["date_text"] = _render_date_text(merged)
    return merged


async def _copy_evidence_version(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    task_id: uuid.UUID,
    from_version: int,
    to_version: int,
) -> None:
    """Keep the active version's evidence readable after a version bump."""
    if from_version == to_version:
        return
    already = await db.scalar(
        select(func.count())
        .select_from(TaskEvidence)
        .where(
            TaskEvidence.user_id == user_id,
            TaskEvidence.task_id == task_id,
            TaskEvidence.task_version == to_version,
        )
    )
    if already:
        return
    rows = (
        await db.scalars(
            select(TaskEvidence).where(
                TaskEvidence.user_id == user_id,
                TaskEvidence.task_id == task_id,
                TaskEvidence.task_version == from_version,
            )
        )
    ).all()
    for row in rows:
        db.add(
            TaskEvidence(
                user_id=row.user_id,
                task_id=row.task_id,
                task_version=to_version,
                segment_id=row.segment_id,
                char_start=row.char_start,
                char_end=row.char_end,
                quote=row.quote,
            )
        )
    await db.flush()


async def _resolve_sources(
    db: AsyncSession, user_id: uuid.UUID, spans: list[EvidenceSpan]
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, int]:
    """Resolve the one class/transcript/version a sourced proposal belongs to."""
    segment_ids = {span.segment_id for span in spans}
    rows = list(
        (
            await db.execute(
                select(
                    Segment.transcript_id,
                    Transcript.version,
                    Transcript.audio_id,
                    Audio.subject_id,
                )
                .join(
                    Transcript,
                    and_(
                        Transcript.user_id == Segment.user_id,
                        Transcript.id == Segment.transcript_id,
                    ),
                )
                .join(
                    Audio,
                    and_(
                        Audio.user_id == Transcript.user_id,
                        Audio.id == Transcript.audio_id,
                        Audio.deleted_at.is_(None),
                    ),
                )
                .where(Segment.user_id == user_id, Segment.id.in_(segment_ids))
            )
        )
        .tuples()
        .all()
    )
    if len(rows) != len(segment_ids):
        raise _evidence_invalid()
    audio_ids = {row[2] for row in rows}
    transcript_ids = {row[0] for row in rows}
    versions = {row[1] for row in rows}
    if len(audio_ids) != 1 or len(transcript_ids) != 1 or len(versions) != 1:
        raise _evidence_invalid()
    transcript_id, version, audio_id, subject_id = rows[0]
    return audio_id, subject_id, transcript_id, version


async def create_task(db: AsyncSession, user_id: uuid.UUID, body: TaskCreate) -> TaskOut:
    spans = [
        EvidenceSpan(
            segment_id=source.segment_id,
            char_start=source.char_start,
            char_end=source.char_end,
            quote=source.quote,
        )
        for source in body.sources
    ]
    try:
        validate_provenance(body.source, spans)
    except ProvenanceInvalid as exc:
        raise _evidence_invalid() from exc

    audio_id = body.audio_id
    subject_id = body.subject_id
    transcript_id: uuid.UUID | None = None
    transcript_version: int | None = None
    if spans:
        derived_audio, derived_subject, transcript_id, transcript_version = (
            await _resolve_sources(db, user_id, spans)
        )
        if audio_id is not None and audio_id != derived_audio:
            raise validation_failed(
                {"audio_id": "no coincide con la clase de la evidencia citada"}
            )
        if subject_id is not None and subject_id != derived_subject:
            raise validation_failed(
                {"subject_id": "no coincide con la materia de la clase citada"}
            )
        audio_id = derived_audio
        subject_id = derived_subject
    else:
        await _validate_subject(db, user_id, subject_id)

    fields: dict[str, Any] = {
        "tipo": body.tipo,
        "titulo": body.titulo,
        "detalle": body.detalle,
        "due_date": body.due_date,
        "due_at": body.due_at,
        "timezone": body.timezone,
        "all_day": body.all_day,
        "date_status": body.date_status.value,
        "date_reason": body.date_reason,
        "confidence_score": body.confidence_score,
        "subject_id": subject_id,
        **_block_values(body.event_block),
    }
    _validate_fields(fields)
    fields["date_text"] = _render_date_text(fields)

    task = Task(
        user_id=user_id,
        audio_id=audio_id,
        subject_id=subject_id,
        source=body.source,
        tipo=fields["tipo"],
        titulo=fields["titulo"],
        detalle=fields["detalle"],
        due_date=fields["due_date"],
        due_at=fields["due_at"],
        timezone=fields["timezone"],
        all_day=fields["all_day"],
        date_status=fields["date_status"],
        date_text=fields["date_text"],
        date_reason=fields["date_reason"],
        confidence_score=fields["confidence_score"],
        event_start_at=fields["event_start_at"],
        event_end_at=fields["event_end_at"],
        event_start_date=fields["event_start_date"],
        event_end_date_exclusive=fields["event_end_date_exclusive"],
        status="needs_review",
        calendar_sync_status="not_requested",
    )
    try:
        # The task is flushed before its spans so `task_sources` has a task id; the
        # savepoint keeps a late provenance rejection from leaving that row behind.
        async with db.begin_nested():
            db.add(task)
            await db.flush()
            if spans:
                assert audio_id is not None  # noqa: S101 - guaranteed by the branch
                assert transcript_id is not None  # noqa: S101 - returned with the spans
                assert transcript_version is not None  # noqa: S101 - returned with the spans
                await persist_task_spans(
                    db,
                    task=task,
                    audio_id=audio_id,
                    transcript_id=transcript_id,
                    transcript_version=transcript_version,
                    spans=spans,
                )
    except ProvenanceInvalid as exc:
        raise _evidence_invalid() from exc
    await db.flush()
    return (await _assemble(db, user_id, [task]))[0]


async def _has_persisted_provenance(
    db: AsyncSession, user_id: uuid.UUID, task_id: uuid.UUID
) -> bool:
    """Whether a source or a quote pins the task to a class/subject."""
    return bool(
        await db.scalar(
            select(
                or_(
                    exists().where(
                        TaskSource.user_id == user_id,
                        TaskSource.task_id == task_id,
                    ),
                    exists().where(
                        TaskEvidence.user_id == user_id,
                        TaskEvidence.task_id == task_id,
                    ),
                )
            )
        )
    )


async def _ensure_subject_change_allowed(
    db: AsyncSession,
    user_id: uuid.UUID,
    task: Task,
    before: dict[str, Any],
    after: dict[str, Any],
) -> None:
    """A sourced task cannot be detached from the subject of its evidence."""
    if after["subject_id"] == before["subject_id"]:
        return
    if task.source == "extraction" or await _has_persisted_provenance(
        db, user_id, task.id
    ):
        raise validation_failed(
            {
                "subject_id": (
                    "La materia de una tarea con evidencia no puede modificarse "
                    "de forma independiente"
                )
            }
        )


async def patch_task(
    db: AsyncSession, user_id: uuid.UUID, task_id: uuid.UUID, body: TaskPatch
) -> TaskOut:
    task = await _load_task(db, user_id, task_id, for_update=True)
    ensure_version(task.version, task.status, body.version)
    if task.status in {"completed", "dismissed"}:
        raise _invalid_transition(task, "edited")

    before = _current_fields(task)
    after = _merge_patch(before, body)
    await _ensure_subject_change_allowed(db, user_id, task, before, after)
    await _validate_subject(db, user_id, after["subject_id"])
    _validate_fields(after)
    after["date_text"] = _render_date_text(after)
    if _fields_equal(before, after):
        return (await _assemble(db, user_id, [task]))[0]

    if task.status == "confirmed":
        # The revision service locks the same row and bases the proposal on its current
        # version, so a concurrent PATCH/confirm cannot leave an unconfirmable revision.
        await record_pending_revision(
            db,
            user_id=user_id,
            task_id=task.id,
            proposed_fields=_revision_payload(after),
            revision_type="edit_of_confirmed",
            evidence_validated=True,
        )
        return (await _assemble(db, user_id, [task]))[0]

    due_changed = _due_changed(before, after)
    old_version = task.version
    _apply_fields(task, after)
    task.version = old_version + 1
    if due_changed:
        task.due_version += 1
    await db.flush()
    await _copy_evidence_version(
        db,
        user_id=user_id,
        task_id=task.id,
        from_version=old_version,
        to_version=task.version,
    )
    return (await _assemble(db, user_id, [task]))[0]


async def _load_pending_revision(
    db: AsyncSession, user_id: uuid.UUID, task: Task, revision_id: uuid.UUID
) -> TaskRevision:
    revision = (
        await db.execute(
            select(TaskRevision)
            .where(
                TaskRevision.user_id == user_id,
                TaskRevision.task_id == task.id,
                TaskRevision.id == revision_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if revision is None:
        raise not_found()
    if revision.status != "pending_confirmation":
        raise ApiError(
            409,
            "version_conflict",
            "La revisión ya no está pendiente de confirmación.",
            {"current_version": task.version, "current_status": task.status},
        )
    return revision


async def _validate_confirmable(
    db: AsyncSession, user_id: uuid.UUID, task: Task, fields: dict[str, Any]
) -> None:
    if fields["date_status"] != DateStatus.RESOLVED.value:
        raise _date_unresolved("date_status")
    if all(
        fields[key] is None
        for key in (
            "event_start_at",
            "event_end_at",
            "event_start_date",
            "event_end_date_exclusive",
        )
    ):
        raise _date_unresolved("event_block")
    try:
        validate_event_block(
            start_at=fields["event_start_at"],
            end_at=fields["event_end_at"],
            start_date=fields["event_start_date"],
            end_date_exclusive=fields["event_end_date_exclusive"],
        )
    except ValueError as exc:
        raise _date_unresolved("event_block") from exc

    source_count = await db.scalar(
        select(func.count())
        .select_from(TaskSource)
        .where(TaskSource.user_id == user_id, TaskSource.task_id == task.id)
    )
    if task.source == "extraction" or (task.source == "chat" and source_count):
        try:
            evidence = await load_task_evidence(
                db, user_id=user_id, task_id=task.id, task_version=task.version
            )
        except ProvenanceInvalid as exc:
            raise _evidence_invalid() from exc
        if not evidence:
            raise _evidence_invalid()


def _calendar_target(value: str | None) -> str | None:
    """The stored write calendar, trimmed; blank means no target."""
    if value is None:
        return None
    return value.strip() or None


def _calendar_ready(
    credential: GoogleCredential | None, target_calendar_id: str | None
) -> bool:
    """Whether a confirmation may enqueue a Calendar write right now.

    A partial scope grant counts as "without Google": enqueuing would only produce a job
    that fails at the provider. The stored grant is not a live probe; a scope revoked at
    Google still fails in the worker (D3.7 owns that recovery).
    """
    if credential is None or credential.status != "connected":
        return False
    if _calendar_target(target_calendar_id) is None:
        return False
    return not missing_calendar_scopes(credential.scopes or [])


async def _calendar_state(
    db: AsyncSession, user_id: uuid.UUID
) -> tuple[str | None, bool]:
    """The target calendar to snapshot and whether a write can be enqueued now."""
    target = _calendar_target(
        await db.scalar(
            select(CalendarSettings.calendar_write_calendar_id).where(
                CalendarSettings.user_id == user_id
            )
        )
    )
    if target is None:
        return None, False
    credential = await db.scalar(
        select(GoogleCredential).where(GoogleCredential.user_id == user_id)
    )
    return target, _calendar_ready(credential, target)


def _confirmation_dedupe_key(*, version: int, due_version: int, cycle: int) -> str:
    """Deterministic per-cycle key; the unique constraint closes the double-click race."""
    return f"confirm:{version}:{due_version}:{cycle}"


async def _active_sync_event(
    db: AsyncSession, *, user_id: uuid.UUID, task_id: uuid.UUID, version: int
) -> uuid.UUID | None:
    found: uuid.UUID | None = await db.scalar(
        select(OutboxEvent.id)
        .where(
            OutboxEvent.user_id == user_id,
            OutboxEvent.type == SYNC_TASK_EVENT,
            OutboxEvent.resource_id == task_id,
            OutboxEvent.resource_version == version,
            OutboxEvent.status.in_(("pending", "dispatched")),
        )
        .order_by(OutboxEvent.created_at.desc(), OutboxEvent.id.desc())
        .limit(1)
    )
    return found


async def _sync_event_cycle(
    db: AsyncSession, *, user_id: uuid.UUID, task_id: uuid.UUID
) -> int:
    count = await db.scalar(
        select(func.count())
        .select_from(OutboxEvent)
        .where(
            OutboxEvent.user_id == user_id,
            OutboxEvent.type == SYNC_TASK_EVENT,
            OutboxEvent.resource_id == task_id,
        )
    )
    return int(count or 0)


async def _ensure_confirmation_outbox(
    db: AsyncSession, *, user_id: uuid.UUID, task: Task
) -> uuid.UUID:
    """Reuse the live event for this task version, or insert the next cycle.

    Runs in the request transaction: the router's single commit publishes the task, its
    revision/evidence copy and this row together. `ON CONFLICT` plus the unique constraint
    closes the race between two confirmations that passed the same read.
    """
    existing = await _active_sync_event(
        db, user_id=user_id, task_id=task.id, version=task.version
    )
    if existing is not None:
        return existing
    cycle = await _sync_event_cycle(db, user_id=user_id, task_id=task.id)
    inserted = await db.execute(
        pg_insert(OutboxEvent)
        .values(
            user_id=user_id,
            type=SYNC_TASK_EVENT,
            resource_type="task",
            resource_id=task.id,
            resource_version=task.version,
            payload={},
            # Not cleanup-gated: the CHECK requires enabled events without a blocked reason.
            enabled=True,
            blocked_reason=None,
            dedupe_key=_confirmation_dedupe_key(
                version=task.version, due_version=task.due_version, cycle=cycle
            ),
        )
        .on_conflict_do_nothing(
            index_elements=["type", "resource_id", "resource_version", "dedupe_key"]
        )
        .returning(OutboxEvent.id)
    )
    event_id = inserted.scalar_one_or_none()
    if event_id is not None:
        return event_id
    existing = await _active_sync_event(
        db, user_id=user_id, task_id=task.id, version=task.version
    )
    if existing is not None:
        return existing
    raise RuntimeError("sync_task_event could not be created or reused")


async def confirm_task(
    db: AsyncSession,
    user_id: uuid.UUID,
    task_id: uuid.UUID,
    *,
    expected_version: int,
    revision_id: uuid.UUID | None = None,
) -> tuple[TaskOut, str | None]:
    task = await _load_task(db, user_id, task_id, for_update=True)
    ensure_version(task.version, task.status, expected_version)
    if task.status in {"completed", "dismissed"}:
        raise _invalid_transition(task, "confirmed")

    now = datetime.now(UTC)
    revision = (
        await _load_pending_revision(db, user_id, task, revision_id)
        if revision_id is not None
        else None
    )
    before = _current_fields(task)
    if revision is not None:
        ensure_version(task.version, task.status, revision.base_task_version)
        proposed = _coerce_revision(dict(revision.proposed_fields))
        after = dict(before)
        after.update(proposed)
        await _ensure_subject_change_allowed(db, user_id, task, before, after)
        await _validate_subject(db, user_id, after["subject_id"])
        _validate_fields(after)
        after["date_text"] = _render_date_text(after)
    else:
        after = before

    target_calendar_id, calendar_ready = await _calendar_state(db, user_id)

    # A repeat without a revision is a safe double click. It never bumps the version; it
    # may still resume a blocked confirmation once the integration is ready (first connect
    # or a reconnect that has not run J3.3 yet).
    if revision is None and task.status == "confirmed":
        if task.calendar_sync_status == "blocked" and calendar_ready:
            task.calendar_sync_status = "pending"
            if target_calendar_id is not None:
                task.calendar_id = target_calendar_id
            await db.flush()
            await _ensure_confirmation_outbox(db, user_id=user_id, task=task)
            return (await _assemble(db, user_id, [task]))[0], None
        notice = (
            CALENDAR_SYNC_BLOCKED_NOTICE
            if task.calendar_sync_status == "blocked"
            else None
        )
        return (await _assemble(db, user_id, [task]))[0], notice

    await _validate_confirmable(db, user_id, task, after)

    due_changed = _due_changed(before, after)
    old_version = task.version
    _apply_fields(task, after)
    task.version = old_version + 1
    if due_changed:
        task.due_version += 1
    task.status = "confirmed"
    task.confirmed_at = task.confirmed_at or now
    # Keep the target even while blocked: J3.4 blocks with `calendar_missing` without it,
    # and J3.3 needs the ID to resume the sync after a reconnect. Never clear an old link.
    if target_calendar_id is not None:
        task.calendar_id = target_calendar_id
    task.calendar_sync_status = "pending" if calendar_ready else "blocked"
    if revision is not None:
        revision.status = "activated"
        revision.activated_at = now
    await db.flush()
    await discard_pending_revisions(db, user_id, task.id)
    await _copy_evidence_version(
        db,
        user_id=user_id,
        task_id=task.id,
        from_version=old_version,
        to_version=task.version,
    )
    if calendar_ready:
        await _ensure_confirmation_outbox(db, user_id=user_id, task=task)
    notice = None if calendar_ready else CALENDAR_SYNC_BLOCKED_NOTICE
    return (await _assemble(db, user_id, [task]))[0], notice


@asynccontextmanager
async def _request_session(db: AsyncSession) -> AsyncIterator[AsyncSession]:
    """Let the lifecycle adapter run inside the request transaction.

    `SqlTaskLifecycleStore` expects a session factory; yielding the request session keeps
    the row lock, the version bump, the reminder cancellation and the evidence copy in one
    transaction committed by the router.
    """
    yield db


async def _lifecycle(
    db: AsyncSession,
    user_id: uuid.UUID,
    task_id: uuid.UUID,
    *,
    expected_version: int,
    target: str,
) -> TaskLifecycleResult:
    lifecycle = TaskLifecycle(SqlTaskLifecycleStore(lambda: _request_session(db)))
    if target == "completed":
        result = await lifecycle.complete(
            user_id, task_id, expected_version=expected_version
        )
    else:
        result = await lifecycle.dismiss(user_id, task_id, expected_version=expected_version)
    if result.version != expected_version:
        await _copy_evidence_version(
            db,
            user_id=user_id,
            task_id=task_id,
            from_version=expected_version,
            to_version=result.version,
        )
        await db.flush()
    return result


async def complete_task(
    db: AsyncSession, user_id: uuid.UUID, task_id: uuid.UUID, *, expected_version: int
) -> TaskLifecycleResult:
    return await _lifecycle(
        db, user_id, task_id, expected_version=expected_version, target="completed"
    )


async def dismiss_task(
    db: AsyncSession, user_id: uuid.UUID, task_id: uuid.UUID, *, expected_version: int
) -> TaskLifecycleResult:
    return await _lifecycle(
        db, user_id, task_id, expected_version=expected_version, target="dismissed"
    )

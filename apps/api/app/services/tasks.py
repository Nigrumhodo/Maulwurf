"""M4 task writes: proposals, edits, confirmation and lifecycle (A3.3/A3.4).

Reads live in `task_reads`. The active `tasks` row is the only source of truth. Writes
are tenant-scoped and use `version` as the optimistic-locking token:

- `POST /tasks` accepts `manual` (zero sources), `extraction` (at least one validated
  source) and `chat` (zero or more; zero is an honest general suggestion). Every span is
  validated against the persisted corpus before the task is published.
- `PATCH` edits `pending`/`needs_review` tasks in place and turns an edit of a `confirmed`
  task into a `pending_confirmation` revision, without touching the active columns or
  Google. `completed`/`dismissed` are terminal.
- `confirm` validates the resolved date, the Calendar block and the evidence, promotes at
  most one revision and bumps `version` in the same transaction that creates or reuses one
  `sync_task_event` outbox row. Without a ready Calendar integration the task is still
  confirmed but stays `calendar_sync_status='blocked'` and no event is written; only a
  later reconnect (J3.3) resumes the same version, never a repeated confirmation.
- `dismiss`/`complete` reuse the J3.6 lifecycle adapter, cancel unsent reminders and keep
  the linked Calendar event with a notice.

Every effective version bump copies the active evidence rows to the new task version, so
`load_task_evidence` keeps finding the quote for the active version.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

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
    User,
)
from app.schemas.analysis import DateStatus
from app.schemas.tasks import (
    TaskCreate,
    TaskOut,
    TaskPatch,
    validate_date_fields,
    validate_event_block,
)
from app.services.analysis_windows import EvidenceSpan
from app.services.oauth_google import missing_calendar_scopes
from app.services.outbox import SYNC_TASK_EVENT
from app.services.reminders import (
    DEFAULT_REMINDER_PREFERENCES,
    ReminderPreferences,
    ReminderTask,
    schedule_task_reminders,
)
from app.services.revisions import (
    discard_pending_revisions,
    record_pending_revision,
)
from app.services.task_evidence import (
    ProvenanceInvalid,
    load_task_evidence,
    persist_task_spans,
    validate_provenance,
)
from app.services.task_lifecycle import TaskLifecycle, TaskLifecycleResult
from app.services.task_lifecycle_store import SqlTaskLifecycleStore
from app.services.task_reads import _assemble, _load_task
from app.services.task_snapshot import TaskSnapshot, needs_review_task
from app.services.task_state import (
    ConfirmationProblem,
    RevisionCommand,
    SyncCommand,
    TaskCommand,
    confirmation_date_problem,
    confirmation_problem,
    ensure_revision_command,
    ensure_sync_command,
    ensure_task_command,
    evidence_required,
    revision_transition,
)

CALENDAR_SYNC_BLOCKED_NOTICE = "calendar_sync_blocked"


def ensure_version(current_version: int, current_status: str, expected_version: int) -> None:
    """The single optimistic-locking decision shared by PATCH/confirm/lifecycle."""
    if current_version != expected_version:
        raise ApiError(
            409,
            "version_conflict",
            "La tarea cambió desde que la cargaste; recarga antes de continuar.",
            {"current_version": current_version, "current_status": current_status},
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


def _validate_fields(fields: TaskSnapshot) -> None:
    proposal = fields.proposal
    try:
        validate_date_fields(
            due_date=proposal.due_date,
            due_at=proposal.due_at,
            timezone=proposal.timezone,
            all_day=proposal.all_day,
            date_status=DateStatus(proposal.date_status),
        )
    except ValueError as exc:
        raise validation_failed({"date_status": str(exc)}) from exc
    try:
        validate_event_block(
            start_at=fields.event_start_at,
            end_at=fields.event_end_at,
            start_date=fields.event_start_date,
            end_date_exclusive=fields.event_end_date_exclusive,
        )
    except ValueError as exc:
        raise validation_failed({"event_block": str(exc)}) from exc
    if not is_iana_timezone(proposal.timezone):
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

    snapshot = TaskSnapshot.from_create(body, subject_id=subject_id)
    _validate_fields(snapshot)
    task = needs_review_task(
        user_id=user_id,
        source=body.source,
        audio_id=audio_id,
        fields=snapshot,
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
    before: TaskSnapshot,
    after: TaskSnapshot,
) -> None:
    """A sourced task cannot be detached from the subject of its evidence."""
    if after.subject_id == before.subject_id:
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
    # Terminal tasks have no `edit` row; a confirmed edit is a pending revision.
    ensure_task_command(task.status, TaskCommand.EDIT)

    before = TaskSnapshot.from_task(task)
    after = before.merge(body)
    await _ensure_subject_change_allowed(db, user_id, task, before, after)
    await _validate_subject(db, user_id, after.subject_id)
    _validate_fields(after)
    if before.same_as(after):
        return (await _assemble(db, user_id, [task]))[0]

    if task.status == "confirmed":
        # The revision service locks the same row and bases the proposal on its current
        # version, so a concurrent PATCH/confirm cannot leave an unconfirmable revision.
        await record_pending_revision(
            db,
            user_id=user_id,
            task_id=task.id,
            proposed_fields=after.to_payload(),
            revision_type="edit_of_confirmed",
            evidence_validated=True,
        )
        return (await _assemble(db, user_id, [task]))[0]

    due_changed = before.due_changed(after)
    old_version = task.version
    after.apply(task)
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
    if revision_transition(revision.status, RevisionCommand.ACTIVATE) is None:
        raise ApiError(
            409,
            "version_conflict",
            "La revisión ya no está pendiente de confirmación.",
            {"current_version": task.version, "current_status": task.status},
        )
    return revision


async def _validate_confirmable(
    db: AsyncSession, user_id: uuid.UUID, task: Task, fields: TaskSnapshot
) -> None:
    # Date and block answer before evidence, matching the documented 422 order.
    problem = confirmation_date_problem(
        date_status=fields.proposal.date_status,
        event_start_at=fields.event_start_at,
        event_end_at=fields.event_end_at,
        event_start_date=fields.event_start_date,
        event_end_date_exclusive=fields.event_end_date_exclusive,
    )
    if problem is not None:
        raise _date_unresolved(problem.value)

    source_count = int(
        await db.scalar(
            select(func.count())
            .select_from(TaskSource)
            .where(TaskSource.user_id == user_id, TaskSource.task_id == task.id)
        )
        or 0
    )
    has_evidence = False
    if evidence_required(task.source, source_count):
        try:
            evidence = await load_task_evidence(
                db, user_id=user_id, task_id=task.id, task_version=task.version
            )
        except ProvenanceInvalid as exc:
            raise _evidence_invalid() from exc
        has_evidence = bool(evidence)

    problem = confirmation_problem(
        date_status=fields.proposal.date_status,
        event_start_at=fields.event_start_at,
        event_end_at=fields.event_end_at,
        event_start_date=fields.event_start_date,
        event_end_date_exclusive=fields.event_end_date_exclusive,
        source=task.source,
        source_count=source_count,
        has_evidence=has_evidence,
    )
    if problem is ConfirmationProblem.EVIDENCE:
        raise _evidence_invalid()
    if problem is not None:
        raise _date_unresolved(problem.value)


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
            # A dispatched row's job may already have finished and the dispatcher only
            # publishes `pending` rows; the job is idempotent, so a new cycle is safe.
            OutboxEvent.status == "pending",
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


async def _arm_task_reminders(
    db: AsyncSession, *, user_id: uuid.UUID, task: Task, now: datetime
) -> None:
    """Arm M8 escalating reminders (J4.3) for a confirmed task in the same transaction."""
    user = await db.get(User, user_id)
    user_timezone = user.timezone if user is not None and user.timezone else "UTC"
    settings_row = await db.get(CalendarSettings, user_id)
    if settings_row is None:
        prefs = DEFAULT_REMINDER_PREFERENCES
    else:
        prefs = ReminderPreferences(
            reminder_offsets_hours=tuple(int(h) for h in settings_row.reminder_offsets_hours),
            reminder_allday_offsets_days=tuple(
                int(d) for d in settings_row.reminder_allday_offsets_days
            ),
        )
    await schedule_task_reminders(
        db,
        ReminderTask.from_task(task),
        user_timezone=user_timezone,
        preferences=prefs,
        now=now,
    )


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
    # `confirm` is a self-row on confirmed (a safe repeat) and forbidden on terminal tasks.
    ensure_task_command(task.status, TaskCommand.CONFIRM)

    now = datetime.now(UTC)
    revision = (
        await _load_pending_revision(db, user_id, task, revision_id)
        if revision_id is not None
        else None
    )
    before = TaskSnapshot.from_task(task)
    if revision is not None:
        ensure_version(task.version, task.status, revision.base_task_version)
        try:
            after = before.overlay(dict(revision.proposed_fields))
        except ValueError as exc:
            raise _invalid_revision() from exc
        await _ensure_subject_change_allowed(db, user_id, task, before, after)
        await _validate_subject(db, user_id, after.subject_id)
        _validate_fields(after)
    else:
        after = before

    target_calendar_id, calendar_ready = await _calendar_state(db, user_id)

    # A repeat without a revision is a safe double click. It never bumps the version and
    # never resumes a blocked delivery: for the same version, only a Calendar reconnect may
    # move `blocked` back to `pending` (A3.7).
    if revision is None and task.status == "confirmed":
        await _arm_task_reminders(db, user_id=user_id, task=task, now=now)
        notice = (
            CALENDAR_SYNC_BLOCKED_NOTICE
            if task.calendar_sync_status == "blocked"
            else None
        )
        return (await _assemble(db, user_id, [task]))[0], notice

    await _validate_confirmable(db, user_id, task, after)

    due_changed = before.due_changed(after)
    old_version = task.version
    after.apply(task)
    task.version = old_version + 1
    if due_changed:
        task.due_version += 1
    task.status = "confirmed"
    task.confirmed_at = task.confirmed_at or now
    # Keep the target even while blocked: J3.4 blocks with `calendar_missing` without it,
    # and J3.3 needs the ID to resume the sync after a reconnect. Never clear an old link.
    if target_calendar_id is not None:
        task.calendar_id = target_calendar_id
    # A new version starts a fresh obligation; `request`/`block_new` are not a resume of an
    # older delivery, which stays a reconnect-only command.
    sync_command = SyncCommand.REQUEST if calendar_ready else SyncCommand.BLOCK_NEW
    task.calendar_sync_status = ensure_sync_command(
        task.calendar_sync_status, sync_command
    )
    if revision is not None:
        revision.status = ensure_revision_command(
            revision.status, RevisionCommand.ACTIVATE
        )
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
    await _arm_task_reminders(db, user_id=user_id, task=task, now=now)
    if calendar_ready:
        await _ensure_confirmation_outbox(db, user_id=user_id, task=task)
    notice = None if calendar_ready else CALENDAR_SYNC_BLOCKED_NOTICE
    return (await _assemble(db, user_id, [task]))[0], notice


async def _lifecycle(
    db: AsyncSession,
    user_id: uuid.UUID,
    task_id: uuid.UUID,
    *,
    expected_version: int,
    target: str,
) -> TaskLifecycleResult:
    lifecycle = TaskLifecycle(SqlTaskLifecycleStore(db))
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

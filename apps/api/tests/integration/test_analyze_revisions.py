"""I-S3-AN-04, reanalysis half: a new pass does not overwrite a confirmed task.

The PATCH half (an edit creates ``pending_confirmation``) stays with A3.5. This module
drives ``analyze(audio_id)`` against PostgreSQL with a scripted model: no provider call.
"""

import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Audio,
    IngestionAttempt,
    ProcessingRun,
    Segment,
    Task,
    TaskEvidence,
    TaskRevision,
    TaskSource,
    Transcript,
)
from app.schemas.tasks import TaskEventBlockIn, TaskOut, TaskPatch
from app.services.analysis import (
    ExistingProposal,
    ExtractionModel,
    PreparedProposal,
    WriteKind,
    apply_task_writes,
    parse_extraction_payload,
    plan_task_writes,
    proposal_fields,
)
from app.services.analysis_windows import AggregatedProposal, EvidenceSpan
from app.services.tasks import confirm_task, patch_task
from app.workers.jobs.analyze import run_analyze
from prompts.registry import load_summary_prompts
from tests.integration import actors

pytestmark = pytest.mark.integration

QUOTE = "the midterm exam will be on Monday, September 21"
NOW = datetime(2026, 9, 16, 15, tzinfo=UTC)
DUE_DAY = date(2026, 9, 28)
DUE_END = date(2026, 9, 29)


def _document(title: str) -> str:
    return (
        '{"items":[{"tipo":"examen","titulo":"'
        + title
        + '","detalle":null,"due_date":null,"due_at":"2026-09-21T00:00:00-06:00",'
        '"timezone":"UTC","all_day":false,"date_status":"resolved","confidence_score":0.5,'
        '"mencion":{"kind":"all_day","due_date":"2026-09-28"},'
        '"fuente":{"spans":[{"segment_id":"%s","inicio_char":0,"fin_char":'
        + str(len(QUOTE))
        + ',"cita_textual":"'
        + QUOTE
        + '"}]}}],"resumen_clase":"One exam was announced for the following week. '
        'Students should review the chapter. No other work was assigned.",'
        '"temas":["midterm"]}'
    )


class _Model:
    segment_id: str = ""

    def __init__(self, title: str) -> None:
        self.title = title

    async def complete(self, *, system: str, user: str) -> str:
        if system == load_summary_prompts().system:
            return (
                '{"resumen_clase":"One exam was announced for the following week. '
                'Students should review the chapter. No other work was assigned.",'
                '"temas":["midterm"]}'
            )
        return _document(self.title) % self.segment_id


def _factory(
    db: AsyncSession,
) -> Callable[[], AbstractAsyncContextManager[AsyncSession]]:
    @asynccontextmanager
    async def sessions() -> AsyncIterator[AsyncSession]:
        yield db

    return sessions


async def _class(db: AsyncSession) -> tuple[actors.Actor, Audio]:
    actor = await actors.make_actor(db)
    audio = await actors.make_audio(db, actor)
    attempt = await actors.make_attempt(db, actor, audio=audio)
    attempt.status = "succeeded"
    attempt.cleanup_status = "verified"
    transcript = Transcript(
        user_id=actor.user_id,
        audio_id=audio.id,
        version=1,
        language_requested="es",
        model="test-model",
        model_config={},
        timestamp_precision="none",
        text=QUOTE,
        char_count=len(QUOTE),
        fts_config="spanish",
    )
    db.add(transcript)
    await db.flush()
    segment = Segment(
        user_id=actor.user_id,
        transcript_id=transcript.id,
        ordinal=0,
        text=QUOTE,
        char_start=0,
        char_end=len(QUOTE),
    )
    db.add(segment)
    audio.active_transcript_version = 1
    await db.flush()
    _Model.segment_id = str(segment.id)
    return actor, audio


async def _task(db: AsyncSession, user_id: uuid.UUID) -> Task:
    return (await db.execute(select(Task).where(Task.user_id == user_id))).scalar_one()


async def _latest_revision(
    db: AsyncSession, task_id: uuid.UUID, *, status: str | None = None
) -> TaskRevision | None:
    stmt = select(TaskRevision).where(TaskRevision.task_id == task_id)
    if status is not None:
        stmt = stmt.where(TaskRevision.status == status)
    stmt = stmt.order_by(TaskRevision.created_at.desc(), TaskRevision.id.desc())
    return (await db.scalars(stmt)).first()


async def _force_new_run(db: AsyncSession, audio_id: uuid.UUID) -> None:
    run = (
        await db.execute(select(ProcessingRun).where(ProcessingRun.audio_id == audio_id))
    ).scalar_one()
    run.status = "failed"
    run.finished_at = None
    await db.flush()


async def _add_event_block(
    db: AsyncSession, user_id: uuid.UUID, task_id: uuid.UUID, version: int
) -> None:
    await patch_task(
        db,
        user_id,
        task_id,
        TaskPatch(
            version=version,
            event_block=TaskEventBlockIn(
                start_date=DUE_DAY, end_date_exclusive=DUE_END
            ),
        ),
    )
    await db.flush()


async def _confirm(
    db: AsyncSession,
    user_id: uuid.UUID,
    task_id: uuid.UUID,
    version: int,
    *,
    revision_id: uuid.UUID | None = None,
) -> TaskOut:
    out, _notice = await confirm_task(
        db,
        user_id,
        task_id,
        expected_version=version,
        revision_id=revision_id,
    )
    await db.flush()
    return out


async def test_reanalysis_revises_a_confirmed_task_without_changing_it(
    db_session: AsyncSession,
) -> None:
    actor, audio = await _class(db_session)
    model = _Model("First title")

    def models() -> ExtractionModel:
        return model

    await run_analyze(_factory(db_session), models, audio.id, job_try=1, now=NOW)
    task = (
        await db_session.execute(select(Task).where(Task.user_id == actor.user_id))
    ).scalar_one()
    assert task.status == "needs_review"
    assert task.source == "extraction"
    assert task.titulo == "First title"
    assert task.calendar_sync_status == "not_requested"
    assert task.due_at is None
    assert task.all_day is True
    assert task.date_text == "Monday, September 28"
    assert task.date_reason is None
    assert task.due_version == 1
    transcript = (
        await db_session.execute(select(Transcript).where(Transcript.audio_id == audio.id))
    ).scalar_one()
    await db_session.refresh(audio)
    assert transcript.summary == (
        "One exam was announced for the following week. "
        "Students should review the chapter. No other work was assigned."
    )
    assert transcript.topics == ["midterm"]
    assert transcript.analysis_version == 1
    assert audio.active_analysis_version == 1
    sources = (
        await db_session.execute(select(func.count()).select_from(TaskSource))
    ).scalar_one()
    assert sources == 1
    evidence = (
        await db_session.execute(select(TaskEvidence))
    ).scalar_one()
    assert evidence.task_version == 1
    assert evidence.quote == QUOTE

    task_id = task.id
    audio_id = audio.id
    task.status = "confirmed"
    task.confirmed_at = NOW
    await db_session.flush()
    db_session.expire_all()
    run = (
        await db_session.execute(
            select(ProcessingRun).where(ProcessingRun.audio_id == audio_id)
        )
    ).scalar_one()
    assert run.status == "succeeded"
    await run_analyze(_factory(db_session), models, audio_id, job_try=1, now=NOW)
    task = (
        await db_session.execute(select(Task).where(Task.id == task_id))
    ).scalar_one()
    assert task.titulo == "First title"
    assert task.status == "confirmed"
    assert (
        await db_session.execute(select(func.count()).select_from(TaskRevision))
    ).scalar_one() == 0

    run.status = "failed"
    run.finished_at = None
    await db_session.flush()
    model.title = "Second title"
    await run_analyze(_factory(db_session), models, audio_id, job_try=1, now=NOW)
    task = (
        await db_session.execute(select(Task).where(Task.id == task_id))
    ).scalar_one()
    assert task.titulo == "First title"
    assert task.status == "confirmed"
    assert task.version == 1
    revision = (
        await db_session.execute(select(TaskRevision).where(TaskRevision.task_id == task.id))
    ).scalar_one()
    assert revision.status == "pending_confirmation"
    assert revision.revision_type == "reanalysis"
    assert revision.base_task_version == 1
    assert revision.run_id == run.id
    assert revision.activated_at is None
    assert revision.proposed_fields["titulo"] == "Second title"
    assert revision.proposed_fields["date_text"] == "Monday, September 28"
    assert revision.evidence_validated is True
    # A pending revision never touches the active version's evidence or reminder
    # generation.
    assert task.due_version == 1
    assert task.date_text == "Monday, September 28"
    assert (
        await db_session.execute(select(func.count()).select_from(TaskEvidence))
    ).scalar_one() == 1

    await db_session.refresh(run)
    run.status = "failed"
    run.finished_at = None
    await db_session.flush()
    await run_analyze(_factory(db_session), models, audio_id, job_try=1, now=NOW)
    revisions = (
        await db_session.execute(select(func.count()).select_from(TaskRevision))
    ).scalar_one()
    assert revisions == 1
    tasks = (
        await db_session.execute(select(func.count()).select_from(Task))
    ).scalar_one()
    assert tasks == 1


async def test_cleanup_pending_writes_nothing(db_session: AsyncSession) -> None:
    _actor, audio = await _class(db_session)
    attempt = (
        await db_session.execute(
            select(IngestionAttempt).where(IngestionAttempt.audio_id == audio.id)
        )
    ).scalar_one()
    attempt.cleanup_status = "pending"
    await db_session.flush()

    def models() -> ExtractionModel:
        return _Model("Ignored")

    await run_analyze(_factory(db_session), models, audio.id, job_try=1, now=NOW)
    assert (
        await db_session.execute(select(func.count()).select_from(Task))
    ).scalar_one() == 0


async def test_confirming_between_the_plan_and_the_write_keeps_the_title(
    db_session: AsyncSession,
) -> None:
    actor, audio = await _class(db_session)
    segment = (
        await db_session.execute(select(Segment).where(Segment.user_id == actor.user_id))
    ).scalar_one()
    run = ProcessingRun(
        user_id=actor.user_id,
        audio_id=audio.id,
        stage="analyze",
        transcript_version=1,
        config_version="test-config",
        prompt_version="v1",
        status="running",
    )
    task = Task(
        user_id=actor.user_id,
        audio_id=audio.id,
        subject_id=audio.subject_id,
        source="extraction",
        tipo="examen",
        titulo="Original",
        timezone=audio.class_timezone,
        all_day=False,
        date_status="missing",
        status="needs_review",
    )
    db_session.add(run)
    db_session.add(task)
    await db_session.flush()
    parsed = parse_extraction_payload(
        _document("Replacement") % str(segment.id),
        class_date=audio.class_date,
        class_timezone=audio.class_timezone,
        now=NOW,
    )
    item = parsed.result.items[0]
    span = EvidenceSpan(
        segment_id=segment.id, char_start=0, char_end=len(QUOTE), quote=QUOTE
    )
    proposal = AggregatedProposal(
        value=PreparedProposal(item=item, date_reason=parsed.reasons[0]),
        evidence=(span,),
    )
    stale = proposal_fields(proposal)
    existing = ExistingProposal(
        task_id=task.id,
        identity=proposal.identity,
        status="needs_review",
        version=1,
        fields=type(stale)(
            tipo=stale.tipo,
            titulo="Original",
            detalle=stale.detalle,
            due_date=stale.due_date,
            due_at=stale.due_at,
            timezone=stale.timezone,
            all_day=stale.all_day,
            date_status=stale.date_status,
            confidence_score=stale.confidence_score,
            date_reason=stale.date_reason,
        ),
    )
    writes = plan_task_writes(run.id, (proposal,), (existing,), set())
    assert writes[0].kind is WriteKind.UPDATE
    task.status = "confirmed"
    task.confirmed_at = NOW
    await db_session.flush()
    await apply_task_writes(
        db_session,
        user_id=actor.user_id,
        audio_id=audio.id,
        subject_id=audio.subject_id,
        transcript_id=segment.transcript_id,
        transcript_version=1,
        run_id=run.id,
        writes=writes,
    )
    await db_session.refresh(task)
    assert task.titulo == "Original"
    assert task.status == "confirmed"
    revision = (
        await db_session.execute(select(TaskRevision).where(TaskRevision.task_id == task.id))
    ).scalar_one()
    assert revision.status == "pending_confirmation"
    assert revision.proposed_fields["titulo"] == "Replacement"


async def test_reanalysis_revises_an_edited_task_without_touching_it(
    db_session: AsyncSession,
) -> None:
    actor, audio = await _class(db_session)
    model = _Model("First title")

    def models() -> ExtractionModel:
        return model

    await run_analyze(_factory(db_session), models, audio.id, job_try=1, now=NOW)
    task = await _task(db_session, actor.user_id)
    task_id = task.id
    audio_id = audio.id
    await patch_task(
        db_session,
        actor.user_id,
        task_id,
        TaskPatch(
            version=1,
            titulo="Edited title",
            event_block=TaskEventBlockIn(
                start_date=DUE_DAY, end_date_exclusive=DUE_END
            ),
        ),
    )
    await db_session.flush()
    db_session.expire_all()
    edited = await db_session.get(Task, task_id)
    assert edited is not None
    assert edited.titulo == "Edited title"
    assert edited.version == 2

    await _force_new_run(db_session, audio_id)
    model.title = "Second title"
    await run_analyze(_factory(db_session), models, audio_id, job_try=1, now=NOW)
    await db_session.flush()
    db_session.expire_all()

    active = await db_session.get(Task, task_id)
    assert active is not None
    assert active.titulo == "Edited title"
    assert active.version == 2
    revision = await _latest_revision(db_session, task_id)
    assert revision is not None
    assert revision.revision_type == "reanalysis"
    assert revision.status == "pending_confirmation"
    assert revision.base_task_version == 2
    assert revision.proposed_fields["titulo"] == "Second title"

    promoted = await _confirm(
        db_session, actor.user_id, task_id, 2, revision_id=revision.id
    )
    assert promoted.titulo == "Second title"
    assert promoted.version == 3


async def test_reanalysis_leaves_an_activated_revision_untouched(
    db_session: AsyncSession,
) -> None:
    actor, audio = await _class(db_session)
    model = _Model("First title")

    def models() -> ExtractionModel:
        return model

    await run_analyze(_factory(db_session), models, audio.id, job_try=1, now=NOW)
    task = await _task(db_session, actor.user_id)
    task_id = task.id
    await _add_event_block(db_session, actor.user_id, task_id, 1)
    await _confirm(db_session, actor.user_id, task_id, 2)

    activated = TaskRevision(
        user_id=actor.user_id,
        task_id=task_id,
        base_task_version=3,
        revision_type="edit_of_confirmed",
        proposed_fields={"titulo": "Already activated"},
        evidence_validated=True,
        status="activated",
        activated_at=NOW,
    )
    db_session.add(activated)
    await db_session.flush()
    activated_id = activated.id
    activated_payload = dict(activated.proposed_fields)

    await _force_new_run(db_session, audio.id)
    model.title = "Second title"
    await run_analyze(_factory(db_session), models, audio.id, job_try=1, now=NOW)
    await db_session.flush()
    db_session.expire_all()

    stored = await db_session.get(TaskRevision, activated_id)
    assert stored is not None
    assert stored.status == "activated"
    assert stored.activated_at == NOW
    assert stored.proposed_fields == activated_payload

    pending = await _latest_revision(db_session, task_id, status="pending_confirmation")
    assert pending is not None
    assert pending.revision_type == "reanalysis"
    assert pending.base_task_version == 3
    assert pending.proposed_fields["titulo"] == "Second title"

    active = await db_session.get(Task, task_id)
    assert active is not None
    assert active.titulo == "First title"
    assert active.version == 3


async def test_reanalysis_supersedes_a_pending_edit_without_mutating_the_task(
    db_session: AsyncSession,
) -> None:
    actor, audio = await _class(db_session)
    model = _Model("First title")

    def models() -> ExtractionModel:
        return model

    await run_analyze(_factory(db_session), models, audio.id, job_try=1, now=NOW)
    task = await _task(db_session, actor.user_id)
    task_id = task.id
    await _add_event_block(db_session, actor.user_id, task_id, 1)
    await _confirm(db_session, actor.user_id, task_id, 2)
    await patch_task(
        db_session,
        actor.user_id,
        task_id,
        TaskPatch(version=3, titulo="Manual edit"),
    )
    await db_session.flush()
    manual = await _latest_revision(db_session, task_id)
    assert manual is not None
    manual_id = manual.id

    await _force_new_run(db_session, audio.id)
    model.title = "Second title"
    await run_analyze(_factory(db_session), models, audio.id, job_try=1, now=NOW)
    await db_session.flush()
    db_session.expire_all()

    discarded = await db_session.get(TaskRevision, manual_id)
    assert discarded is not None
    assert discarded.status == "discarded"
    pending = await _latest_revision(db_session, task_id, status="pending_confirmation")
    assert pending is not None
    assert pending.revision_type == "reanalysis"
    assert pending.base_task_version == 3
    active = await db_session.get(Task, task_id)
    assert active is not None
    assert active.titulo == "First title"
    assert active.version == 3


@pytest.mark.parametrize("status", ["dismissed", "completed"])
async def test_reanalysis_skips_terminal_tasks(
    db_session: AsyncSession, status: str
) -> None:
    actor, audio = await _class(db_session)
    model = _Model("First title")

    def models() -> ExtractionModel:
        return model

    await run_analyze(_factory(db_session), models, audio.id, job_try=1, now=NOW)
    task = await _task(db_session, actor.user_id)
    task.status = status
    await db_session.flush()
    task_id = task.id

    await _force_new_run(db_session, audio.id)
    model.title = "Ignored"
    await run_analyze(_factory(db_session), models, audio.id, job_try=1, now=NOW)
    await db_session.flush()
    db_session.expire_all()

    active = await db_session.get(Task, task_id)
    assert active is not None
    assert active.status == status
    assert active.titulo == "First title"
    assert await _latest_revision(db_session, task_id) is None


async def test_reanalysis_with_an_unchanged_proposal_creates_no_revision(
    db_session: AsyncSession,
) -> None:
    actor, audio = await _class(db_session)
    model = _Model("First title")

    def models() -> ExtractionModel:
        return model

    await run_analyze(_factory(db_session), models, audio.id, job_try=1, now=NOW)
    task = await _task(db_session, actor.user_id)
    task_id = task.id
    await _add_event_block(db_session, actor.user_id, task_id, 1)
    await _confirm(db_session, actor.user_id, task_id, 2)
    assert await _latest_revision(db_session, task_id) is None

    await _force_new_run(db_session, audio.id)
    await run_analyze(_factory(db_session), models, audio.id, job_try=1, now=NOW)
    await db_session.flush()
    db_session.expire_all()

    active = await db_session.get(Task, task_id)
    assert active is not None
    assert active.titulo == "First title"
    assert active.version == 3
    assert await _latest_revision(db_session, task_id) is None


async def test_a_version_bump_between_the_plan_and_the_write_is_the_revision_base(
    db_session: AsyncSession,
) -> None:
    actor, audio = await _class(db_session)
    segment = (
        await db_session.execute(select(Segment).where(Segment.user_id == actor.user_id))
    ).scalar_one()
    run = ProcessingRun(
        user_id=actor.user_id,
        audio_id=audio.id,
        stage="analyze",
        transcript_version=1,
        config_version="test-config",
        prompt_version="v1",
        status="running",
    )
    task = Task(
        user_id=actor.user_id,
        audio_id=audio.id,
        subject_id=audio.subject_id,
        source="extraction",
        tipo="examen",
        titulo="Original",
        timezone=audio.class_timezone,
        all_day=False,
        date_status="missing",
        status="needs_review",
    )
    db_session.add(run)
    db_session.add(task)
    await db_session.flush()
    parsed = parse_extraction_payload(
        _document("Replacement") % str(segment.id),
        class_date=audio.class_date,
        class_timezone=audio.class_timezone,
        now=NOW,
    )
    proposal = AggregatedProposal(
        value=PreparedProposal(
            item=parsed.result.items[0], date_reason=parsed.reasons[0]
        ),
        evidence=(
            EvidenceSpan(
                segment_id=segment.id,
                char_start=0,
                char_end=len(QUOTE),
                quote=QUOTE,
            ),
        ),
    )
    fields = proposal_fields(proposal)
    existing = ExistingProposal(
        task_id=task.id,
        identity=proposal.identity,
        status="needs_review",
        version=1,
        fields=type(fields)(
            tipo=fields.tipo,
            titulo="Original",
            detalle=fields.detalle,
            due_date=fields.due_date,
            due_at=fields.due_at,
            timezone=fields.timezone,
            all_day=fields.all_day,
            date_status=fields.date_status,
            confidence_score=fields.confidence_score,
            date_reason=fields.date_reason,
        ),
    )
    writes = plan_task_writes(run.id, (proposal,), (existing,), set())
    assert writes[0].kind is WriteKind.UPDATE

    task.titulo = "Edited title"
    task.version = 2
    await db_session.flush()
    await apply_task_writes(
        db_session,
        user_id=actor.user_id,
        audio_id=audio.id,
        subject_id=audio.subject_id,
        transcript_id=segment.transcript_id,
        transcript_version=1,
        run_id=run.id,
        writes=writes,
    )
    await db_session.refresh(task)
    assert task.titulo == "Edited title"
    assert task.version == 2
    revision = await _latest_revision(db_session, task.id)
    assert revision is not None
    assert revision.base_task_version == 2
    assert revision.proposed_fields["titulo"] == "Replacement"

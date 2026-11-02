"""I-S3-AN-04, reanalysis half: a new pass does not overwrite a confirmed task.

The PATCH half (an edit creates ``pending_confirmation``) stays with A3.5. This module
drives ``analyze(audio_id)`` against PostgreSQL with a scripted model: no provider call.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Audio,
    IngestionAttempt,
    ProcessingRun,
    Segment,
    Task,
    TaskRevision,
    TaskSource,
    Transcript,
)
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
from app.workers.jobs.analyze import run_analyze
from prompts.registry import load_summary_prompts
from tests.integration import actors

pytestmark = pytest.mark.integration

QUOTE = "the midterm exam will be on Monday, September 21"
NOW = datetime(2026, 9, 16, 15, tzinfo=UTC)


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
    assert revision.evidence_validated is True

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

"""I-S3-AN-05: task evidence and provenance are reconstructible from PostgreSQL.

The suite exercises the real tables and the service layer, not a mock:

- every producer persists the normalized source rows and the literal quote;
- the origin rule is enforced before a row is written (manual 0, extraction >= 1,
  chat 0 or more);
- invalid membership or a mismatching quote writes nothing;
- an in-place reanalysis replaces the active provenance and bumps `due_version` only
  when the due representation changes;
- a protected task only gains a pending revision;
- `load_task_evidence` rebuilds the quote, offsets and label, rejects a tampered quote,
  and a tombstoned class or another tenant cannot read the evidence.
"""

import json
import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Audio,
    ProcessingRun,
    Segment,
    Task,
    TaskEvidence,
    TaskRevision,
    TaskSource,
    Transcript,
)
from app.services.analysis import (
    PreparedProposal,
    WriteKind,
    apply_task_writes,
    load_existing_proposals,
    parse_extraction_payload,
    plan_task_writes,
)
from app.services.analysis_windows import AggregatedProposal, EvidenceSpan
from app.services.task_evidence import (
    ProvenanceInvalid,
    load_task_evidence,
    persist_task_spans,
    validate_provenance,
)
from tests.integration import actors

pytestmark = pytest.mark.integration

FIRST = "the midterm exam will be on Monday, September 21"
SECOND = "bring the lab report on Friday"
TRANSCRIPT = f"{FIRST} {SECOND}"
NOW = datetime(2026, 9, 16, 15, tzinfo=UTC)
APP_ROLE = "maulwurf_app"


async def _class(
    db: AsyncSession, actor: actors.Actor
) -> tuple[Audio, Segment, Segment]:
    """Two ordered segments; only the first carries a reliable measured time."""
    audio = await actors.make_audio(db, actor)
    transcript = Transcript(
        user_id=actor.user_id,
        audio_id=audio.id,
        version=1,
        language_requested="es",
        model="test-model",
        model_config={},
        timestamp_precision="segment",
        text=TRANSCRIPT,
        char_count=len(TRANSCRIPT),
        fts_config="spanish",
    )
    db.add(transcript)
    await db.flush()
    first = Segment(
        user_id=actor.user_id,
        transcript_id=transcript.id,
        ordinal=0,
        text=FIRST,
        char_start=0,
        char_end=len(FIRST),
        t_start=0.0,
        t_end=5.0,
    )
    second_start = len(FIRST) + 1
    second = Segment(
        user_id=actor.user_id,
        transcript_id=transcript.id,
        ordinal=1,
        text=SECOND,
        char_start=second_start,
        char_end=second_start + len(SECOND),
    )
    db.add_all([first, second])
    audio.active_transcript_version = 1
    await db.flush()
    return audio, first, second


def _task(actor: actors.Actor, source: str, *, audio: Audio | None = None) -> Task:
    return Task(
        user_id=actor.user_id,
        audio_id=None if audio is None else audio.id,
        source=source,
        tipo="tarea",
        titulo=f"Tarea {uuid.uuid4().hex[:6]}",
        timezone="America/Bogota",
        all_day=True,
        date_status="missing",
        status="needs_review",
    )


def _document(title: str, mention: str, *, segment_id: uuid.UUID, quote: str) -> str:
    return json.dumps(
        {
            "items": [
                {
                    "tipo": "tarea",
                    "titulo": title,
                    "detalle": None,
                    "due_date": None,
                    "due_at": None,
                    "timezone": "America/Bogota",
                    "all_day": True,
                    "date_status": "missing",
                    "confidence_score": 0.5,
                    "mencion": {"kind": "all_day", "due_date": mention},
                    "fuente": {
                        "spans": [
                            {
                                "segment_id": str(segment_id),
                                "inicio_char": 0,
                                "fin_char": len(quote),
                                "cita_textual": quote,
                            }
                        ]
                    },
                }
            ],
            "resumen_clase": "A task was announced for the next week. Students should review it.",
            "temas": ["homework"],
        }
    )


def _proposal(
    audio: Audio,
    segment_id: uuid.UUID,
    *,
    title: str,
    mention: str = "2026-09-28",
    quote: str = FIRST,
) -> AggregatedProposal[PreparedProposal]:
    parsed = parse_extraction_payload(
        _document(title, mention, segment_id=segment_id, quote=quote),
        class_date=audio.class_date,
        class_timezone=audio.class_timezone,
        now=NOW,
    )
    assert len(parsed.result.items) == 1
    return AggregatedProposal(
        value=PreparedProposal(item=parsed.result.items[0], date_reason=parsed.reasons[0]),
        evidence=(
            EvidenceSpan(
                segment_id=segment_id,
                char_start=0,
                char_end=len(quote),
                quote=quote,
            ),
        ),
    )


async def _source_count(db: AsyncSession, task_id: uuid.UUID) -> int:
    return int(
        (
            await db.execute(
                select(func.count()).select_from(TaskSource).where(TaskSource.task_id == task_id)
            )
        ).scalar_one()
    )


async def _evidence_count(db: AsyncSession, task_id: uuid.UUID) -> int:
    return int(
        (
            await db.execute(
                select(func.count())
                .select_from(TaskEvidence)
                .where(TaskEvidence.task_id == task_id)
            )
        ).scalar_one()
    )


async def test_extraction_persists_sources_and_reconstructs_the_quote(
    db_session: AsyncSession,
) -> None:
    actor = await actors.make_actor(db_session)
    audio, first, second = await _class(db_session, actor)
    task = _task(actor, "extraction", audio=audio)
    db_session.add(task)
    await db_session.flush()

    spans = (
        EvidenceSpan(segment_id=first.id, char_start=0, char_end=len(FIRST), quote=FIRST),
        EvidenceSpan(segment_id=second.id, char_start=0, char_end=len(SECOND), quote=SECOND),
    )
    sources = await persist_task_spans(
        db_session,
        task=task,
        audio_id=audio.id,
        transcript_id=first.transcript_id,
        transcript_version=1,
        spans=spans,
    )
    assert len(sources) == 2
    assert await _source_count(db_session, task.id) == 2
    assert await _evidence_count(db_session, task.id) == 2

    stored = await load_task_evidence(
        db_session, user_id=actor.user_id, task_id=task.id, task_version=1
    )
    assert [item.evidence.quote for item in stored] == [FIRST, SECOND]
    assert [item.evidence.ordinal for item in stored] == [0, 1]
    assert stored[0].evidence.inicio_char == 0
    assert stored[0].evidence.fin_char == len(FIRST)
    assert stored[0].evidence.char_start == first.char_start
    assert stored[0].evidence.char_end == first.char_start + len(FIRST)
    assert (stored[0].evidence.t_start, stored[0].evidence.t_end) == (0.0, 5.0)
    assert stored[0].evidence.reference == "00:00"
    assert stored[1].evidence.inicio_char == 0
    assert stored[1].evidence.fin_char == len(SECOND)
    assert stored[1].evidence.char_start == second.char_start
    assert stored[1].evidence.char_end == second.char_start + len(SECOND)
    assert (stored[1].evidence.t_start, stored[1].evidence.t_end) == (None, None)
    assert stored[1].evidence.reference == "segment 1"
    assert stored[0].task_version == 1
    assert stored[0].audio_id == audio.id
    assert stored[0].transcript_version == 1


async def test_date_fields_are_persisted_not_re_derived(db_session: AsyncSession) -> None:
    actor = await actors.make_actor(db_session)
    task = _task(actor, "manual")
    task.due_date = date(2026, 9, 21)
    task.all_day = True
    task.date_status = "resolved"
    task.date_text = "Monday, September 21"
    task.date_reason = "the teacher announced the date"
    db_session.add(task)
    await db_session.flush()
    task_id = task.id

    db_session.expire_all()
    stored = await db_session.get(Task, task_id)
    assert stored is not None
    assert stored.date_status == "resolved"
    assert stored.due_date == date(2026, 9, 21)
    assert stored.due_at is None
    assert stored.timezone == "America/Bogota"
    assert stored.date_text == "Monday, September 21"
    assert stored.date_reason == "the teacher announced the date"
    assert stored.due_version == 1


async def test_manual_task_has_no_sources_and_rejects_a_span(
    db_session: AsyncSession,
) -> None:
    actor = await actors.make_actor(db_session)
    audio, first, _second = await _class(db_session, actor)
    task = _task(actor, "manual")
    db_session.add(task)
    await db_session.flush()

    sources = await persist_task_spans(
        db_session,
        task=task,
        audio_id=audio.id,
        transcript_id=first.transcript_id,
        transcript_version=1,
        spans=(),
    )
    assert sources == ()
    assert await _source_count(db_session, task.id) == 0
    assert await _evidence_count(db_session, task.id) == 0

    with pytest.raises(ProvenanceInvalid):
        await persist_task_spans(
            db_session,
            task=task,
            audio_id=audio.id,
            transcript_id=first.transcript_id,
            transcript_version=1,
            spans=(EvidenceSpan(first.id, 0, len(FIRST), FIRST),),
        )
    assert await _source_count(db_session, task.id) == 0
    assert await _evidence_count(db_session, task.id) == 0


async def test_extraction_requires_at_least_one_span(db_session: AsyncSession) -> None:
    actor = await actors.make_actor(db_session)
    audio = await actors.make_audio(db_session, actor)
    task = _task(actor, "extraction", audio=audio)
    db_session.add(task)
    await db_session.flush()

    with pytest.raises(ProvenanceInvalid):
        await persist_task_spans(
            db_session,
            task=task,
            audio_id=audio.id,
            transcript_id=uuid.uuid4(),
            transcript_version=1,
            spans=(),
        )
    assert await _source_count(db_session, task.id) == 0
    assert await _evidence_count(db_session, task.id) == 0


async def test_chat_allows_a_general_suggestion_and_persists_a_cited_span(
    db_session: AsyncSession,
) -> None:
    actor = await actors.make_actor(db_session)
    audio, first, _second = await _class(db_session, actor)

    suggestion = _task(actor, "chat")
    db_session.add(suggestion)
    await db_session.flush()
    assert (
        await persist_task_spans(
            db_session,
            task=suggestion,
            audio_id=audio.id,
            transcript_id=first.transcript_id,
            transcript_version=1,
            spans=(),
        )
    ) == ()
    assert await _source_count(db_session, suggestion.id) == 0
    assert await _evidence_count(db_session, suggestion.id) == 0

    cited = _task(actor, "chat", audio=audio)
    db_session.add(cited)
    await db_session.flush()
    await persist_task_spans(
        db_session,
        task=cited,
        audio_id=audio.id,
        transcript_id=first.transcript_id,
        transcript_version=1,
        spans=(EvidenceSpan(first.id, 0, len(FIRST), FIRST),),
    )
    assert await _source_count(db_session, cited.id) == 1
    assert await _evidence_count(db_session, cited.id) == 1
    stored = await load_task_evidence(
        db_session, user_id=actor.user_id, task_id=cited.id, task_version=1
    )
    assert stored[0].evidence.quote == FIRST


async def test_invalid_span_is_rejected_without_writing(db_session: AsyncSession) -> None:
    actor = await actors.make_actor(db_session)
    audio, first, _second = await _class(db_session, actor)
    _other_audio, other_first, _ = await _class(db_session, actor)
    foreign = await actors.make_actor(db_session)
    _foreign_audio, foreign_first, _ = await _class(db_session, foreign)
    task = _task(actor, "extraction", audio=audio)
    db_session.add(task)
    await db_session.flush()

    def spans(*items: EvidenceSpan) -> tuple[EvidenceSpan, ...]:
        return tuple(items)

    cases = (
        # The range exceeds the persisted segment text.
        spans(EvidenceSpan(first.id, 0, len(FIRST) + 1, "x" * (len(FIRST) + 1))),
        # The quote has the right length but is not the segment slice.
        spans(EvidenceSpan(first.id, 0, len(FIRST), "x" * len(FIRST))),
        # The same span twice in one proposal.
        spans(
            EvidenceSpan(first.id, 0, len(FIRST), FIRST),
            EvidenceSpan(first.id, 0, len(FIRST), FIRST),
        ),
        # A real segment of the same tenant but a different transcript.
        spans(EvidenceSpan(other_first.id, 0, len(FIRST), FIRST)),
        # A real segment of another tenant.
        spans(EvidenceSpan(foreign_first.id, 0, len(FIRST), FIRST)),
    )
    for case in cases:
        with pytest.raises(ProvenanceInvalid):
            await persist_task_spans(
                db_session,
                task=task,
                audio_id=audio.id,
                transcript_id=first.transcript_id,
                transcript_version=1,
                spans=case,
            )
        assert await _source_count(db_session, task.id) == 0
        assert await _evidence_count(db_session, task.id) == 0


async def test_in_place_reanalysis_replaces_evidence_and_bumps_due_version_only_once(
    db_session: AsyncSession,
) -> None:
    actor = await actors.make_actor(db_session)
    audio, first, _second = await _class(db_session, actor)
    run = ProcessingRun(
        user_id=actor.user_id,
        audio_id=audio.id,
        stage="analyze",
        transcript_version=1,
        config_version="test-config",
        prompt_version="v1",
        status="running",
    )
    task = _task(actor, "extraction", audio=audio)
    task.due_date = date(2026, 9, 28)
    task.all_day = True
    task.date_status = "resolved"
    task.date_text = "Monday, September 28"
    db_session.add_all([run, task])
    await db_session.flush()
    await persist_task_spans(
        db_session,
        task=task,
        audio_id=audio.id,
        transcript_id=first.transcript_id,
        transcript_version=1,
        spans=(EvidenceSpan(first.id, 0, len(FIRST), FIRST),),
    )
    task_id = task.id
    run_id = run.id
    audio_id = audio.id
    subject_id = audio.subject_id
    transcript_id = first.transcript_id
    segment_id = first.id
    original_evidence = (
        await db_session.execute(select(TaskEvidence).where(TaskEvidence.task_id == task_id))
    ).scalar_one()
    original_id = original_evidence.id

    async def update(title: str, mention: str) -> None:
        proposal = _proposal(audio, segment_id, title=title, mention=mention)
        existing = await load_existing_proposals(
            db_session, user_id=actor.user_id, audio_id=audio_id
        )
        writes = plan_task_writes(run_id, (proposal,), existing, set())
        assert writes[0].kind is WriteKind.UPDATE
        await apply_task_writes(
            db_session,
            user_id=actor.user_id,
            audio_id=audio_id,
            subject_id=subject_id,
            transcript_id=transcript_id,
            transcript_version=1,
            run_id=run_id,
            writes=writes,
        )

    # The due representation is unchanged: the title changes but the reminder generation
    # must not be invalidated.
    await update("First review", "2026-09-28")
    await db_session.refresh(task)
    stored = task
    assert stored.titulo == "First review"
    assert stored.due_version == 1
    assert stored.date_text == "Monday, September 28"
    assert await _source_count(db_session, task_id) == 1
    assert await _evidence_count(db_session, task_id) == 1
    replacement = (
        await db_session.execute(select(TaskEvidence).where(TaskEvidence.task_id == task_id))
    ).scalar_one()
    assert replacement.id != original_id
    assert replacement.quote == FIRST
    assert replacement.task_version == 1

    # A real due change advances `due_version` and the canonical text together.
    await update("Second review", "2026-10-05")
    await db_session.refresh(task)
    stored = task
    assert stored.titulo == "Second review"
    assert stored.due_date == date(2026, 10, 5)
    assert stored.date_text == "Monday, October 5"
    assert stored.due_version == 2
    assert await _evidence_count(db_session, task_id) == 1


async def test_confirmed_task_gains_a_revision_without_touching_active_evidence(
    db_session: AsyncSession,
) -> None:
    actor = await actors.make_actor(db_session)
    audio, first, _second = await _class(db_session, actor)
    run = ProcessingRun(
        user_id=actor.user_id,
        audio_id=audio.id,
        stage="analyze",
        transcript_version=1,
        config_version="test-config",
        prompt_version="v1",
        status="running",
    )
    task = _task(actor, "extraction", audio=audio)
    task.due_date = date(2026, 9, 28)
    task.all_day = True
    task.date_status = "resolved"
    task.date_text = "Monday, September 28"
    task.status = "confirmed"
    task.confirmed_at = NOW
    db_session.add_all([run, task])
    await db_session.flush()
    task_id = task.id
    await persist_task_spans(
        db_session,
        task=task,
        audio_id=audio.id,
        transcript_id=first.transcript_id,
        transcript_version=1,
        spans=(EvidenceSpan(first.id, 0, len(FIRST), FIRST),),
    )
    active_id = (
        await db_session.execute(
            select(TaskEvidence.id).where(TaskEvidence.task_id == task_id)
        )
    ).scalar_one()

    proposal = _proposal(audio, first.id, title="Reanalysis title", mention="2026-10-05")
    existing = await load_existing_proposals(
        db_session, user_id=actor.user_id, audio_id=audio.id
    )
    writes = plan_task_writes(run.id, (proposal,), existing, set())
    assert writes[0].kind is WriteKind.REVISE
    await apply_task_writes(
        db_session,
        user_id=actor.user_id,
        audio_id=audio.id,
        subject_id=audio.subject_id,
        transcript_id=first.transcript_id,
        transcript_version=1,
        run_id=run.id,
        writes=writes,
    )

    db_session.expire_all()
    stored = await db_session.get(Task, task_id)
    assert stored is not None
    assert stored.status == "confirmed"
    assert stored.titulo != "Reanalysis title"
    assert stored.due_date == date(2026, 9, 28)
    assert stored.date_text == "Monday, September 28"
    assert stored.due_version == 1
    assert (
        await db_session.execute(select(TaskEvidence.id).where(TaskEvidence.task_id == task_id))
    ).scalar_one() == active_id
    revision = (
        await db_session.execute(select(TaskRevision).where(TaskRevision.task_id == task_id))
    ).scalar_one()
    assert revision.status == "pending_confirmation"
    assert revision.proposed_fields["titulo"] == "Reanalysis title"
    assert revision.proposed_fields["date_text"] == "Monday, October 5"
    assert revision.evidence_validated is True


async def test_load_returns_empty_for_an_unknown_version_and_rejects_a_tampered_quote(
    db_session: AsyncSession,
) -> None:
    actor = await actors.make_actor(db_session)
    audio, first, _second = await _class(db_session, actor)
    task = _task(actor, "extraction", audio=audio)
    db_session.add(task)
    await db_session.flush()
    await persist_task_spans(
        db_session,
        task=task,
        audio_id=audio.id,
        transcript_id=first.transcript_id,
        transcript_version=1,
        spans=(EvidenceSpan(first.id, 0, len(FIRST), FIRST),),
    )

    assert (
        await load_task_evidence(
            db_session, user_id=actor.user_id, task_id=task.id, task_version=2
        )
        == ()
    )

    await db_session.execute(
        update(TaskEvidence)
        .where(TaskEvidence.task_id == task.id)
        .values(quote="x" * len(FIRST))
        .execution_options(synchronize_session=False)
    )
    with pytest.raises(ProvenanceInvalid):
        await load_task_evidence(
            db_session, user_id=actor.user_id, task_id=task.id, task_version=1
        )


async def test_evidence_is_hidden_from_another_tenant_by_rls(
    db_session: AsyncSession,
) -> None:
    actor = await actors.make_actor(db_session)
    other = await actors.make_actor(db_session)
    audio, first, _second = await _class(db_session, actor)
    task = _task(actor, "extraction", audio=audio)
    db_session.add(task)
    await db_session.flush()
    await persist_task_spans(
        db_session,
        task=task,
        audio_id=audio.id,
        transcript_id=first.transcript_id,
        transcript_version=1,
        spans=(EvidenceSpan(first.id, 0, len(FIRST), FIRST),),
    )

    # The service also scopes by `user_id`; this is the RLS read as the runtime role, with
    # the tenant bound exactly like `app/core/db.py` does.
    await db_session.execute(text(f'SET LOCAL ROLE "{APP_ROLE}"'))
    await db_session.execute(
        text("SELECT set_config('app.user_id', :user_id, true)"),
        {"user_id": str(actor.user_id)},
    )
    assert (
        await db_session.execute(text("SELECT count(*) FROM task_evidence"))
    ).scalar_one() == 1
    await db_session.execute(
        text("SELECT set_config('app.user_id', :user_id, true)"),
        {"user_id": str(other.user_id)},
    )
    assert (
        await db_session.execute(text("SELECT count(*) FROM task_evidence"))
    ).scalar_one() == 0
    assert (
        await load_task_evidence(
            db_session, user_id=other.user_id, task_id=task.id, task_version=1
        )
        == ()
    )


async def test_deleting_a_source_cascades_to_its_evidence(db_session: AsyncSession) -> None:
    actor = await actors.make_actor(db_session)
    audio, first, _second = await _class(db_session, actor)
    task = _task(actor, "extraction", audio=audio)
    db_session.add(task)
    await db_session.flush()
    await persist_task_spans(
        db_session,
        task=task,
        audio_id=audio.id,
        transcript_id=first.transcript_id,
        transcript_version=1,
        spans=(EvidenceSpan(first.id, 0, len(FIRST), FIRST),),
    )
    assert await _evidence_count(db_session, task.id) == 1

    await db_session.execute(delete(TaskSource).where(TaskSource.task_id == task.id))
    await db_session.flush()
    assert await _source_count(db_session, task.id) == 0
    assert await _evidence_count(db_session, task.id) == 0


def test_origin_rule_is_closed() -> None:
    span = EvidenceSpan(uuid.uuid4(), 0, 1, "x")
    validate_provenance("manual", ())
    validate_provenance("chat", ())
    validate_provenance("chat", (span,))
    validate_provenance("extraction", (span,))
    for source, spans in (
        ("manual", (span,)),
        ("extraction", ()),
        ("unknown", ()),
        ("unknown", (span,)),
    ):
        with pytest.raises(ProvenanceInvalid):
            validate_provenance(source, spans)

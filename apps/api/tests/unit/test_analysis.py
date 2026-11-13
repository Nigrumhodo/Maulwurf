"""U-S3-JL-05: reanalysis publishes a revision and does not overwrite protected tasks.

Also covers the L3.6 rules that sit next to that decision: the model's clock is discarded,
a failed window publishes nothing, and a replay of the same run does not insert again.
"""

import asyncio
import json
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest

from app.schemas.analysis import DateStatus
from app.services.analysis import (
    AnalysisBatch,
    AnalysisPayloadError,
    ClassContext,
    ExistingProposal,
    PreparedProposal,
    ProposalFields,
    WriteKind,
    analyze_transcript,
    parse_extraction_payload,
    plan_task_writes,
    proposal_fields,
    update_is_safe,
)
from app.services.analysis_validation import ClassTranscript
from app.services.analysis_windows import AggregatedProposal, EvidenceSpan
from app.services.chunking import SegmentInput
from app.services.llm import LLMRateLimited
from prompts.registry import SUMMARY_PROMPT_VERSION, load_summary_prompts

SEGMENT = UUID("3f1c9a52-7d4e-4b8a-9c1e-2a6b5d8e0f11")
TEXT = "the midterm exam will be on Monday, September 21"
NOW = datetime(2026, 9, 16, 15, tzinfo=UTC)
RUN = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


_SUMMARY = (
    '{"resumen_clase":"The class announced one exam. Students should review. '
    'Nothing else was assigned.","temas":["midterm"]}'
)


class _Scripted:
    def __init__(self, payloads: list[str | Exception]) -> None:
        self._payloads = list(payloads)

    async def complete(self, *, system: str, user: str) -> str:
        if system == load_summary_prompts().system:
            return _SUMMARY
        nxt = self._payloads.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def _context(text: str = TEXT, *, segments: int = 1) -> ClassContext:
    piece = text
    rows: list[SegmentInput] = []
    view_segments: dict[UUID, str] = {}
    cursor = 0
    for ordinal in range(segments):
        segment_id = SEGMENT if segments == 1 else uuid4()
        body = piece if segments == 1 else f"Sentence {ordinal} mentions the exam on Friday. "
        start = cursor
        cursor += len(body)
        rows.append(
            SegmentInput(
                id=segment_id,
                ordinal=ordinal,
                text=body,
                char_start=start,
                char_end=cursor,
                t_start=None,
                t_end=None,
            )
        )
        view_segments[segment_id] = body
    transcript = "".join(row.text for row in rows)
    return ClassContext(
        class_date=date(2026, 9, 16),
        class_timezone="America/Mexico_City",
        subject="Calculus II",
        teacher="unknown",
        transcript_text=transcript,
        segments=tuple(rows),
        view=ClassTranscript(
            user_id=uuid4(),
            audio_id=uuid4(),
            transcript_id=uuid4(),
            transcript_version=1,
            segments=view_segments,
        ),
    )


def _payload(segment_id: UUID, quote: str, *, mention: dict[str, object] | None = None) -> str:
    mention_json = json.dumps(mention or {"kind": "all_day", "due_date": "2026-09-21"})
    return (
        '{"items":[{"tipo":"examen","titulo":"Midterm","detalle":null,'
        '"due_date":null,"due_at":"2026-09-21T00:00:00-06:00","timezone":"UTC",'
        '"all_day":false,"date_status":"resolved","confidence_score":0.4,'
        f'"mencion":{mention_json},'
        '"fuente":{"spans":[{"segment_id":"'
        + str(segment_id)
        + '","inicio_char":0,"fin_char":'
        + str(len(quote))
        + ',"cita_textual":"'
        + quote
        + '"}]}}],"resumen_clase":"The class announced one exam. Students should review. '
        'Nothing else was assigned.","temas":["midterm"]}'
    )


def _proposal(
    segment_id: UUID = SEGMENT, quote: str = TEXT
) -> AggregatedProposal[PreparedProposal]:
    parsed = parse_extraction_payload(
        _payload(segment_id, quote),
        class_date=date(2026, 9, 16),
        class_timezone="America/Mexico_City",
        now=NOW,
    )
    item = parsed.result.items[0]
    return AggregatedProposal(
        value=PreparedProposal(item=item, date_reason=parsed.reasons[0]),
        evidence=(
            EvidenceSpan(
                segment_id=segment_id,
                char_start=0,
                char_end=len(quote),
                quote=quote,
            ),
        ),
    )


def _existing(
    proposal: AggregatedProposal[PreparedProposal],
    *,
    status: str = "needs_review",
    version: int = 1,
    titulo: str | None = None,
) -> ExistingProposal:
    fields = proposal_fields(proposal)
    if titulo is not None:
        fields = ProposalFields(
            tipo=fields.tipo,
            titulo=titulo,
            detalle=fields.detalle,
            due_date=fields.due_date,
            due_at=fields.due_at,
            timezone=fields.timezone,
            all_day=fields.all_day,
            date_status=fields.date_status,
            confidence_score=fields.confidence_score,
            date_reason=fields.date_reason,
        )
    return ExistingProposal(
        task_id=uuid4(),
        identity=proposal.identity,
        status=status,
        version=version,
        fields=fields,
    )


def test_the_server_discards_an_invented_midnight() -> None:
    parsed = parse_extraction_payload(
        _payload(SEGMENT, TEXT),
        class_date=date(2026, 9, 16),
        class_timezone="America/Mexico_City",
        now=NOW,
    )
    item = parsed.result.items[0]
    assert item.all_day is True
    assert item.due_date == date(2026, 9, 21)
    assert item.due_at is None
    assert item.date_status is DateStatus.RESOLVED
    assert item.timezone == "America/Mexico_City"


def test_next_friday_stays_ambiguous() -> None:
    parsed = parse_extraction_payload(
        _payload(SEGMENT, TEXT, mention={"kind": "weekday", "weekday": 4, "qualifier": "next"}),
        class_date=date(2026, 9, 16),
        class_timezone="America/Mexico_City",
        now=NOW,
    )
    item = parsed.result.items[0]
    assert item.date_status is DateStatus.AMBIGUOUS
    assert item.due_date is None
    assert item.due_at is None
    assert parsed.reasons[0] == "ambiguous_relative"


def test_invalid_json_fails_before_any_item_is_kept() -> None:
    with pytest.raises(AnalysisPayloadError):
        parse_extraction_payload(
            "not-json",
            class_date=date(2026, 9, 16),
            class_timezone="America/Mexico_City",
            now=NOW,
        )


async def test_a_later_window_failure_returns_no_batch() -> None:
    context = _context(" ".join(f"Word{index}." for index in range(30)))
    model = _Scripted(
        [
            _payload(context.segments[0].id, context.segments[0].text),
            LLMRateLimited("quota", details={"status": 429}),
        ]
    )
    with pytest.raises(LLMRateLimited):
        await analyze_transcript(
            context,
            model,
            now=NOW,
            count_tokens=lambda text: max(1, len(text.split())),
            target_tokens=4,
            overlap_tokens=1,
        )


async def test_the_provider_loop_stops_at_the_deadline() -> None:
    class _Slow:
        async def complete(self, *, system: str, user: str) -> str:
            await asyncio.sleep(5)
            return "{}"

    with pytest.raises(TimeoutError):
        await analyze_transcript(
            _context(),
            _Slow(),
            now=NOW,
            count_tokens=lambda text: max(1, len(text.split())),
            deadline_seconds=0.05,
        )


def test_a_confirmed_or_moved_task_is_not_updated_in_place() -> None:
    assert update_is_safe("needs_review", 1, 1) is True
    assert update_is_safe("confirmed", 1, 1) is False
    assert update_is_safe("dismissed", 1, 1) is False
    assert update_is_safe("completed", 1, 1) is False
    assert update_is_safe("needs_review", 2, 1) is False
    assert update_is_safe("needs_review", 2, 2) is False


async def test_a_transcript_over_the_window_budget_is_rejected() -> None:
    context = _context(" ".join(f"Word{index}." for index in range(30)))
    with pytest.raises(AnalysisPayloadError):
        await analyze_transcript(
            context,
            _Scripted([]),
            now=NOW,
            count_tokens=lambda text: max(1, len(text.split())),
            target_tokens=4,
            overlap_tokens=1,
            max_windows=1,
        )


async def test_a_resolved_mention_survives_one_window() -> None:
    context = _context()
    batch = await analyze_transcript(
        context,
        _Scripted([_payload(SEGMENT, TEXT)]),
        now=NOW,
        count_tokens=lambda text: len(text.split()) or 1,
    )
    assert isinstance(batch, AnalysisBatch)
    assert len(batch.proposals) == 1
    assert batch.proposals[0].value.item.due_at is None
    assert batch.proposals[0].evidence[0].quote == TEXT
    assert batch.summary.startswith("The class announced one exam.")
    assert batch.topics == ("midterm",)
    assert batch.prompt_version == SUMMARY_PROMPT_VERSION


async def test_a_summary_that_is_too_short_fails_the_batch() -> None:
    class _Short:
        async def complete(self, *, system: str, user: str) -> str:
            if system == load_summary_prompts().system:
                return '{"resumen_clase":"Too short. Still short.","temas":["midterm"]}'
            return _payload(SEGMENT, TEXT)

    with pytest.raises(AnalysisPayloadError):
        await analyze_transcript(
            _context(),
            _Short(),
            now=NOW,
            count_tokens=lambda text: len(text.split()) or 1,
        )


def test_a_confirmed_task_becomes_a_pending_revision() -> None:
    proposal = _proposal()
    current = _existing(proposal, status="confirmed", titulo="Confirmed title")
    writes = plan_task_writes(RUN, (proposal,), (current,), set())
    assert [(write.kind, write.task_id, write.base_version) for write in writes] == [
        (WriteKind.REVISE, current.task_id, 1)
    ]


def test_an_edited_task_is_not_overwritten() -> None:
    proposal = _proposal()
    current = _existing(
        proposal, status="needs_review", version=2, titulo="Edited title"
    )
    writes = plan_task_writes(RUN, (proposal,), (current,), set())
    assert writes[0].kind is WriteKind.REVISE
    assert writes[0].task_id == current.task_id


def test_a_dismissed_or_completed_task_is_not_revised() -> None:
    proposal = _proposal()
    for status in ("dismissed", "completed"):
        current = _existing(proposal, status=status, titulo="Closed title")
        writes = plan_task_writes(RUN, (proposal,), (current,), set())
        assert writes[0].kind is WriteKind.SKIP


def test_an_unchanged_proposal_does_not_create_a_revision() -> None:
    proposal = _proposal()
    current = _existing(proposal, status="confirmed")
    writes = plan_task_writes(RUN, (proposal,), (current,), set())
    assert writes[0].kind is WriteKind.SKIP


def test_the_same_run_does_not_publish_twice() -> None:
    proposal = _proposal()
    current = _existing(proposal, status="confirmed", titulo="Confirmed title")
    first = plan_task_writes(RUN, (proposal,), (current,), set())
    assert first[0].kind is WriteKind.REVISE
    second = plan_task_writes(RUN, (proposal,), (current,), {current.task_id})
    assert second[0].kind is WriteKind.SKIP


def test_an_untouched_proposal_is_updated_in_place() -> None:
    proposal = _proposal()
    current = _existing(proposal, titulo="Old title")
    writes = plan_task_writes(RUN, (proposal,), (current,), set())
    assert writes[0].kind is WriteKind.UPDATE
    assert writes[0].task_id == current.task_id


def test_an_equal_replay_does_not_insert_another_task() -> None:
    proposal = _proposal()
    current = _existing(proposal)
    writes = plan_task_writes(RUN, (proposal, proposal), (current,), set())
    assert [write.kind for write in writes] == [WriteKind.SKIP, WriteKind.SKIP]


def test_new_evidence_inserts_one_needs_review_proposal() -> None:
    proposal = _proposal()
    writes = plan_task_writes(RUN, (proposal,), (), set())
    assert writes[0].kind is WriteKind.INSERT
    assert writes[0].task_id is None
    assert proposal.value.item.date_status is DateStatus.RESOLVED

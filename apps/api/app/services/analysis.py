"""Windowed class analysis (L3.6) over the L3.5 publication rules.

`analyze_transcript` is pure aside from the injected model: it plans windows, asks the
model once per window, then once more for the v1 class summary, and returns nothing until
every call has answered. A transport error, a payload that is not the §M4 object, or a
summary that is not 3–5 sentences fails the whole run, so a later call cannot publish the
items of an earlier one.

Dates come from `resolve_due`. The model's `due_date`, `due_at`, `all_day` and
`date_status` are discarded. A mention that cannot be resolved drops that item and
nothing else. Quotes still have to be the segment slice (`validate_analysis`); a miss
drops the item. The surviving items are deduped by evidence and then turned into
inserts, in-place updates, or pending revisions. A confirmed or edited task is never
overwritten, and a revision is never activated here.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings as live_settings
from app.models import Segment, Task, TaskRevision, TaskSource
from app.schemas.analysis import AnalysisResult, DateStatus, ProposedItem
from app.services.analysis_validation import (
    AnalysisValidationError,
    ClassTranscript,
    validate_analysis,
)
from app.services.analysis_windows import (
    DEFAULT_TARGET_TOKENS,
    AggregatedProposal,
    AnalysisWindow,
    EvidenceKey,
    EvidenceSpan,
    PublicationKind,
    PublishedIdentity,
    WindowCitation,
    aggregate_window_citations,
    plan_run_publication,
    plan_windows,
)
from app.services.chunking import SegmentInput, TokenCounter, count_tokens_default
from app.services.date_resolution import (
    DateMention,
    DateResolutionError,
    MentionKind,
    ResolvedDue,
    WeekdayQualifier,
    render_due_text,
    resolve_due,
)
from app.services.llm import ChatMessage, LLMProvider
from app.services.summary import SummaryRejected, parse_summary_payload
from app.services.task_evidence import persist_task_spans
from prompts.registry import (
    load_extraction_prompts,
    load_summary_prompts,
    render_summary_prompt,
    render_window_prompt,
)

logger = logging.getLogger(__name__)

# Below the analyze job timeout (600 s). Each provider call may use the 60 s LLM timeout,
# so the window count does not bound wall time; this deadline does, and it leaves room to
# record the run as failed before ARQ cancels the job.
ANALYSIS_DEADLINE_S = 540.0

# Provisional output budget until a measured extraction profile replaces it.
EXTRACTION_MAX_TOKENS = 2048
_OTHER_RUN = UUID("00000000-0000-4000-8000-000000000001")
_PROTECTED_STATUSES = frozenset({"confirmed", "dismissed", "completed"})


class AnalysisPayloadError(Exception):
    """The model did not return one §M4 object. The run writes nothing."""

    code = "analysis_unexpected"

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class ExtractionModel(Protocol):
    """One complete extraction document. Tests inject a deterministic fake."""

    async def complete(self, *, system: str, user: str) -> str: ...


@dataclass(frozen=True)
class ClassContext:
    """The persisted class the analyzer is allowed to read."""

    class_date: date
    class_timezone: str
    subject: str
    teacher: str
    transcript_text: str
    segments: tuple[SegmentInput, ...]
    view: ClassTranscript


@dataclass(frozen=True)
class PreparedProposal:
    """One item whose date is the resolver's and whose quote survived validation."""

    item: ProposedItem
    date_reason: str | None


@dataclass(frozen=True)
class AnalysisBatch:
    """The complete publication of one run. Empty means the class stated nothing usable."""

    proposals: tuple[AggregatedProposal[PreparedProposal], ...]
    summary: str
    topics: tuple[str, ...]
    prompt_version: str


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


@dataclass(frozen=True)
class ExistingProposal:
    """A task already stored for this class, keyed by its evidence identity."""

    task_id: UUID
    identity: frozenset[EvidenceKey]
    status: str
    version: int
    fields: ProposalFields


class WriteKind(StrEnum):
    """How one proposal meets the tasks that already exist."""

    INSERT = "insert"
    UPDATE = "update"
    REVISE = "revise"
    SKIP = "skip"


@dataclass(frozen=True)
class TaskWrite:
    proposal: AggregatedProposal[PreparedProposal]
    kind: WriteKind
    task_id: UUID | None
    base_version: int | None


@dataclass(frozen=True)
class ParsedExtraction:
    result: AnalysisResult
    reasons: tuple[str | None, ...]


class StreamingExtractionModel:
    """Join the chat adapter's text deltas into the one JSON object extraction needs."""

    def __init__(self, provider: LLMProvider, *, max_tokens: int = EXTRACTION_MAX_TOKENS) -> None:
        if max_tokens < 1:
            raise ValueError("max_tokens must be >= 1")
        self._provider = provider
        self._max_tokens = max_tokens

    async def complete(self, *, system: str, user: str) -> str:
        parts: list[str] = []
        stream: AsyncIterator[str] = self._provider.stream(
            (
                ChatMessage(role="system", content=system),
                ChatMessage(role="user", content=user),
            ),
            max_tokens=self._max_tokens,
        )
        async for delta in stream:
            parts.append(delta)
        return "".join(parts)


def mention_from_payload(payload: object) -> DateMention:
    """Parse the closed mention. Anything else raises and the item is dropped."""
    if not isinstance(payload, dict) or isinstance(payload.get("kind"), bool):
        raise DateResolutionError("mention_invalid", "the date mention does not match its kind")
    kind_raw = payload.get("kind")
    if not isinstance(kind_raw, str):
        raise DateResolutionError("mention_invalid", "the date mention does not match its kind")
    try:
        kind = MentionKind(kind_raw)
    except ValueError as exc:
        raise DateResolutionError(
            "mention_invalid", "the date mention does not match its kind"
        ) from exc
    allowed = {"kind", "due_date", "local_time", "day_offset", "weekday", "qualifier"}
    if set(payload) - allowed:
        raise DateResolutionError("mention_invalid", "the date mention does not match its kind")
    return DateMention(
        kind=kind,
        due_date=_payload_date(payload, "due_date"),
        local_time=_payload_time(payload, "local_time"),
        day_offset=_payload_int(payload, "day_offset"),
        weekday=_payload_int(payload, "weekday"),
        qualifier=_payload_qualifier(payload),
    )


def parse_extraction_payload(
    raw: str,
    *,
    class_date: date,
    class_timezone: str,
    now: datetime,
) -> ParsedExtraction:
    """Replace every model date with `resolve_due` and validate the §M4 object.

    A missing or impossible mention drops that item. A payload that is not one object,
    or whose remaining items break the schema, raises `AnalysisPayloadError` and must
    not be persisted.
    """
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AnalysisPayloadError("the extraction payload is not JSON") from exc
    if not isinstance(payload, dict):
        raise AnalysisPayloadError("the extraction payload is not an object")
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise AnalysisPayloadError("the extraction payload has no item list")
    kept: list[dict[str, object]] = []
    reasons: list[str | None] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            raise AnalysisPayloadError("an extraction item is not an object")
        rewritten = _rewrite_item(
            raw_item,
            class_date=class_date,
            class_timezone=class_timezone,
            now=now,
        )
        if rewritten is None:
            continue
        item, reason = rewritten
        kept.append(item)
        reasons.append(reason)
    body = {
        "items": kept,
        "resumen_clase": payload.get("resumen_clase"),
        "temas": payload.get("temas"),
    }
    try:
        result = AnalysisResult.model_validate(body)
    except ValidationError as exc:
        raise AnalysisPayloadError("the extraction payload does not match the schema") from exc
    if len(result.items) != len(reasons):
        raise AnalysisPayloadError("the extraction payload does not match the schema")
    return ParsedExtraction(result=result, reasons=tuple(reasons))


async def analyze_transcript(
    context: ClassContext,
    model: ExtractionModel,
    *,
    now: datetime,
    count_tokens: TokenCounter = count_tokens_default,
    target_tokens: int | None = None,
    overlap_tokens: int | None = None,
    max_windows: int | None = None,
    deadline_seconds: float | None = None,
) -> AnalysisBatch:
    """Extract every window, then return one batch. A failed window raises before return.

    The window count bounds tokens. Wall time is a separate deadline, below the analyze
    job timeout, around the provider calls only. If it expires, nothing is returned and
    nothing is written. A transcript that cannot fit the token budget raises the same way.
    """
    prompts = load_extraction_prompts()
    cap = live_settings.analysis_max_windows if max_windows is None else max_windows
    if cap < 1:
        raise ValueError("max_windows must be >= 1")
    window_target = DEFAULT_TARGET_TOKENS if target_tokens is None else target_tokens
    try:
        plan = plan_windows(
            transcript_text=context.transcript_text,
            segments=context.segments,
            count_tokens=count_tokens,
            max_total_tokens=cap * window_target,
            **_window_sizes(target_tokens, overlap_tokens),
        )
    except ValueError as exc:
        raise AnalysisPayloadError("the transcript exceeds the analysis window budget") from exc
    if plan.window_count > cap:
        raise AnalysisPayloadError("the transcript exceeds the analysis window budget")
    logger.info("analyze.windows count=%d tokens=%d", plan.window_count, plan.total_tokens)
    deadline = ANALYSIS_DEADLINE_S if deadline_seconds is None else deadline_seconds
    WindowBatch = tuple[PreparedProposal, tuple[WindowCitation, ...]]
    batches: list[tuple[AnalysisWindow, list[WindowBatch]]] = []
    notes: list[tuple[str, tuple[str, ...]]] = []
    async with asyncio.timeout(deadline):
        for window in plan.windows:
            if not window.segments:
                continue
            raw = await model.complete(
                system=prompts.system,
                user=render_window_prompt(
                    subject=context.subject,
                    teacher=context.teacher,
                    class_date=context.class_date,
                    class_timezone=context.class_timezone,
                    segments=tuple(
                        (part.segment_id, part.text, part.local_char_start)
                        for part in window.segments
                    ),
                ),
            )
            parsed = parse_extraction_payload(
                raw,
                class_date=context.class_date,
                class_timezone=context.class_timezone,
                now=now,
            )
            notes.append((parsed.result.resumen_clase, tuple(parsed.result.temas)))
            batches.append((window, _accepted(parsed, window, context.view)))
        if not notes:
            raise AnalysisPayloadError("the class produced no summary notes")
        summary_prompts = load_summary_prompts()
        raw_summary = await model.complete(
            system=summary_prompts.system,
            user=render_summary_prompt(
                subject=context.subject,
                teacher=context.teacher,
                class_date=context.class_date,
                class_timezone=context.class_timezone,
                windows=tuple(notes),
            ),
        )
    try:
        class_summary = parse_summary_payload(raw_summary)
    except SummaryRejected as exc:
        raise AnalysisPayloadError("the summary payload does not match the schema") from exc
    aggregated = aggregate_window_citations(batches)
    return AnalysisBatch(
        proposals=tuple(aggregated),
        summary=class_summary.text,
        topics=class_summary.topics,
        prompt_version=class_summary.prompt_version,
    )


def proposal_fields(proposal: AggregatedProposal[PreparedProposal]) -> ProposalFields:
    item = proposal.value.item
    return ProposalFields(
        tipo=item.tipo,
        titulo=item.titulo,
        detalle=item.detalle,
        due_date=item.due_date,
        due_at=item.due_at,
        timezone=item.timezone,
        all_day=item.all_day,
        date_status=item.date_status.value,
        confidence_score=item.confidence_score,
        date_reason=proposal.value.date_reason,
    )


def plan_task_writes(
    run_id: UUID,
    proposals: Sequence[AggregatedProposal[PreparedProposal]],
    existing: Sequence[ExistingProposal],
    revisions_for_run: set[UUID],
) -> list[TaskWrite]:
    """Insert new evidence, update an untouched proposal, or revise a protected task.

    The L3.5 ledger decides insert, revise, or skip for the run. A revise of a task that
    is still `needs_review` or `pending` at version 1 becomes an in-place update, and an
    equal payload is a skip so the same run does not write a second row. Protected tasks
    (`confirmed`, `dismissed`, `completed`, or any version above 1) only gain a pending
    revision. Nothing here mutates the active columns of those tasks.
    """
    by_identity = {row.identity: row for row in existing}
    published = [
        PublishedIdentity(
            identity=row.identity,
            run_id=run_id if row.task_id in revisions_for_run else _OTHER_RUN,
        )
        for row in existing
    ]
    writes: list[TaskWrite] = []
    for proposal, kind in plan_run_publication(run_id, proposals, published):
        current = by_identity.get(proposal.identity)
        writes.append(_write_for(proposal, kind, current))
    return writes


def proposed_fields_payload(proposal: AggregatedProposal[PreparedProposal]) -> dict[str, object]:
    """JSON object stored on a revision. Dates are ISO strings; the block is absent."""
    fields = proposal_fields(proposal)
    return {
        "tipo": fields.tipo,
        "titulo": fields.titulo,
        "detalle": fields.detalle,
        "due_date": None if fields.due_date is None else fields.due_date.isoformat(),
        "due_at": None if fields.due_at is None else fields.due_at.isoformat(),
        "timezone": fields.timezone,
        "all_day": fields.all_day,
        "date_status": fields.date_status,
        "date_text": render_due_text(
            due_date=fields.due_date,
            due_at=fields.due_at,
            timezone=fields.timezone,
            all_day=fields.all_day,
            date_status=fields.date_status,
        ),
        "confidence_score": fields.confidence_score,
        "date_reason": fields.date_reason,
    }


async def load_existing_proposals(
    db: AsyncSession, *, user_id: UUID, audio_id: UUID
) -> list[ExistingProposal]:
    """Tasks of this class, identified by the segment slice their sources still point at."""
    rows = (
        await db.execute(
            select(Task, TaskSource, Segment.text)
            .join(
                TaskSource,
                and_(TaskSource.user_id == Task.user_id, TaskSource.task_id == Task.id),
            )
            .join(
                Segment,
                and_(
                    Segment.user_id == TaskSource.user_id,
                    Segment.transcript_id == TaskSource.transcript_id,
                    Segment.id == TaskSource.segment_id,
                ),
            )
            .where(Task.user_id == user_id, Task.audio_id == audio_id)
        )
    ).all()
    grouped: dict[UUID, tuple[Task, list[EvidenceSpan]]] = {}
    for task, source, text in rows:
        quote = text[source.char_start : source.char_end]
        if len(quote) != source.char_end - source.char_start:
            continue
        bucket = grouped.setdefault(task.id, (task, []))
        bucket[1].append(
            EvidenceSpan(
                segment_id=source.segment_id,
                char_start=source.char_start,
                char_end=source.char_end,
                quote=quote,
            )
        )
    existing: list[ExistingProposal] = []
    for stored, spans in grouped.values():
        if not spans:
            continue
        existing.append(
            ExistingProposal(
                task_id=stored.id,
                identity=frozenset(
                    (span.segment_id, span.char_start, span.char_end, span.quote) for span in spans
                ),
                status=stored.status,
                version=stored.version,
                fields=ProposalFields(
                    tipo=stored.tipo,
                    titulo=stored.titulo,
                    detalle=stored.detalle,
                    due_date=stored.due_date,
                    due_at=stored.due_at,
                    timezone=stored.timezone,
                    all_day=stored.all_day,
                    date_status=stored.date_status,
                    confidence_score=stored.confidence_score,
                    date_reason=stored.date_reason,
                ),
            )
        )
    return existing


async def revision_task_ids(db: AsyncSession, *, user_id: UUID, run_id: UUID) -> set[UUID]:
    """Tasks this run already revised. A replay must not add another revision."""
    rows = (
        await db.scalars(
            select(TaskRevision.task_id).where(
                TaskRevision.user_id == user_id,
                TaskRevision.run_id == run_id,
            )
        )
    ).all()
    return set(rows)


async def apply_task_writes(
    db: AsyncSession,
    *,
    user_id: UUID,
    audio_id: UUID,
    subject_id: UUID,
    transcript_id: UUID,
    transcript_version: int,
    run_id: UUID,
    writes: Sequence[TaskWrite],
) -> None:
    """Apply one complete batch. Skips write nothing; revisions stay pending."""
    for write in writes:
        if write.kind is WriteKind.SKIP:
            continue
        if write.kind is WriteKind.INSERT:
            await _insert_task(
                db,
                user_id=user_id,
                audio_id=audio_id,
                subject_id=subject_id,
                transcript_id=transcript_id,
                transcript_version=transcript_version,
                proposal=write.proposal,
            )
            continue
        if write.task_id is None:
            continue
        if write.kind is WriteKind.UPDATE and write.base_version is not None:
            updated = await _update_task(
                db,
                user_id=user_id,
                task_id=write.task_id,
                base_version=write.base_version,
                proposal=write.proposal,
                audio_id=audio_id,
                transcript_id=transcript_id,
                transcript_version=transcript_version,
            )
            if updated == "revise":
                await _revise_task(
                    db,
                    user_id=user_id,
                    task_id=write.task_id,
                    run_id=run_id,
                    base_version=write.base_version,
                    proposal=write.proposal,
                )
            continue
        if write.kind is WriteKind.REVISE and write.base_version is not None:
            await _revise_task(
                db,
                user_id=user_id,
                task_id=write.task_id,
                run_id=run_id,
                base_version=write.base_version,
                proposal=write.proposal,
            )


def _window_sizes(
    target_tokens: int | None, overlap_tokens: int | None
) -> dict[str, int]:
    sizes: dict[str, int] = {}
    if target_tokens is not None:
        sizes["target_tokens"] = target_tokens
    if overlap_tokens is not None:
        sizes["overlap_tokens"] = overlap_tokens
    return sizes


def _accepted(
    parsed: ParsedExtraction,
    window: AnalysisWindow,
    view: ClassTranscript,
) -> list[tuple[PreparedProposal, tuple[WindowCitation, ...]]]:
    accepted: list[tuple[PreparedProposal, tuple[WindowCitation, ...]]] = []
    parts = {part.segment_id: part for part in window.segments}
    for item, reason in zip(parsed.result.items, parsed.reasons, strict=True):
        single = AnalysisResult(
            items=[item],
            resumen_clase=parsed.result.resumen_clase,
            temas=parsed.result.temas,
        )
        try:
            validate_analysis(single, view)
        except AnalysisValidationError:
            continue
        citations: list[WindowCitation] = []
        outside = False
        for span in item.fuente.spans:
            part = parts.get(span.segment_id)
            if part is None:
                outside = True
                break
            citations.append(
                WindowCitation(
                    segment_id=span.segment_id,
                    char_start=part.segment_char_start + span.inicio_char,
                    char_end=part.segment_char_start + span.fin_char,
                )
            )
        if outside or not citations:
            continue
        accepted.append((PreparedProposal(item=item, date_reason=reason), tuple(citations)))
    return accepted


def _write_for(
    proposal: AggregatedProposal[PreparedProposal],
    kind: PublicationKind,
    current: ExistingProposal | None,
) -> TaskWrite:
    if kind is PublicationKind.INSERT:
        return TaskWrite(proposal, WriteKind.INSERT, None, None)
    if current is None or kind is PublicationKind.SKIP:
        task_id = None if current is None else current.task_id
        version = None if current is None else current.version
        return TaskWrite(proposal, WriteKind.SKIP, task_id, version)
    protected = current.status in _PROTECTED_STATUSES or current.version > 1
    if protected:
        return TaskWrite(proposal, WriteKind.REVISE, current.task_id, current.version)
    if current.fields.same_as(proposal_fields(proposal)):
        return TaskWrite(proposal, WriteKind.SKIP, current.task_id, current.version)
    return TaskWrite(proposal, WriteKind.UPDATE, current.task_id, current.version)


def _rewrite_item(
    raw_item: Mapping[str, object],
    *,
    class_date: date,
    class_timezone: str,
    now: datetime,
) -> tuple[dict[str, object], str | None] | None:
    mention_raw = raw_item.get("mencion")
    if mention_raw is None:
        return None
    try:
        resolved = resolve_due(
            mention_from_payload(mention_raw),
            class_date=class_date,
            class_timezone=class_timezone,
            now=now,
        )
    except DateResolutionError:
        return None
    rewritten = {key: value for key, value in raw_item.items() if key != "mencion"}
    _apply_resolution(rewritten, resolved)
    return rewritten, resolved.reason


def _apply_resolution(item: dict[str, object], resolved: ResolvedDue) -> None:
    item["due_date"] = None if resolved.due_date is None else resolved.due_date.isoformat()
    item["due_at"] = None if resolved.due_at is None else resolved.due_at.isoformat()
    item["all_day"] = resolved.all_day
    item["date_status"] = resolved.date_status.value
    item["timezone"] = resolved.timezone
    if resolved.date_status is not DateStatus.RESOLVED:
        item["all_day"] = False


def _payload_date(payload: Mapping[str, object], key: str) -> date | None:
    if key not in payload or payload[key] is None:
        return None
    value = payload[key]
    if not isinstance(value, str):
        raise DateResolutionError("mention_invalid", "the date mention does not match its kind")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise DateResolutionError(
            "impossible_date", "the calendar date does not exist"
        ) from exc


def _payload_time(payload: Mapping[str, object], key: str) -> time | None:
    if key not in payload or payload[key] is None:
        return None
    value = payload[key]
    if not isinstance(value, str):
        raise DateResolutionError("mention_invalid", "the date mention does not match its kind")
    pieces = value.split(":")
    if len(pieces) not in (2, 3):
        raise DateResolutionError("mention_invalid", "the date mention does not match its kind")
    try:
        hour = int(pieces[0])
        minute = int(pieces[1])
        second = int(pieces[2]) if len(pieces) == 3 else 0
        return time(hour, minute, second)
    except ValueError as exc:
        raise DateResolutionError(
            "mention_invalid", "the date mention does not match its kind"
        ) from exc


def _payload_int(payload: Mapping[str, object], key: str) -> int | None:
    if key not in payload or payload[key] is None:
        return None
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise DateResolutionError("mention_invalid", "the date mention does not match its kind")
    return value


def _payload_qualifier(payload: Mapping[str, object]) -> WeekdayQualifier | None:
    if "qualifier" not in payload or payload["qualifier"] is None:
        return None
    value = payload["qualifier"]
    if not isinstance(value, str):
        raise DateResolutionError("mention_invalid", "the date mention does not match its kind")
    try:
        return WeekdayQualifier(value)
    except ValueError as exc:
        raise DateResolutionError(
            "mention_invalid", "the date mention does not match its kind"
        ) from exc


def _same_instant(left: datetime | None, right: datetime | None) -> bool:
    if left is None or right is None:
        return left is right
    return left.astimezone(UTC) == right.astimezone(UTC)


async def _insert_task(
    db: AsyncSession,
    *,
    user_id: UUID,
    audio_id: UUID,
    subject_id: UUID,
    transcript_id: UUID,
    transcript_version: int,
    proposal: AggregatedProposal[PreparedProposal],
) -> None:
    fields = proposal_fields(proposal)
    task = Task(
        user_id=user_id,
        audio_id=audio_id,
        subject_id=subject_id,
        source="extraction",
        tipo=fields.tipo,
        titulo=fields.titulo,
        detalle=fields.detalle,
        due_date=fields.due_date,
        due_at=fields.due_at,
        timezone=fields.timezone,
        all_day=fields.all_day,
        date_status=fields.date_status,
        date_text=render_due_text(
            due_date=fields.due_date,
            due_at=fields.due_at,
            timezone=fields.timezone,
            all_day=fields.all_day,
            date_status=fields.date_status,
        ),
        date_reason=fields.date_reason,
        confidence_score=fields.confidence_score,
        status="needs_review",
        calendar_sync_status="not_requested",
    )
    db.add(task)
    await db.flush()
    await persist_task_spans(
        db,
        task=task,
        audio_id=audio_id,
        transcript_id=transcript_id,
        transcript_version=transcript_version,
        spans=proposal.evidence,
    )


def update_is_safe(status: str, version: int, base_version: int) -> bool:
    """An in-place update is only safe while the row is still the unedited proposal.

    The plan read a snapshot. A confirm or edit between that read and this write changes
    the status or the version, and the active columns must stay as the user left them.
    """
    if status in _PROTECTED_STATUSES or version != base_version or version > 1:
        return False
    return True


async def _update_task(
    db: AsyncSession,
    *,
    user_id: UUID,
    task_id: UUID,
    base_version: int,
    proposal: AggregatedProposal[PreparedProposal],
    audio_id: UUID,
    transcript_id: UUID,
    transcript_version: int,
) -> str:
    """Lock the row. Return ``updated``, ``revise``, or ``missing``.

    ``revise`` keeps the proposal as a pending revision and does not touch active columns.
    ``missing`` means the task disappeared; the caller must not invent a revision. An
    in-place update replaces the task's sources and evidence with the newly validated
    set, and bumps `due_version` only when the due representation actually changed.
    """
    task = (
        await db.execute(
            select(Task)
            .where(Task.user_id == user_id, Task.id == task_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if task is None:
        return "missing"
    if not update_is_safe(task.status, task.version, base_version):
        return "revise"
    fields = proposal_fields(proposal)
    due_changed = (
        task.due_date != fields.due_date
        or not _same_instant(task.due_at, fields.due_at)
        or task.timezone != fields.timezone
        or task.all_day != fields.all_day
        or task.date_status != fields.date_status
    )
    task.tipo = fields.tipo
    task.titulo = fields.titulo
    task.detalle = fields.detalle
    task.due_date = fields.due_date
    task.due_at = fields.due_at
    task.timezone = fields.timezone
    task.all_day = fields.all_day
    task.date_status = fields.date_status
    task.date_text = render_due_text(
        due_date=fields.due_date,
        due_at=fields.due_at,
        timezone=fields.timezone,
        all_day=fields.all_day,
        date_status=fields.date_status,
    )
    task.date_reason = fields.date_reason
    task.confidence_score = fields.confidence_score
    if due_changed:
        task.due_version += 1
    await persist_task_spans(
        db,
        task=task,
        audio_id=audio_id,
        transcript_id=transcript_id,
        transcript_version=transcript_version,
        spans=proposal.evidence,
        replace=True,
    )
    return "updated"


async def _revise_task(
    db: AsyncSession,
    *,
    user_id: UUID,
    task_id: UUID,
    run_id: UUID,
    base_version: int,
    proposal: AggregatedProposal[PreparedProposal],
) -> None:
    db.add(
        TaskRevision(
            user_id=user_id,
            task_id=task_id,
            run_id=run_id,
            base_task_version=base_version,
            revision_type="reanalysis",
            proposed_fields=proposed_fields_payload(proposal),
            evidence_validated=True,
            status="pending_confirmation",
        )
    )

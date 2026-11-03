"""Persisted task evidence and provenance (A3.2, ESPECIFICACION §M4).

A proposal is reconstructible from PostgreSQL alone:

- `task_sources` says *where* it came from (class, transcript version, segment and
  segment-local Unicode span) and is the normalized provenance;
- `task_evidence` stores the literal quote for one source span of one task version,
  anchored by the composite foreign key so a quote cannot exist without its source;
- `tasks.date_text`, `date_reason`, `due_version` and `timezone` are persisted by every
  producer and never re-derived from the model output.

The provenance rule is enforced here, before a row is written: a manual task has no
sources, an extraction task needs at least one, and a chat proposal may be a general
suggestion with none. Every span and quote is checked against the persisted segment text
on write and again on read; the database can enforce the shape, not the words.

Messages are fixed and never echo a quote or transcript text: both are untrusted data.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Audio, Segment, Task, TaskEvidence, TaskSource, Transcript
from app.services.analysis_windows import EvidenceSpan
from app.services.evidence import TextualEvidence, reliable_times

ORIGINS = frozenset({"extraction", "chat", "manual"})
MANUAL = "manual"
EXTRACTION = "extraction"


class ProvenanceInvalid(ValueError):
    """A span cannot be persisted or reconstructed from the persisted corpus.

    The future A3.3 router maps this to `422 evidence_invalid`. Messages are fixed and
    never include the quote or the transcript.
    """

    code = "evidence_invalid"


def validate_provenance(source: str, spans: Sequence[EvidenceSpan]) -> None:
    """Reject a provenance that the source kind does not allow.

    Manual tasks never cite the corpus. An extraction must cite at least one validated
    span. A chat proposal may have zero spans: that is a general suggestion, not a fake
    extraction.
    """
    if source not in ORIGINS:
        raise ProvenanceInvalid("the task source is not known")
    if source == MANUAL and spans:
        raise ProvenanceInvalid("a manual task cannot cite corpus evidence")
    if source == EXTRACTION and not spans:
        raise ProvenanceInvalid("an extraction task requires corpus evidence")


@dataclass(frozen=True)
class _SegmentView:
    text: str
    version: int


@dataclass(frozen=True)
class StoredTaskEvidence:
    """One reconstructed quote plus the provenance needed to read its class."""

    evidence: TextualEvidence
    task_version: int
    audio_id: uuid.UUID
    transcript_id: uuid.UUID
    transcript_version: int


async def persist_task_spans(
    db: AsyncSession,
    *,
    task: Task,
    audio_id: uuid.UUID,
    transcript_id: uuid.UUID,
    transcript_version: int,
    spans: Sequence[EvidenceSpan],
    replace: bool = False,
) -> tuple[TaskSource, ...]:
    """Validate and persist the spans (and their quotes) of one task.

    `replace=True` is the in-place reanalysis path: the task's previous evidence and
    sources are removed in the same transaction before the new validated set is written.
    Nothing is deleted until every new span has passed validation.

    :raises ProvenanceInvalid: unknown origin, forbidden/empty provenance, unknown
        segment, empty/inverted/out-of-range/duplicate span, or a quote that is not the
        exact persisted segment slice.
    """
    validate_provenance(task.source, spans)
    if transcript_version < 1:
        raise ProvenanceInvalid("the transcript version is not valid")

    views = await _load_segments(
        db,
        user_id=task.user_id,
        transcript_id=transcript_id,
        transcript_version=transcript_version,
        spans=spans,
    )
    validated = _validate_spans(spans, views)
    if replace:
        await _delete_provenance(db, user_id=task.user_id, task_id=task.id)

    sources: list[TaskSource] = []
    for span in validated:
        source = TaskSource(
            user_id=task.user_id,
            task_id=task.id,
            audio_id=audio_id,
            transcript_id=transcript_id,
            transcript_version=transcript_version,
            segment_id=span.segment_id,
            char_start=span.char_start,
            char_end=span.char_end,
        )
        db.add(source)
        sources.append(source)
    await db.flush()

    for span in validated:
        db.add(
            TaskEvidence(
                user_id=task.user_id,
                task_id=task.id,
                task_version=task.version,
                segment_id=span.segment_id,
                char_start=span.char_start,
                char_end=span.char_end,
                quote=span.quote,
            )
        )
    await db.flush()
    return tuple(sources)


async def load_task_evidence(
    db: AsyncSession, *, user_id: uuid.UUID, task_id: uuid.UUID, task_version: int
) -> tuple[StoredTaskEvidence, ...]:
    """Rebuild the active version's evidence from PostgreSQL.

    The join starts at the evidence row and follows the source, the exact transcript
    version, the segment and the live class, so a tombstoned class yields nothing. A
    missing task version is an empty tuple; a row that no longer matches its segment is
    corruption and raises instead of producing a wrong citation.

    :raises ProvenanceInvalid: a persisted row no longer matches the segment text or its
        own span checks.
    """
    rows = (
        await db.execute(
            select(TaskEvidence, TaskSource, Segment, Transcript)
            .join(
                TaskSource,
                and_(
                    TaskSource.user_id == TaskEvidence.user_id,
                    TaskSource.task_id == TaskEvidence.task_id,
                    TaskSource.segment_id == TaskEvidence.segment_id,
                    TaskSource.char_start == TaskEvidence.char_start,
                    TaskSource.char_end == TaskEvidence.char_end,
                ),
            )
            .join(
                Segment,
                and_(
                    Segment.user_id == TaskSource.user_id,
                    Segment.transcript_id == TaskSource.transcript_id,
                    Segment.id == TaskSource.segment_id,
                ),
            )
            .join(
                Transcript,
                and_(
                    Transcript.user_id == TaskSource.user_id,
                    Transcript.id == TaskSource.transcript_id,
                    Transcript.version == TaskSource.transcript_version,
                ),
            )
            .join(
                Audio,
                and_(
                    Audio.user_id == TaskSource.user_id,
                    Audio.id == TaskSource.audio_id,
                    Audio.deleted_at.is_(None),
                ),
            )
            .where(
                TaskEvidence.user_id == user_id,
                TaskEvidence.task_id == task_id,
                TaskEvidence.task_version == task_version,
            )
            .order_by(Segment.ordinal, TaskEvidence.char_start)
        )
    ).all()

    evidence: list[StoredTaskEvidence] = []
    for stored, source, segment, transcript in rows:
        if stored.task_version != task_version:
            raise ProvenanceInvalid("the stored evidence version is inconsistent")
        if stored.quote != segment.text[stored.char_start : stored.char_end]:
            raise ProvenanceInvalid("the stored quote does not match the segment")
        if len(stored.quote) != stored.char_end - stored.char_start:
            raise ProvenanceInvalid("the stored quote length does not match the span")
        if stored.char_start < 0 or stored.char_end > len(segment.text):
            raise ProvenanceInvalid("the stored span is outside the segment")
        t_start, t_end = reliable_times(
            timestamp_precision=transcript.timestamp_precision,
            t_start=segment.t_start,
            t_end=segment.t_end,
        )
        evidence.append(
            StoredTaskEvidence(
                evidence=TextualEvidence(
                    segment_id=segment.id,
                    ordinal=segment.ordinal,
                    char_start=segment.char_start + stored.char_start,
                    char_end=segment.char_start + stored.char_end,
                    inicio_char=stored.char_start,
                    fin_char=stored.char_end,
                    quote=stored.quote,
                    t_start=t_start,
                    t_end=t_end,
                ),
                task_version=stored.task_version,
                audio_id=source.audio_id,
                transcript_id=source.transcript_id,
                transcript_version=source.transcript_version,
            )
        )
    return tuple(evidence)


async def _load_segments(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    transcript_id: uuid.UUID,
    transcript_version: int,
    spans: Sequence[EvidenceSpan],
) -> dict[uuid.UUID, _SegmentView]:
    if not spans:
        return {}
    rows = (
        await db.execute(
            select(Segment.id, Segment.text, Transcript.version)
            .join(
                Transcript,
                and_(
                    Transcript.user_id == Segment.user_id,
                    Transcript.id == Segment.transcript_id,
                ),
            )
            .where(
                Segment.user_id == user_id,
                Segment.transcript_id == transcript_id,
                Segment.id.in_({span.segment_id for span in spans}),
            )
        )
    ).all()
    views = {
        segment_id: _SegmentView(text=text, version=version)
        for segment_id, text, version in rows
    }
    if any(view.version != transcript_version for view in views.values()):
        raise ProvenanceInvalid("the segment does not belong to the transcript version")
    return views


def _validate_spans(
    spans: Sequence[EvidenceSpan], views: dict[uuid.UUID, _SegmentView]
) -> list[EvidenceSpan]:
    seen: set[tuple[uuid.UUID, int, int]] = set()
    for span in spans:
        view = views.get(span.segment_id)
        if view is None:
            raise ProvenanceInvalid("the span references a segment outside the transcript")
        if span.char_start < 0 or span.char_end <= span.char_start:
            raise ProvenanceInvalid("the span is empty or inverted")
        if span.char_end > len(view.text):
            raise ProvenanceInvalid("the span exceeds the persisted segment text")
        if len(span.quote) != span.char_end - span.char_start:
            raise ProvenanceInvalid("the quote length does not match the span")
        if not span.quote.strip():
            raise ProvenanceInvalid("the quote is blank")
        if view.text[span.char_start : span.char_end] != span.quote:
            raise ProvenanceInvalid("the quote does not match the persisted segment text")
        key = (span.segment_id, span.char_start, span.char_end)
        if key in seen:
            raise ProvenanceInvalid("the span is duplicated")
        seen.add(key)
    return list(spans)


async def _delete_provenance(
    db: AsyncSession, *, user_id: uuid.UUID, task_id: uuid.UUID
) -> None:
    await db.execute(
        delete(TaskEvidence)
        .where(TaskEvidence.user_id == user_id, TaskEvidence.task_id == task_id)
        .execution_options(synchronize_session=False)
    )
    await db.execute(
        delete(TaskSource)
        .where(TaskSource.user_id == user_id, TaskSource.task_id == task_id)
        .execution_options(synchronize_session=False)
    )
    await db.flush()

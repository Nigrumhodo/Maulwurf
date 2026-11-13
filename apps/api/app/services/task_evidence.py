"""Persisted task evidence and provenance (A3.2/A3.6, ESPECIFICACION §M4).

A proposal is reconstructible from PostgreSQL alone:

- `task_sources` says *where* it came from (class, transcript version, segment and
  segment-local Unicode span) and is the normalized provenance;
- `task_evidence` stores the literal quote for one source span of one task version,
  anchored by the composite foreign key so a quote cannot exist without its source;
- `tasks.date_text`, `date_reason`, `due_version` and `timezone` are persisted by every
  producer and never re-derived from the model output.

The provenance rule is enforced here, before a row is written: a manual task has no
sources, an extraction task needs at least one, and a chat proposal may be a general
suggestion with none. On write, `evidence_reconstruction` loads the L3.3 class view from
PostgreSQL and rebuilds every quote from the segment text; the persisted quote is that
slice, never a caller copy. The same check runs on read. The database can enforce the
shape, not the words.

Messages are fixed and never echo a quote or transcript text: both are untrusted data.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import and_, delete, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Audio, Segment, Task, TaskEvidence, TaskSource, Transcript
from app.services.analysis_validation import AnalysisValidationError
from app.services.analysis_windows import EvidenceSpan
from app.services.evidence import TextualEvidence, reliable_times
from app.services.evidence_reconstruction import reconstruct_spans

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

    The text is reconstructed from the L3.3 class view in PostgreSQL; a span of another
    class, tenant, transcript or version, an out-of-range span or a quote that is not the
    exact slice is rejected before anything is written.

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

    try:
        rebuilt = await reconstruct_spans(
            db,
            user_id=task.user_id,
            audio_id=audio_id,
            transcript_version=transcript_version,
            transcript_id=transcript_id,
            spans=spans,
        )
    except AnalysisValidationError as exc:
        raise ProvenanceInvalid(str(exc)) from exc

    validated: list[EvidenceSpan] = []
    seen: set[tuple[uuid.UUID, int, int]] = set()
    for item in rebuilt:
        if not item.quote.strip():
            raise ProvenanceInvalid("the quote is blank")
        key = (item.segment_id, item.inicio_char, item.fin_char)
        if key in seen:
            raise ProvenanceInvalid("the span is duplicated")
        seen.add(key)
        validated.append(
            EvidenceSpan(
                segment_id=item.segment_id,
                char_start=item.inicio_char,
                char_end=item.fin_char,
                quote=item.quote,
            )
        )

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


def _reconstruct_evidence(
    stored: TaskEvidence,
    source: TaskSource,
    segment: Segment,
    transcript: Transcript,
    *,
    task_version: int,
) -> StoredTaskEvidence:
    """Validate one persisted row and rebuild its textual evidence."""
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
    return StoredTaskEvidence(
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


async def load_task_evidence_batch(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    task_versions: Mapping[uuid.UUID, int],
) -> dict[uuid.UUID, tuple[StoredTaskEvidence, ...]]:
    """Rebuild the active evidence of several tasks in one query.

    The row-value filter keeps every task bound to its own active version, so a page
    cannot mix the evidence of an older version of one task with the current version of
    another. The join starts at the evidence row and follows the source, the exact
    transcript version, the segment and the live class, so a tombstoned class yields
    nothing.

    :raises ProvenanceInvalid: a persisted row no longer matches the segment text or its
        own span checks.
    """
    if not task_versions:
        return {}
    pairs = list(task_versions.items())
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
                tuple_(TaskEvidence.task_id, TaskEvidence.task_version).in_(pairs),
            )
            .order_by(TaskEvidence.task_id, Segment.ordinal, TaskEvidence.char_start)
        )
    ).all()

    grouped: dict[uuid.UUID, list[StoredTaskEvidence]] = {}
    for stored, source, segment, transcript in rows:
        expected_version = task_versions.get(stored.task_id)
        if expected_version is None:  # pragma: no cover - the row-value filter excludes it
            continue
        grouped.setdefault(stored.task_id, []).append(
            _reconstruct_evidence(
                stored, source, segment, transcript, task_version=expected_version
            )
        )
    return {task_id: tuple(items) for task_id, items in grouped.items()}


async def load_task_evidence(
    db: AsyncSession, *, user_id: uuid.UUID, task_id: uuid.UUID, task_version: int
) -> tuple[StoredTaskEvidence, ...]:
    """Rebuild the active version's evidence from PostgreSQL.

    A missing task version is an empty tuple; a row that no longer matches its segment is
    corruption and raises instead of producing a wrong citation.

    :raises ProvenanceInvalid: a persisted row no longer matches the segment text or its
        own span checks.
    """
    batch = await load_task_evidence_batch(
        db, user_id=user_id, task_versions={task_id: task_version}
    )
    return batch.get(task_id, ())


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

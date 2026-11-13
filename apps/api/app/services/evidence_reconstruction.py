"""Database-backed reconstruction of L3.3 evidence (A3.6).

`analysis_validation.validate_analysis` checks a model result against an already-loaded
`ClassTranscript`. This module loads that view from PostgreSQL and rebuilds each quote from
the immutable segment text with Python code-point offsets. The reconstructed slice is the
source of truth; a caller-supplied quote is only an assertion to compare.

`task_evidence.persist_task_spans` uses the same reconstruction, so a proposal cannot
persist a segment, span or quote that the database cannot reproduce. Errors keep the
closed L3.3 codes and never echo the transcript or the quote.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.analysis import AnalysisResult
from app.services.analysis_validation import (
    ClassTranscript,
    load_class_transcript,
    reject,
    validate_analysis,
)
from app.services.analysis_windows import EvidenceSpan


@dataclass(frozen=True)
class ReconstructedEvidence:
    """One span whose quote was rebuilt from the persisted segment text."""

    segment_id: uuid.UUID
    transcript_id: uuid.UUID
    transcript_version: int
    inicio_char: int
    fin_char: int
    quote: str


def reconstruct_from_view(
    view: ClassTranscript, spans: Sequence[EvidenceSpan]
) -> tuple[ReconstructedEvidence, ...]:
    """Rebuild each quote from an already-loaded class view.

    This is the pure half of the reconstruction. Membership, range and literalness use the
    same closed codes as L3.3; `persist_task_spans` still owns the origin rule, duplicate
    detection and the blank-quote rule.
    """
    rebuilt: list[ReconstructedEvidence] = []
    for span in spans:
        text = view.segments.get(span.segment_id)
        if text is None:
            reject("segment_not_in_class")
        if span.char_start < 0 or span.char_end <= span.char_start:
            reject("span_out_of_range")
        if span.char_end > len(text):
            reject("span_out_of_range")
        quote = text[span.char_start : span.char_end]
        if quote != span.quote:
            reject("quote_mismatch")
        rebuilt.append(
            ReconstructedEvidence(
                segment_id=span.segment_id,
                transcript_id=view.transcript_id,
                transcript_version=view.transcript_version,
                inicio_char=span.char_start,
                fin_char=span.char_end,
                quote=quote,
            )
        )
    return tuple(rebuilt)


async def reconstruct_spans(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    audio_id: uuid.UUID,
    transcript_version: int,
    spans: Sequence[EvidenceSpan],
    transcript_id: uuid.UUID | None = None,
) -> tuple[ReconstructedEvidence, ...]:
    """Load the class view and rebuild every span from the persisted segment text.

    An empty span list is the chat general-suggestion case and needs no class read. When
    `transcript_id` is supplied, it must be the transcript of this tenant/class/version;
    this closes the "real segment of another transcript" hole before the database FKs do.

    :raises AnalysisValidationError: with a closed L3.3 code and a fixed message.
    """
    if not spans:
        return ()
    view = await load_class_transcript(
        db,
        user_id=user_id,
        audio_id=audio_id,
        transcript_version=transcript_version,
    )
    if transcript_id is not None and view.transcript_id != transcript_id:
        reject("transcript_not_in_class")
    return reconstruct_from_view(view, spans)


async def validate_analysis_from_db(
    db: AsyncSession,
    result: AnalysisResult,
    *,
    user_id: uuid.UUID,
    audio_id: uuid.UUID,
    transcript_version: int,
) -> ClassTranscript:
    """Validate an L3.3 result against the class text loaded from PostgreSQL.

    Returns the pinned view so the caller can reuse its transcript id/version. The pure
    `validate_analysis` remains the comparison; this function only supplies the DB view.
    """
    view = await load_class_transcript(
        db,
        user_id=user_id,
        audio_id=audio_id,
        transcript_version=transcript_version,
    )
    validate_analysis(result, view)
    return view

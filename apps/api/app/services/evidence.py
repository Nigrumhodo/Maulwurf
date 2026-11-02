"""Textual evidence for analysis, with or without ASR offsets (S3.3, M4/D6).

The extraction pipeline identifies an item by *text*: the persisted segment, the Unicode
span inside it and the verbatim quote. Times are an optional ornament, never the identity.
When D6 resolves to `none`, or when a transcript has no usable offsets, the analysis still
works with that textual evidence:

- `t_start`/`t_end` stay `None` when `timestamp_precision` is not `word`/`segment`, when the
  pair is incomplete, non-finite, negative, empty or inverted, or when it exceeds the class
  duration. The values are copied from the persisted segment and are never interpolated,
  averaged with a neighbour or rounded up.
- The user-visible reference falls back to `"segment N"` and never to `00:00`. A missing
  offset must not look like the first minute of the class.
- Quotes are reconstructed from the persisted segment text with Python code-point offsets
  (the L1.4 contract), not from model output.

This module is pure: it does not read the database or render the inbox. A future analysis
runner loads the active transcript version's segments and passes them here after validating
the LLM spans (L3.3/L3.6); the task evidence tables (A3.2/A3.6) can persist `as_source()`.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from app.services.exports import EPS, EXPORTABLE_PRECISIONS


class EvidenceInvalid(ValueError):
    """A span cannot be reconstructed from the persisted segment text.

    Messages are fixed and never include transcript content: the LLM response and the
    transcript are untrusted data, and validation errors must not echo them.
    """


@dataclass(frozen=True)
class SegmentText:
    """Minimal view of a persisted `segments` row needed to rebuild evidence."""

    segment_id: uuid.UUID
    ordinal: int
    text: str
    char_start: int
    char_end: int
    t_start: float | None = None
    t_end: float | None = None


@dataclass(frozen=True)
class EvidenceSpan:
    """`[inicio_char, fin_char)` inside the immutable text of one segment."""

    segment_id: uuid.UUID
    inicio_char: int
    fin_char: int


def has_reliable_offsets(t_start: float | None, t_end: float | None) -> bool:
    """True only when both offsets are real, finite and ordered.

    This answers a narrower question than `reliable_times`: it does not know the transcript
    precision or duration, so callers that already filtered those can use it for labels.
    """
    if t_start is None or t_end is None:
        return False
    return math.isfinite(t_start) and math.isfinite(t_end) and 0 <= t_start < t_end


def reliable_times(
    *,
    timestamp_precision: str,
    t_start: float | None,
    t_end: float | None,
    duration_seconds: float | None = None,
) -> tuple[float | None, float | None]:
    """Both persisted offsets or `(None, None)`; never a guessed or one-sided range.

    `timestamp_precision=none` is the G2-X/D6 text-only case: even if a row somehow carries
    times, they are withheld because the ASR did not measure them.
    """
    if timestamp_precision not in EXPORTABLE_PRECISIONS:
        return None, None
    if t_start is None or t_end is None:
        return None, None
    if not has_reliable_offsets(t_start, t_end):
        return None, None
    # A segment that ends past the class duration is inconsistent; a truthful anchor cannot
    # be published from it. The small tolerance mirrors the SRT export rule.
    if (
        duration_seconds is not None
        and math.isfinite(duration_seconds)
        and t_end > duration_seconds + EPS
    ):
        return None, None
    return t_start, t_end


def format_timestamp(seconds: float) -> str:
    """Deterministic `mm:ss` / `h:mm:ss`; timestamps are evidence, never interpolated."""
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def evidence_reference(
    *,
    segment_ordinal: int | None,
    t_start: float | None,
    t_end: float | None,
) -> str:
    """Visible anchor: a measured time when truthful, otherwise "segment N".

    Returns an empty string only when neither a time nor an ordinal is available, so the
    caller can omit the third label part instead of printing an empty separator.
    """
    if t_start is not None and t_end is not None and has_reliable_offsets(t_start, t_end):
        return format_timestamp(t_start)
    if segment_ordinal is not None:
        return f"segment {segment_ordinal}"
    return ""


def evidence_label(
    *,
    subject_name: str,
    class_date: date,
    segment_ordinal: int | None,
    t_start: float | None,
    t_end: float | None,
) -> str:
    """Human label for a citation or task evidence entry, built only from server columns."""
    parts = [subject_name, class_date.isoformat()]
    reference = evidence_reference(
        segment_ordinal=segment_ordinal, t_start=t_start, t_end=t_end
    )
    if reference:
        parts.append(reference)
    return f"[{' · '.join(parts)}]"


@dataclass(frozen=True)
class TextualEvidence:
    """One validated evidence entry, ready for the analysis result or the review inbox."""

    segment_id: uuid.UUID
    ordinal: int
    # Absolute transcript offsets (segment.char_start + local span) and the local span.
    char_start: int
    char_end: int
    inicio_char: int
    fin_char: int
    quote: str
    t_start: float | None
    t_end: float | None

    @property
    def reference(self) -> str:
        """`"segment N"` when the pair is absent or untruthful, never a fabricated time."""
        return evidence_reference(
            segment_ordinal=self.ordinal, t_start=self.t_start, t_end=self.t_end
        )

    def as_source(self) -> dict[str, object]:
        """API/JSON shape: IDs and offsets plus the optional measured times.

        `label` is server-derived so the future inbox does not have to reconstruct the
        "segment N" fallback (and cannot accidentally render minute zero).
        """
        return {
            "segment_id": str(self.segment_id),
            "ordinal": self.ordinal,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "inicio_char": self.inicio_char,
            "fin_char": self.fin_char,
            "quote": self.quote,
            "t_start": self.t_start,
            "t_end": self.t_end,
            "label": self.reference,
        }


def resolve_textual_evidence(
    *,
    spans: Sequence[EvidenceSpan],
    segments: Sequence[SegmentText],
    timestamp_precision: str,
    duration_seconds: float | None = None,
) -> tuple[TextualEvidence, ...]:
    """Rebuild evidence from persisted segments without trusting any time from the LLM.

    The span must belong to one of the supplied segments and lie inside its text. The quote
    is sliced from that text, so it is verbatim by construction. Times come only from the
    segment row and only through `reliable_times`.
    """
    by_id: dict[uuid.UUID, SegmentText] = {}
    for row in segments:
        if row.segment_id in by_id:
            raise EvidenceInvalid("duplicate segment in the transcript view")
        by_id[row.segment_id] = row

    resolved: list[TextualEvidence] = []
    seen: set[tuple[uuid.UUID, int, int]] = set()
    for span in spans:
        segment = by_id.get(span.segment_id)
        if segment is None:
            raise EvidenceInvalid("span references a segment outside the transcript")
        if span.inicio_char < 0 or span.fin_char <= span.inicio_char:
            raise EvidenceInvalid("span is empty or inverted")
        if span.fin_char > len(segment.text):
            raise EvidenceInvalid("span exceeds the persisted segment text")
        key = (span.segment_id, span.inicio_char, span.fin_char)
        if key in seen:
            raise EvidenceInvalid("duplicate span")
        seen.add(key)
        t_start, t_end = reliable_times(
            timestamp_precision=timestamp_precision,
            t_start=segment.t_start,
            t_end=segment.t_end,
            duration_seconds=duration_seconds,
        )
        resolved.append(
            TextualEvidence(
                segment_id=segment.segment_id,
                ordinal=segment.ordinal,
                char_start=segment.char_start + span.inicio_char,
                char_end=segment.char_start + span.fin_char,
                inicio_char=span.inicio_char,
                fin_char=span.fin_char,
                quote=segment.text[span.inicio_char : span.fin_char],
                t_start=t_start,
                t_end=t_end,
            )
        )
    return tuple(resolved)

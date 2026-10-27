"""TXT/SRT export rules (A2.9, S2.md §A2.9 on ESPECIFICACION.md M2/M9).

Pure decision core: no I/O, no DB, no router imports. An SRT is exportable ONLY when the
timestamps the ASR actually measured are usable — a real precision and every segment with
ordered, non-negative times contained in the class duration. Anything else fails closed so
the API answers `409 srt_unavailable` instead of inventing or shifting minutes
(ESPECIFICACION.md M3: "never invent or distribute proportionally; SRT only with valid
times").
"""

from collections.abc import Sequence
from typing import Protocol

# Timestamp precisions that carry real per-segment times (ESPECIFICACION.md M2/M3).
EXPORTABLE_PRECISIONS = ("word", "segment")
# Tolerance when comparing a segment end against the class duration: the last segment may end
# a fraction of a second after `duration_seconds` because ffprobe and ASR round differently.
# A larger gap means the times are inconsistent and the export is refused.
EPS = 1e-3


class TimedSegment(Protocol):
    """Minimal view of a `segments` row consumed by the export."""

    text: str
    t_start: float | None
    t_end: float | None


def srt_exportable(
    *,
    timestamp_precision: str,
    segments: Sequence[TimedSegment],
    duration_seconds: float | None,
) -> bool:
    """True only when a truthful SRT can be built from the measured times.

    Fails closed: unknown precision, no segments, a missing duration, a segment without
    times, non-increasing times, out-of-order/overlapping segments or a segment past the
    class duration all mean "no SRT" — never a guessed one.
    """
    if timestamp_precision not in EXPORTABLE_PRECISIONS:
        return False
    if not segments or duration_seconds is None:
        return False
    previous_end = 0.0
    for segment in segments:
        start, end = segment.t_start, segment.t_end
        if start is None or end is None:
            return False
        if start < 0 or start >= end:
            return False
        if start < previous_end:
            # Out of order or overlapping: an SRT cannot be built truthfully.
            return False
        if end > duration_seconds + EPS:
            return False
        previous_end = end
    return True


def _times(segment: TimedSegment) -> tuple[float, float]:
    if segment.t_start is None or segment.t_end is None:
        # Unreachable for callers that ran `srt_exportable` first; the explicit error keeps
        # the type contract honest without an assert in production code.
        raise ValueError("segment without timestamps")
    return segment.t_start, segment.t_end


def _srt_timestamp(seconds: float) -> str:
    total_ms = max(0, round(seconds * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def render_srt(segments: Sequence[TimedSegment]) -> str:
    """Serialise segments to SubRip (1-based numbering, `HH:MM:SS,mmm`)."""
    blocks = []
    for index, segment in enumerate(segments, start=1):
        start, end = _times(segment)
        text = segment.text.strip()
        blocks.append(
            f"{index}\n{_srt_timestamp(start)} --> {_srt_timestamp(end)}\n{text}\n"
        )
    return "\n".join(blocks)


def render_txt(*, text: str) -> str:
    """Verbatim transcript text: the export never rewrites or normalises the ASR output."""
    return text

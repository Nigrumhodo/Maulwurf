"""Bounded analysis windows and evidence dedupe (S3.2, ESPECIFICACION.md M4).

The extraction worker cannot send a whole class to the model: a long class exceeds the
context and the token bill grows with every retry. This module is the pure decision core
that plans sentence-aligned windows over the active persisted transcript and collapses the
items that two overlapping windows extracted from the same evidence.

Three invariants are enforced before any provider call:

- **Coverage.** Windows advance over the transcript, cut at sentence or ASR-segment
  boundaries (never mid-sentence unless a single sentence is larger than the target) and
  keep Unicode code-point offsets, the same convention as the §M6 chunker.
- **Bounded cost.** Every window is measured against ``target_tokens``. The overlap is
  capped by ``overlap_tokens`` and by an optional ``max_total_tokens`` budget that is a
  hard, measured bound: the planner trims whole overlap units (down to zero) instead of
  exceeding it. A budget below the no-overlap floor is rejected.
- **Evidence identity.** A span is the segment plus its ``[char_start, char_end)`` range and
  the literal quote. Dedupe uses exactly that identity, so a model repeating a task in the
  overlap yields one proposal, while two different spans or quotes stay distinct.

No DB, HTTP, prompt or provider access happens here. Token counting is injected; the
default is the offline BPE counter from the chunker with its conservative fallback. The
G8 measurement lives in ``tools/eval/window_cost.py`` and never calls a provider.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from uuid import UUID

from app.services.chunking import (
    SegmentInput,
    TextUnit,
    TokenCounter,
    count_tokens_default,
    transcript_units,
)

# The analysis prompt (L3.1) and G8 have not pinned an analysis-specific size yet. Mirror
# the validated §M6 defaults until the window-cost measurement selects a production profile.
DEFAULT_TARGET_TOKENS = 800
DEFAULT_OVERLAP_TOKENS = 100

EvidenceKey = tuple[UUID, int, int, str]


@dataclass(frozen=True)
class WindowSegment:
    """A segment clipped to one window, keeping both global and segment-local offsets."""

    segment_id: UUID
    ordinal: int
    segment_char_start: int
    segment_char_end: int
    window_char_start: int
    window_char_end: int
    text: str

    @property
    def local_char_start(self) -> int:
        return self.window_char_start - self.segment_char_start

    @property
    def local_char_end(self) -> int:
        return self.window_char_end - self.segment_char_start


@dataclass(frozen=True)
class AnalysisWindow:
    """One measured prompt window; ``text`` is exactly ``transcript[char_start:char_end]``."""

    ordinal: int
    char_start: int
    char_end: int
    text: str
    token_count: int
    overlap_char_end: int
    overlap_tokens: int
    segments: tuple[WindowSegment, ...]

    @property
    def overlap_char_start(self) -> int:
        return self.char_start

    @property
    def overlap_chars(self) -> int:
        return self.overlap_char_end - self.char_start


@dataclass(frozen=True)
class WindowPlan:
    """Measured plan for one transcript version.

    ``total_tokens`` is the sum of the measured window texts, not an estimate from unit
    sums. ``effective_overlap_tokens`` is the per-window cap actually used after applying
    ``max_total_tokens``; ``budget_limited`` says whether it was reduced.
    """

    windows: tuple[AnalysisWindow, ...]
    transcript_tokens: int
    requested_overlap_tokens: int
    effective_overlap_tokens: int
    max_total_tokens: int | None

    @property
    def window_count(self) -> int:
        return len(self.windows)

    @property
    def total_tokens(self) -> int:
        return sum(window.token_count for window in self.windows)

    @property
    def overlap_tokens_total(self) -> int:
        return sum(window.overlap_tokens for window in self.windows)

    @property
    def amplification(self) -> float:
        """Window tokens divided by transcript tokens; ``0.0`` for an empty plan."""
        if not self.windows or self.transcript_tokens <= 0:
            return 0.0
        return self.total_tokens / self.transcript_tokens

    @property
    def budget_limited(self) -> bool:
        return self.effective_overlap_tokens < self.requested_overlap_tokens

    @property
    def coverage_char_start(self) -> int | None:
        return self.windows[0].char_start if self.windows else None

    @property
    def coverage_char_end(self) -> int | None:
        return self.windows[-1].char_end if self.windows else None


@dataclass(frozen=True)
class EvidenceSpan:
    """One citation anchor: segment identity plus a segment-local Unicode span and quote."""

    segment_id: UUID
    char_start: int
    char_end: int
    quote: str

    def __post_init__(self) -> None:
        if self.char_start < 0 or self.char_end <= self.char_start:
            raise ValueError("evidence span must satisfy 0 <= char_start < char_end")
        if len(self.quote) != self.char_end - self.char_start:
            raise ValueError("quote length must match the evidence range")


def evidence_key(span: EvidenceSpan) -> EvidenceKey:
    """Exact identity of a piece of evidence (segment + range + literal quote)."""
    return (span.segment_id, span.char_start, span.char_end, span.quote)


def evidence_span_in_window(
    window: AnalysisWindow,
    *,
    segment_id: UUID,
    char_start: int,
    char_end: int,
) -> EvidenceSpan:
    """Translate a global window range into the segment-local §M4 evidence span.

    Raises ``ValueError`` when the range is outside the window, belongs to a segment that is
    not part of it, or crosses the clipped boundary of that segment.
    """
    if not (window.char_start <= char_start < char_end <= window.char_end):
        raise ValueError("evidence range must be inside the window")
    part = next(
        (candidate for candidate in window.segments if candidate.segment_id == segment_id),
        None,
    )
    if part is None:
        raise ValueError("evidence segment is not part of the window")
    if not (part.window_char_start <= char_start and char_end <= part.window_char_end):
        raise ValueError("evidence range must be inside one segment of the window")
    quote = window.text[char_start - window.char_start : char_end - window.char_start]
    return EvidenceSpan(
        segment_id=segment_id,
        char_start=char_start - part.segment_char_start,
        char_end=char_end - part.segment_char_start,
        quote=quote,
    )


def dedupe_by_evidence[T](
    items: Iterable[T],
    *,
    evidence: Callable[[T], Iterable[EvidenceSpan]],
) -> list[T]:
    """Keep the first item per exact evidence identity, preserving input order.

    An item with no evidence is a general suggestion, not a corpus extraction: it has no
    identity to compare, so it is never collapsed with another suggestion. Multi-source
    items are identified by the unordered set of their spans, because the model may cite
    the same sources in a different order in two overlapping windows.
    """
    kept: list[T] = []
    seen: set[frozenset[EvidenceKey]] = set()
    for item in items:
        spans = tuple(evidence(item))
        if not spans:
            kept.append(item)
            continue
        key = frozenset(evidence_key(span) for span in spans)
        if key in seen:
            continue
        seen.add(key)
        kept.append(item)
    return kept


def plan_windows(
    *,
    transcript_text: str,
    segments: Sequence[SegmentInput],
    target_tokens: int = DEFAULT_TARGET_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
    max_total_tokens: int | None = None,
    count_tokens: TokenCounter = count_tokens_default,
) -> WindowPlan:
    """Plan and measure sentence-aligned windows for one persisted transcript.

    ``max_total_tokens`` is a hard bound on ``WindowPlan.total_tokens``. When the natural
    plan exceeds it, the effective overlap cap is reduced (bisection, down to zero) and the
    windows are replanned; a budget below the zero-overlap floor raises ``ValueError``
    because no windowing can make the transcript fit.
    """
    if target_tokens < 1:
        raise ValueError("target_tokens must be >= 1")
    if overlap_tokens < 0:
        raise ValueError("overlap_tokens must be >= 0")
    if overlap_tokens >= target_tokens:
        raise ValueError("overlap_tokens must be lower than target_tokens")
    if max_total_tokens is not None and max_total_tokens < 0:
        raise ValueError("max_total_tokens must be >= 0")

    transcript_tokens = count_tokens(transcript_text)
    empty = WindowPlan(
        windows=(),
        transcript_tokens=transcript_tokens,
        requested_overlap_tokens=overlap_tokens,
        effective_overlap_tokens=0,
        max_total_tokens=max_total_tokens,
    )
    units = transcript_units(
        transcript_text=transcript_text,
        segments=segments,
        target_tokens=target_tokens,
        count_tokens=count_tokens,
    )
    if not units:
        return empty

    def total_for(cap: int) -> int:
        return _measure_ranges(
            units,
            transcript_text,
            _ranges(units, transcript_text, target_tokens, cap, count_tokens),
            count_tokens,
        )

    effective = overlap_tokens
    if max_total_tokens is not None:
        floor = total_for(0)
        if floor > max_total_tokens:
            raise ValueError(
                f"max_total_tokens={max_total_tokens} cannot cover the transcript: "
                f"at least {floor} tokens are required"
            )
        if total_for(overlap_tokens) > max_total_tokens:
            low, high = 0, overlap_tokens
            while low + 1 < high:
                middle = (low + high) // 2
                if total_for(middle) <= max_total_tokens:
                    low = middle
                else:
                    high = middle
            effective = low

    ranges = _ranges(units, transcript_text, target_tokens, effective, count_tokens)
    plan = WindowPlan(
        windows=_build_windows(
            units=units,
            transcript_text=transcript_text,
            segments=segments,
            ranges=ranges,
            count_tokens=count_tokens,
        ),
        transcript_tokens=transcript_tokens,
        requested_overlap_tokens=overlap_tokens,
        effective_overlap_tokens=effective,
        max_total_tokens=max_total_tokens,
    )
    if max_total_tokens is not None and plan.total_tokens > max_total_tokens:
        # Defensive: the bisection uses the same planner, so this cannot happen unless a
        # caller's counter is non-deterministic.
        raise ValueError(
            f"planned {plan.total_tokens} tokens over the {max_total_tokens} budget"
        )
    return plan


def _ranges(
    units: Sequence[TextUnit],
    transcript_text: str,
    target_tokens: int,
    overlap_cap: int,
    count_tokens: TokenCounter,
) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    first = 0
    while first < len(units):
        last = _window_end(
            units,
            transcript_text,
            first,
            target_tokens=target_tokens,
            count_tokens=count_tokens,
        )
        ranges.append((first, last))
        if last >= len(units):
            break
        candidate = _next_start(
            units,
            transcript_text,
            first,
            last,
            target_tokens=target_tokens,
            overlap_cap=overlap_cap,
            count_tokens=count_tokens,
        )
        if candidate < last:
            candidate_end = _window_end(
                units,
                transcript_text,
                candidate,
                target_tokens=target_tokens,
                count_tokens=count_tokens,
            )
            if candidate_end <= last:
                candidate = last
        first = candidate
    return ranges


def _window_end(
    units: Sequence[TextUnit],
    transcript_text: str,
    first: int,
    *,
    target_tokens: int,
    count_tokens: TokenCounter,
) -> int:
    """Exclusive end of the greedy window, re-measured so tokenizer boundaries cannot
    push the real prompt text over the target."""
    last = first
    tokens = 0
    while last < len(units) and (last == first or tokens + units[last].tokens <= target_tokens):
        tokens += units[last].tokens
        last += 1
    while last - first > 1 and (
        count_tokens(transcript_text[units[first].start : units[last - 1].end]) > target_tokens
    ):
        last -= 1
    return last


def _next_start(
    units: Sequence[TextUnit],
    transcript_text: str,
    first: int,
    last: int,
    *,
    target_tokens: int,
    overlap_cap: int,
    count_tokens: TokenCounter,
) -> int:
    """First unit of the next window: sentence-aligned overlap that still makes progress.

    The overlap never re-includes the previous window's first unit and never exceeds the
    measured cap. If the whole overlap leaves no room for a new unit, it is trimmed to zero.
    """
    if overlap_cap == 0:
        # Zero budget means exactly zero shared text, including zero-token whitespace units.
        return last
    start = last
    overlap_total = 0
    while start - 1 > first and overlap_total + units[start - 1].tokens <= overlap_cap:
        start -= 1
        overlap_total += units[start].tokens
    while (
        start < last
        and count_tokens(transcript_text[units[start].start : units[last - 1].end]) > overlap_cap
    ):
        start += 1
    while start < last and (
        _window_end(
            units,
            transcript_text,
            start,
            target_tokens=target_tokens,
            count_tokens=count_tokens,
        )
        <= last
    ):
        start += 1
    return start


def _measure_ranges(
    units: Sequence[TextUnit],
    transcript_text: str,
    ranges: Sequence[tuple[int, int]],
    count_tokens: TokenCounter,
) -> int:
    return sum(
        count_tokens(transcript_text[units[first].start : units[last - 1].end])
        for first, last in ranges
    )


def _build_windows(
    *,
    units: Sequence[TextUnit],
    transcript_text: str,
    segments: Sequence[SegmentInput],
    ranges: Sequence[tuple[int, int]],
    count_tokens: TokenCounter,
) -> tuple[AnalysisWindow, ...]:
    windows: list[AnalysisWindow] = []
    previous_end: int | None = None
    for ordinal, (first, last) in enumerate(ranges):
        char_start = units[first].start
        char_end = units[last - 1].end
        overlap_char_end = char_start if previous_end is None else min(char_end, previous_end)
        parts: list[WindowSegment] = []
        for segment in segments:
            window_start = max(char_start, segment.char_start)
            window_end = min(char_end, segment.char_end)
            if window_end <= window_start:
                continue
            parts.append(
                WindowSegment(
                    segment_id=segment.id,
                    ordinal=segment.ordinal,
                    segment_char_start=segment.char_start,
                    segment_char_end=segment.char_end,
                    window_char_start=window_start,
                    window_char_end=window_end,
                    text=transcript_text[window_start:window_end],
                )
            )
        content = transcript_text[char_start:char_end]
        windows.append(
            AnalysisWindow(
                ordinal=ordinal,
                char_start=char_start,
                char_end=char_end,
                text=content,
                token_count=count_tokens(content),
                overlap_char_end=overlap_char_end,
                overlap_tokens=count_tokens(transcript_text[char_start:overlap_char_end]),
                segments=tuple(parts),
            )
        )
        previous_end = char_end
    return tuple(windows)

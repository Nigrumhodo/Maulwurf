"""Chunking of transcripts into indexable ~800-token windows (L2.1, S2.md §1.1).

Pure decision core: no I/O, no DB, no router imports. It turns a stored
``transcript.text`` plus its ordered ``segments`` rows into chunk drafts that are 1:1
with the ``chunks``/``chunk_segments`` tables of migration ``0002_s2_schema``
(ESPECIFICACION.md M6: target ~800 tokens with ~100 overlap, preferred cuts at sentence
and ASR segment boundaries, Unicode offsets ``[start, end)`` preserved).

Offset convention (frozen by the L1.4 contract): every offset is a Python string index,
i.e. a Unicode code point, never a UTF-16 unit — an emoji counts as one. ``content`` is
always exactly ``transcript.text[char_start:char_end]``.

Token counting is injected (``count_tokens``) because D5 (the provider tokenizer) is not
closed yet; the default is a deterministic, offline heuristic. The definitive
``token_count`` semantics arrive with L2.2/D5.

This module deliberately does NOT import the §M6 Pydantic schema: reconciling it with the
persisted tables is ticket L2.9, which is still open. The drafts below are shaped after the
tables instead, so they persist with no extra mapping once L2.2 consumes them.
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from uuid import UUID

# Contract defaults (ESPECIFICACION.md M6 / S2.md §1.1).
TARGET_TOKENS = 800
OVERLAP_TOKENS = 100

TokenCounter = Callable[[str], int]

# Deterministic offline token approximation used until D5 fixes the provider tokenizer.
# Each CJK ideograph/kana/hangul is one token (real tokenizers treat them individually); a
# run of other word characters is one token; every other non-space code point (punctuation,
# emoji) is one token.
_CJK = "\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af"
_TOKEN_RE = re.compile(rf"[{_CJK}]|[^\W_]+|\S", re.UNICODE)

# A sentence ends after a run of terminal punctuation (optionally followed by closing
# quotes/brackets) or after a newline run. Trailing whitespace is left to the next span so
# the spans stay contiguous.
_SENTENCE_BOUNDARY = re.compile(r"[.!?\u2026\u3002\uff01\uff1f]+[)\]\"'\u201d\u2019\u00bb]*|\n+")


def count_tokens_default(text: str) -> int:
    """Approximate token count used until the provider tokenizer (D5) is wired in."""
    return len(_TOKEN_RE.findall(text))


def split_sentences(text: str) -> list[tuple[int, int]]:
    """Split ``text`` into contiguous sentence spans ``[start, end)`` covering it fully.

    Heuristic Unicode splitter: false splits on abbreviations are tolerated because the
    chunker only needs *preferred* cut points, never a linguistically perfect analysis.
    """
    if not text:
        return []
    spans: list[tuple[int, int]] = []
    start = 0
    for match in _SENTENCE_BOUNDARY.finditer(text):
        end = match.end()
        if end > start:
            spans.append((start, end))
            start = end
    if start < len(text):
        spans.append((start, len(text)))
    return spans


@dataclass(frozen=True)
class SegmentInput:
    """Minimal view of a `segments` row consumed by the chunker.

    ``char_start``/``char_end`` are global offsets over ``transcript.text``; ``text`` must
    equal ``transcript.text[char_start:char_end]``.
    """

    id: UUID
    ordinal: int
    text: str
    char_start: int
    char_end: int
    t_start: float | None
    t_end: float | None


@dataclass(frozen=True)
class ChunkSegmentDraft:
    """One `chunk_segments` row: offsets local to the segment, plus the overlap region."""

    segment_id: UUID
    segment_ordinal: int
    inicio_char: int
    fin_char: int
    overlap_span: dict[str, int] | None


@dataclass(frozen=True)
class ChunkDraft:
    """One `chunks` row: global character offsets and its links to segments."""

    audio_id: UUID
    transcript_version: int
    index_version: str
    ordinal: int
    content: str
    char_start: int
    char_end: int
    token_count: int
    t_start: float | None
    t_end: float | None
    segments: list[ChunkSegmentDraft]


@dataclass(frozen=True)
class _Unit:
    """Atomic chunker unit: a sentence span, or a token-bounded piece of one."""

    start: int
    end: int
    tokens: int
    segment_index: int


def chunk_segments(
    *,
    audio_id: UUID,
    transcript_version: int,
    index_version: str,
    transcript_text: str,
    segments: Sequence[SegmentInput],
    target_tokens: int = TARGET_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS,
    count_tokens: TokenCounter = count_tokens_default,
) -> list[ChunkDraft]:
    """Produce the ordered chunk drafts for one transcript version.

    Raises ``ValueError`` when the input violates the frozen invariants (non-increasing
    ordinals, a segment range outside the text, a ``text`` that does not match its range,
    non-contiguous segments, or non-blank text outside the segments). Blank edges before
    the first or after the last segment are tolerated. An empty transcript returns no
    chunks.
    """
    if target_tokens < 1:
        raise ValueError("target_tokens debe ser >= 1")
    if overlap_tokens < 0:
        raise ValueError("overlap_tokens debe ser >= 0")
    if not transcript_text:
        return []
    if not segments:
        raise ValueError("se requiere al menos un segmento cuando hay texto")

    _validate_segments(transcript_text, segments)

    units: list[_Unit] = []
    for segment_index, segment in enumerate(segments):
        units.extend(
            _segment_units(
                segment_index,
                segment,
                target_tokens=target_tokens,
                count_tokens=count_tokens,
            )
        )
    if not units:
        return []

    drafts: list[ChunkDraft] = []
    previous_range: tuple[int, int] | None = None
    for ordinal, (first, last) in enumerate(_chunk_ranges(units, target_tokens, overlap_tokens)):
        char_start = units[first].start
        char_end = units[last - 1].end
        drafts.append(
            _build_draft(
                ordinal=ordinal,
                char_start=char_start,
                char_end=char_end,
                previous_range=previous_range,
                audio_id=audio_id,
                transcript_version=transcript_version,
                index_version=index_version,
                transcript_text=transcript_text,
                segments=segments,
                count_tokens=count_tokens,
            )
        )
        previous_range = (char_start, char_end)
    return drafts


def _validate_segments(transcript_text: str, segments: Sequence[SegmentInput]) -> None:
    previous: SegmentInput | None = None
    for segment in segments:
        if previous is not None:
            if segment.ordinal <= previous.ordinal:
                raise ValueError(
                    f"segments debe ir en ordinal estrictamente creciente ({segment.ordinal})"
                )
            if segment.char_start != previous.char_end:
                raise ValueError(
                    f"segments no contiguo en el ordinal {segment.ordinal}: "
                    f"{segment.char_start} != {previous.char_end}"
                )
        if segment.char_start < 0 or segment.char_end > len(transcript_text):
            raise ValueError(f"rango fuera del texto en el ordinal {segment.ordinal}")
        # Empty segments (``char_start == char_end``, text ``""``) are tolerated: ASR
        # engines emit them. Only inverted ranges are rejected.
        if segment.char_start > segment.char_end:
            raise ValueError(f"rango invertido en el ordinal {segment.ordinal}")
        if segment.text != transcript_text[segment.char_start : segment.char_end]:
            raise ValueError(f"texto inconsistente con el rango en el ordinal {segment.ordinal}")
        # Zero-duration segments are tolerated (ASR may emit them); only inverted or
        # negative ranges are rejected.
        if segment.t_start is not None and segment.t_end is not None:
            if segment.t_start < 0 or segment.t_start > segment.t_end:
                raise ValueError(f"tiempos inválidos en el ordinal {segment.ordinal}")
        elif (segment.t_start is None) != (segment.t_end is None):
            raise ValueError(f"t_start y t_end van juntos o ambos nulos ({segment.ordinal})")
        previous = segment
    if transcript_text[: segments[0].char_start].strip():
        raise ValueError("text before the first segment is not covered")
    if transcript_text[segments[-1].char_end :].strip():
        raise ValueError("text after the last segment is not covered")


def _segment_units(
    segment_index: int,
    segment: SegmentInput,
    *,
    target_tokens: int,
    count_tokens: TokenCounter,
) -> list[_Unit]:
    units: list[_Unit] = []
    for local_start, local_end in split_sentences(segment.text):
        global_start = segment.char_start + local_start
        global_end = segment.char_start + local_end
        tokens = count_tokens(segment.text[local_start:local_end])
        if tokens <= target_tokens:
            units.append(_Unit(global_start, global_end, tokens, segment_index))
        else:
            units.extend(
                _split_oversized(
                    text=segment.text,
                    offset=segment.char_start,
                    start=local_start,
                    end=local_end,
                    segment_index=segment_index,
                    target_tokens=target_tokens,
                    count_tokens=count_tokens,
                )
            )
    return units


def _last_whitespace(text: str, start: int, end: int) -> int:
    """Index of the last whitespace code point in ``text[start:end]``, or ``-1``.

    Scans backwards so the common case (a space-like run near ``end``) stops on the first
    step, unlike a forward scan that visits every whitespace in the span. ``str.isspace``
    covers all Unicode whitespace (spaces, tabs, newlines, non-breaking spaces).
    """
    index = end - 1
    while index >= start:
        if text[index].isspace():
            return index
        index -= 1
    return -1


def _split_oversized(
    *,
    text: str,
    offset: int,
    start: int,
    end: int,
    segment_index: int,
    target_tokens: int,
    count_tokens: TokenCounter,
) -> list[_Unit]:
    """Split a sentence that alone exceeds ``target_tokens``, preserving character offsets.

    The cut prefers the last whitespace inside the token-bounded prefix so a word is not
    split unless unavoidable (for example in CJK text with no spaces).
    """
    units: list[_Unit] = []
    position = start
    while position < end:
        remaining = count_tokens(text[position:end])
        if remaining <= target_tokens:
            units.append(_Unit(offset + position, offset + end, remaining, segment_index))
            break
        low, high = position + 1, end
        best = position + 1
        while low <= high:
            middle = (low + high) // 2
            if count_tokens(text[position:middle]) <= target_tokens:
                best = middle
                low = middle + 1
            else:
                high = middle - 1
        split = best
        whitespace = _last_whitespace(text, position, split)
        if whitespace > position:
            split = whitespace + 1
        elif split <= position:
            split = position + 1
        units.append(
            _Unit(
                offset + position,
                offset + split,
                count_tokens(text[position:split]),
                segment_index,
            )
        )
        position = split
    return units


def _chunk_ranges(
    units: Sequence[_Unit], target_tokens: int, overlap_tokens: int
) -> list[tuple[int, int]]:
    """Greedy unit ranges, each overlapping the previous one by up to ``overlap_tokens``.

    Every unit already fits ``target_tokens`` (oversized sentences were pre-split), so a
    chunk always includes at least one unit. The overlap re-includes the previous chunk's
    trailing units while they fit the overlap budget, but a chunk is never a subset of its
    predecessor: if carrying the overlap forward would leave no room for a new unit, the
    overlap is trimmed by whole units until the next chunk advances past ``last``. A zero
    overlap budget therefore yields contiguous, non-overlapping chunks.
    """
    ranges: list[tuple[int, int]] = []
    total = len(units)
    first = 0
    while first < total:
        last = _greedy_end(units, first, target_tokens)
        ranges.append((first, last))
        if last >= total:
            break
        first = _next_start(units, first, last, target_tokens, overlap_tokens)
    return ranges


def _greedy_end(units: Sequence[_Unit], first: int, target_tokens: int) -> int:
    """Exclusive end of the greedy chunk that starts at ``first`` (at least one unit)."""
    last = first
    tokens = 0
    while last < len(units) and (last == first or tokens + units[last].tokens <= target_tokens):
        tokens += units[last].tokens
        last += 1
    return last


def _next_start(
    units: Sequence[_Unit], first: int, last: int, target_tokens: int, overlap_tokens: int
) -> int:
    """First unit of the next chunk: sentence-aligned overlap that still makes progress.

    Starts from the maximal overlap that fits ``overlap_tokens`` (never re-including the
    previous chunk's first unit) and, while the resulting chunk would not extend past
    ``last``, trims the overlap by one unit. Starting at ``last`` always adds a new unit, so
    the loop terminates and no chunk is overlap-only.
    """
    start = last
    overlap_total = 0
    while start - 1 > first and overlap_total + units[start - 1].tokens <= overlap_tokens:
        start -= 1
        overlap_total += units[start].tokens
    while start < last and _greedy_end(units, start, target_tokens) <= last:
        start += 1
    return start


def _build_draft(
    *,
    ordinal: int,
    char_start: int,
    char_end: int,
    previous_range: tuple[int, int] | None,
    audio_id: UUID,
    transcript_version: int,
    index_version: str,
    transcript_text: str,
    segments: Sequence[SegmentInput],
    count_tokens: TokenCounter,
) -> ChunkDraft:
    links: list[ChunkSegmentDraft] = []
    times: list[tuple[float | None, float | None]] = []
    for segment in segments:
        local_start = max(char_start, segment.char_start) - segment.char_start
        local_end = min(char_end, segment.char_end) - segment.char_start
        if local_end <= local_start:
            continue
        links.append(
            ChunkSegmentDraft(
                segment_id=segment.id,
                segment_ordinal=segment.ordinal,
                inicio_char=local_start,
                fin_char=local_end,
                overlap_span=_overlap_span(
                    char_start, char_end, previous_range, segment, transcript_text, count_tokens
                ),
            )
        )
        times.append((segment.t_start, segment.t_end))
    t_start, t_end = _derive_times(times)
    content = transcript_text[char_start:char_end]
    return ChunkDraft(
        audio_id=audio_id,
        transcript_version=transcript_version,
        index_version=index_version,
        ordinal=ordinal,
        content=content,
        char_start=char_start,
        char_end=char_end,
        token_count=count_tokens(content),
        t_start=t_start,
        t_end=t_end,
        segments=links,
    )


def _overlap_span(
    char_start: int,
    char_end: int,
    previous_range: tuple[int, int] | None,
    segment: SegmentInput,
    transcript_text: str,
    count_tokens: TokenCounter,
) -> dict[str, int] | None:
    """Global overlap region with the previous chunk, clamped to this segment; else ``None``.

    The JSON shape is provisional: L2.9 owns validating it against the `overlap_span` jsonb
    column once the §M6 contract and the persisted tables are reconciled.
    """
    if previous_range is None:
        return None
    overlap_start = max(char_start, previous_range[0])
    overlap_end = min(char_end, previous_range[1])
    span_start = max(overlap_start, segment.char_start)
    span_end = min(overlap_end, segment.char_end)
    if span_start >= span_end:
        return None
    return {
        "char_start": span_start,
        "char_end": span_end,
        "tokens": count_tokens(transcript_text[span_start:span_end]),
    }


def _derive_times(
    times: Sequence[tuple[float | None, float | None]],
) -> tuple[float | None, float | None]:
    if not times:
        return (None, None)
    starts: list[float] = []
    ends: list[float] = []
    for start, end in times:
        if start is None or end is None:
            return (None, None)
        starts.append(start)
        ends.append(end)
    return (min(starts), max(ends))

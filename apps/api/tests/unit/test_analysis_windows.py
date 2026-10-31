"""U-S3-SG-02: bounded analysis windows and dedupe by evidence (S3.2).

The analyzer must never send a whole transcript that exceeds the context. It processes
sentence-aligned windows with a small overlap, but the overlap is paid for twice: those
tokens are part of the cost. These tests pin three contracts of
``app/services/analysis_windows.py``:

- coverage: windows advance over the transcript without gaps, and only the declared
  overlap is repeated;
- cost: each window respects the target, the overlap respects its own budget, and an
  optional global budget is a hard, measured bound (overlap is trimmed, never exceeded);
- evidence: the same segment + range + quote is one identity even when two overlapping
  windows produced it.

Token counting is injected with a deterministic word counter so the boundaries do not
depend on a tokenizer download. The real tokenizer is exercised by ``test_window_cost.py``.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest

from app.services.analysis_windows import (
    AnalysisWindow,
    EvidenceSpan,
    WindowPlan,
    dedupe_by_evidence,
    evidence_key,
    evidence_span_in_window,
    plan_windows,
)
from app.services.chunking import SegmentInput, split_sentences


def _word_count(text: str) -> int:
    return len(text.split())


def _build(pieces: Sequence[str]) -> tuple[str, list[SegmentInput]]:
    """Transcript text plus contiguous segments; each segment owns its trailing space."""
    text = ""
    segments: list[SegmentInput] = []
    for ordinal, piece in enumerate(pieces):
        start = len(text)
        text += piece
        if ordinal < len(pieces) - 1:
            text += " "
        segments.append(
            SegmentInput(
                id=uuid4(),
                ordinal=ordinal,
                text=text[start:],
                char_start=start,
                char_end=len(text),
                t_start=None,
                t_end=None,
            )
        )
    return text, segments


def _sentences(count: int, words: int) -> list[str]:
    return [" ".join(f"w{index}_{word}" for word in range(words)) + "." for index in range(count)]


def _plan(
    pieces: Sequence[str],
    *,
    target_tokens: int = 40,
    overlap_tokens: int = 10,
    max_total_tokens: int | None = None,
) -> tuple[str, list[SegmentInput], WindowPlan]:
    text, segments = _build(pieces)
    plan = plan_windows(
        transcript_text=text,
        segments=segments,
        target_tokens=target_tokens,
        overlap_tokens=overlap_tokens,
        max_total_tokens=max_total_tokens,
        count_tokens=_word_count,
    )
    return text, segments, plan


def test_windows_cover_the_transcript_without_gaps() -> None:
    text, segments, plan = _plan(_sentences(40, 8))

    assert len(plan.windows) >= 3
    assert plan.windows[0].char_start == 0
    assert plan.windows[-1].char_end == len(text)

    covered_end = plan.windows[0].char_start
    for window in plan.windows:
        assert window.char_end > covered_end, "every window must add new text"
        assert text[window.char_start : window.char_end] == window.text
        covered_end = window.char_end
    assert covered_end == len(text)

    unit_starts = {
        segment.char_start + local_start
        for segment in segments
        for local_start, _ in split_sentences(segment.text)
    }
    for window in plan.windows:
        assert window.char_start in unit_starts, "windows cut at sentence boundaries"


def test_each_window_respects_the_token_target() -> None:
    _, _, plan = _plan(_sentences(40, 8), target_tokens=40)

    assert all(window.token_count <= 40 for window in plan.windows)
    assert all(window.token_count == _word_count(window.text) for window in plan.windows)


def test_overlap_is_bounded_and_matches_the_previous_window() -> None:
    text, _, plan = _plan(_sentences(40, 8), target_tokens=40, overlap_tokens=10)

    assert plan.windows[0].overlap_char_end == plan.windows[0].char_start
    assert plan.windows[0].overlap_tokens == 0
    for previous, current in zip(plan.windows, plan.windows[1:], strict=False):
        assert current.char_start < previous.char_end
        assert current.overlap_char_end == previous.char_end
        assert current.overlap_tokens <= 10
        overlap_text = text[current.char_start : current.overlap_char_end]
        assert overlap_text
        assert current.token_count == _word_count(current.text)
        assert current.overlap_tokens == _word_count(overlap_text)
    assert plan.overlap_tokens_total <= 10 * (len(plan.windows) - 1)


def test_window_segments_are_clipped_and_contiguous() -> None:
    text, _, plan = _plan(_sentences(40, 8), target_tokens=40, overlap_tokens=10)

    for window in plan.windows:
        assert window.segments
        assert "".join(part.text for part in window.segments) == window.text
        for part in window.segments:
            assert part.text == text[part.window_char_start : part.window_char_end]
            assert part.segment_char_start <= part.window_char_start
            assert part.window_char_end <= part.segment_char_end
            assert part.local_char_start == part.window_char_start - part.segment_char_start
            assert part.local_char_end == part.window_char_end - part.segment_char_start


def test_zero_overlap_makes_disjoint_windows() -> None:
    text, _, plan = _plan(_sentences(40, 8), target_tokens=40, overlap_tokens=0)

    assert plan.overlap_tokens_total == 0
    for previous, current in zip(plan.windows, plan.windows[1:], strict=False):
        assert current.char_start == previous.char_end
    assert "".join(window.text for window in plan.windows) == text


def test_global_budget_is_a_hard_measured_bound() -> None:
    pieces = _sentences(40, 8)
    text, segments = _build(pieces)
    full = plan_windows(
        transcript_text=text,
        segments=segments,
        target_tokens=40,
        overlap_tokens=10,
        count_tokens=_word_count,
    )
    floor = plan_windows(
        transcript_text=text,
        segments=segments,
        target_tokens=40,
        overlap_tokens=0,
        count_tokens=_word_count,
    )
    assert full.total_tokens > floor.total_tokens
    budget = (full.total_tokens + floor.total_tokens) // 2

    limited = plan_windows(
        transcript_text=text,
        segments=segments,
        target_tokens=40,
        overlap_tokens=10,
        max_total_tokens=budget,
        count_tokens=_word_count,
    )

    assert limited.total_tokens <= budget
    assert limited.budget_limited is True
    assert limited.effective_overlap_tokens < 10
    assert limited.overlap_tokens_total < full.overlap_tokens_total
    assert limited.windows[0].char_start == 0
    assert limited.windows[-1].char_end == len(text)
    assert all(window.token_count <= 40 for window in limited.windows)


def test_budget_equal_to_the_floor_drops_all_overlap() -> None:
    text, segments = _build(_sentences(40, 8))
    floor = plan_windows(
        transcript_text=text,
        segments=segments,
        target_tokens=40,
        overlap_tokens=0,
        count_tokens=_word_count,
    )

    limited = plan_windows(
        transcript_text=text,
        segments=segments,
        target_tokens=40,
        overlap_tokens=10,
        max_total_tokens=floor.total_tokens,
        count_tokens=_word_count,
    )

    assert limited.total_tokens <= floor.total_tokens
    assert limited.overlap_tokens_total == 0
    assert limited.budget_limited is True


def test_budget_above_the_natural_cost_keeps_the_overlap() -> None:
    text, segments = _build(_sentences(40, 8))
    full = plan_windows(
        transcript_text=text,
        segments=segments,
        target_tokens=40,
        overlap_tokens=10,
        count_tokens=_word_count,
    )

    granted = plan_windows(
        transcript_text=text,
        segments=segments,
        target_tokens=40,
        overlap_tokens=10,
        max_total_tokens=full.total_tokens,
        count_tokens=_word_count,
    )

    assert granted.total_tokens == full.total_tokens
    assert granted.effective_overlap_tokens == 10
    assert granted.budget_limited is False


def test_budget_that_cannot_cover_the_text_is_rejected() -> None:
    text, segments = _build(_sentences(40, 8))
    floor = plan_windows(
        transcript_text=text,
        segments=segments,
        target_tokens=40,
        overlap_tokens=0,
        count_tokens=_word_count,
    )

    with pytest.raises(ValueError, match="max_total_tokens"):
        plan_windows(
            transcript_text=text,
            segments=segments,
            target_tokens=40,
            overlap_tokens=10,
            max_total_tokens=floor.total_tokens - 1,
            count_tokens=_word_count,
        )


@pytest.mark.parametrize(
    ("target_tokens", "overlap_tokens"),
    [(0, 0), (-1, 0), (40, -1), (40, 40), (40, 41)],
)
def test_invalid_window_parameters_are_rejected(
    target_tokens: int, overlap_tokens: int
) -> None:
    text, segments = _build(_sentences(4, 8))

    with pytest.raises(ValueError):
        plan_windows(
            transcript_text=text,
            segments=segments,
            target_tokens=target_tokens,
            overlap_tokens=overlap_tokens,
            count_tokens=_word_count,
        )


def test_text_without_segments_is_rejected() -> None:
    with pytest.raises(ValueError):
        plan_windows(
            transcript_text="hay texto",
            segments=[],
            target_tokens=40,
            overlap_tokens=10,
            count_tokens=_word_count,
        )


def test_inconsistent_segment_is_rejected() -> None:
    text, segments = _build(["una frase.", "otra frase."])
    broken = segments[1]
    segments[1] = SegmentInput(
        id=broken.id,
        ordinal=broken.ordinal,
        text="texto distinto",
        char_start=broken.char_start,
        char_end=broken.char_end,
        t_start=None,
        t_end=None,
    )

    with pytest.raises(ValueError):
        plan_windows(
            transcript_text=text,
            segments=segments,
            target_tokens=40,
            overlap_tokens=10,
            count_tokens=_word_count,
        )


def test_empty_transcript_produces_an_empty_plan() -> None:
    plan = plan_windows(
        transcript_text="   ",
        segments=[],
        target_tokens=40,
        overlap_tokens=10,
        count_tokens=_word_count,
    )

    assert plan.windows == ()
    assert plan.total_tokens == 0
    assert plan.transcript_tokens == 0
    assert plan.amplification == 0.0


def test_oversized_sentence_is_split_within_the_target() -> None:
    text, _, plan = _plan([" ".join(f"w{index}" for index in range(500))], target_tokens=40)

    assert len(plan.windows) >= 10
    assert all(window.token_count <= 40 for window in plan.windows)
    assert plan.windows[0].char_start == 0
    assert plan.windows[-1].char_end == len(text)
    covered_end = 0
    for window in plan.windows:
        assert window.char_end > covered_end
        covered_end = window.char_end


def test_unicode_offsets_are_code_points() -> None:
    text, _, plan = _plan(
        ["día 🎓 微積分 café.", "Otra frase con ñandú y más texto."],
        target_tokens=3,
        overlap_tokens=1,
    )

    assert len(text.encode("utf-16-le")) // 2 > len(text)
    for window in plan.windows:
        assert window.text == text[window.char_start : window.char_end]
    assert plan.windows[-1].char_end == len(text)


def test_planning_is_deterministic() -> None:
    text, segments = _build(_sentences(40, 8))
    first = plan_windows(
        transcript_text=text,
        segments=segments,
        target_tokens=40,
        overlap_tokens=10,
        count_tokens=_word_count,
    )
    second = plan_windows(
        transcript_text=text,
        segments=segments,
        target_tokens=40,
        overlap_tokens=10,
        count_tokens=_word_count,
    )

    assert first == second


@dataclass(frozen=True)
class _Item:
    label: str
    spans: tuple[EvidenceSpan, ...]


def _spans(item: _Item) -> Sequence[EvidenceSpan]:
    return item.spans


def _span(
    *,
    segment_id: UUID,
    char_start: int,
    char_end: int,
    quote: str,
) -> EvidenceSpan:
    return EvidenceSpan(
        segment_id=segment_id,
        char_start=char_start,
        char_end=char_end,
        quote=quote,
    )


def test_evidence_span_rejects_invalid_ranges_and_quotes() -> None:
    segment_id = uuid4()
    with pytest.raises(ValueError):
        _span(segment_id=segment_id, char_start=2, char_end=2, quote="")
    with pytest.raises(ValueError):
        _span(segment_id=segment_id, char_start=-1, char_end=1, quote="ab")
    with pytest.raises(ValueError):
        _span(segment_id=segment_id, char_start=0, char_end=3, quote="ab")


def test_evidence_key_is_the_exact_segment_range_and_quote() -> None:
    segment_id = uuid4()
    span = _span(segment_id=segment_id, char_start=1, char_end=4, quote="abc")

    assert evidence_key(span) == (segment_id, 1, 4, "abc")


def test_dedupe_keeps_the_first_item_for_identical_evidence() -> None:
    segment_id = uuid4()
    span = _span(segment_id=segment_id, char_start=0, char_end=4, quote="hola")
    first = _Item("first", (span,))
    second = _Item("second", (span,))

    assert dedupe_by_evidence([first, second], evidence=_spans) == [first]


def test_dedupe_distinguishes_segment_range_or_quote() -> None:
    segment_a, segment_b = uuid4(), uuid4()
    base = _span(segment_id=segment_a, char_start=0, char_end=4, quote="hola")
    candidates = [
        _Item("base", (base,)),
        _Item(
            "other-segment",
            (_span(segment_id=segment_b, char_start=0, char_end=4, quote="hola"),),
        ),
        _Item(
            "other-start",
            (_span(segment_id=segment_a, char_start=1, char_end=5, quote="hola"),),
        ),
        _Item(
            "other-quote",
            (_span(segment_id=segment_a, char_start=0, char_end=5, quote="hola!"),),
        ),
    ]

    kept = dedupe_by_evidence(candidates, evidence=_spans)

    assert [item.label for item in kept] == [
        "base",
        "other-segment",
        "other-start",
        "other-quote",
    ]


def test_dedupe_ignores_the_order_of_multiple_sources() -> None:
    segment_a, segment_b = uuid4(), uuid4()
    first_span = _span(segment_id=segment_a, char_start=0, char_end=4, quote="hola")
    second_span = _span(segment_id=segment_b, char_start=2, char_end=7, quote="mundo")
    first = _Item("first", (first_span, second_span))
    second = _Item("second", (second_span, first_span))

    assert dedupe_by_evidence([first, second], evidence=_spans) == [first]


def test_dedupe_does_not_merge_a_subset_of_sources() -> None:
    segment_a, segment_b = uuid4(), uuid4()
    first = _span(segment_id=segment_a, char_start=0, char_end=4, quote="hola")
    second = _span(segment_id=segment_b, char_start=2, char_end=7, quote="mundo")
    partial = _Item("partial", (first,))
    complete = _Item("complete", (first, second))

    assert dedupe_by_evidence([partial, complete], evidence=_spans) == [partial, complete]


def test_dedupe_never_collapses_items_without_evidence() -> None:
    first = _Item("suggestion-a", ())
    second = _Item("suggestion-b", ())

    assert dedupe_by_evidence([first, second], evidence=_spans) == [first, second]


def test_dedupe_is_stable_and_preserves_input_order() -> None:
    segment_id = uuid4()
    a = _Item("a", (_span(segment_id=segment_id, char_start=0, char_end=1, quote="a"),))
    b = _Item("b", (_span(segment_id=segment_id, char_start=2, char_end=3, quote="b"),))
    a_again = _Item("a-again", (_span(segment_id=segment_id, char_start=0, char_end=1, quote="a"),))

    assert dedupe_by_evidence([b, a, a_again], evidence=_spans) == [b, a]


def test_evidence_from_two_overlapping_windows_is_deduplicated() -> None:
    text, _, plan = _plan(_sentences(40, 8), target_tokens=40, overlap_tokens=10)
    first, second = plan.windows[0], plan.windows[1]
    shared = [
        part
        for part in first.segments
        if second.char_start <= part.window_char_start and part.window_char_end <= second.char_end
    ]
    assert shared
    part = shared[0]

    from_first = evidence_span_in_window(
        first,
        segment_id=part.segment_id,
        char_start=part.window_char_start,
        char_end=part.window_char_end,
    )
    from_second = evidence_span_in_window(
        second,
        segment_id=part.segment_id,
        char_start=part.window_char_start,
        char_end=part.window_char_end,
    )

    assert from_first == from_second
    assert from_first.quote == text[part.window_char_start : part.window_char_end]
    items = [_Item("window-0", (from_first,)), _Item("window-1", (from_second,))]
    assert dedupe_by_evidence(items, evidence=_spans) == [items[0]]


def test_evidence_span_in_window_rejects_foreign_ranges() -> None:
    _, _, plan = _plan(_sentences(20, 8), target_tokens=40, overlap_tokens=10)
    window = plan.windows[0]
    part = window.segments[0]

    with pytest.raises(ValueError):
        evidence_span_in_window(
            window,
            segment_id=uuid4(),
            char_start=part.window_char_start,
            char_end=part.window_char_end,
        )
    with pytest.raises(ValueError):
        evidence_span_in_window(
            window,
            segment_id=part.segment_id,
            char_start=window.char_start - 1,
            char_end=window.char_start,
        )
    with pytest.raises(ValueError):
        evidence_span_in_window(
            window,
            segment_id=part.segment_id,
            char_start=part.window_char_end,
            char_end=part.window_char_end,
        )


def test_analysis_window_text_is_the_exact_transcript_slice() -> None:
    text, _, plan = _plan(_sentences(10, 8))
    window: AnalysisWindow = plan.windows[0]

    assert window.text == text[window.char_start : window.char_end]
    assert window.overlap_char_start == window.char_start

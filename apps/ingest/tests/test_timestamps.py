"""U-S1-SG-03: global = fragment offset + local, without adding the overlap twice."""

from __future__ import annotations

import pytest

from maulwurf_ingest.audio.timestamps import (
    ends_before_trailing_silence,
    global_time_s,
    propose_precision,
    scale_pairs,
    spans_are_valid,
    word_time_scale_s,
)

_CLIENT_MS = '''
value = ["Word", "Start (ms)", "End (ms)"]
"start_time": str(group_words[0].start_time / 1000),
'''


def test_global_time_does_not_add_overlap() -> None:
    """A local time already sits on the fragment clock. Overlap is not added again."""
    fragment_offset_s = 10.0
    overlap_s = 2.0
    local_s = 2.5
    assert global_time_s(fragment_offset_s, local_s, overlap_s) == pytest.approx(12.5)
    doubled = fragment_offset_s + overlap_s + local_s
    assert global_time_s(fragment_offset_s, local_s, overlap_s) != pytest.approx(doubled)


def test_global_time_after_the_overlap_region() -> None:
    assert global_time_s(10.0, 4.0, 2.0) == pytest.approx(14.0)


def test_negative_time_is_rejected() -> None:
    with pytest.raises(ValueError, match="overlap_s"):
        global_time_s(1.0, 1.0, -0.1)


def test_spans_require_seconds_order_and_range() -> None:
    duration_s = 3.0
    assert spans_are_valid([(0.0, 0.4), (0.4, 1.2)], duration_s)
    assert not spans_are_valid([], duration_s)
    assert not spans_are_valid([(0.5, 0.5)], duration_s)
    assert not spans_are_valid([(1.0, 0.2)], duration_s)
    assert not spans_are_valid([(0.0, 3.1)], duration_s)
    assert not spans_are_valid([(0.2, 0.3), (0.2, 0.4)], duration_s)


def test_trailing_silence_is_not_covered() -> None:
    spans = [(0.1, 1.2), (1.2, 1.9)]
    assert ends_before_trailing_silence(
        spans,
        spoken_duration_s=2.0,
        total_duration_s=4.0,
    )
    assert not ends_before_trailing_silence(
        [(0.1, 3.5)],
        spoken_duration_s=2.0,
        total_duration_s=4.0,
    )


def test_precision_prefers_word_then_segment() -> None:
    assert propose_precision(word_ok=True, segment_ok=True) == "word"
    assert propose_precision(word_ok=False, segment_ok=True) == "segment"
    assert propose_precision(word_ok=False, segment_ok=False) == "none"


def test_scale_comes_only_from_documented_milliseconds() -> None:
    assert word_time_scale_s(_CLIENT_MS) == pytest.approx(0.001)
    assert word_time_scale_s("start_time is a float") is None
    assert scale_pairs([[840, 880]], None) == []
    assert scale_pairs([[840, 880]], 0.001) == [(0.84, 0.88)]

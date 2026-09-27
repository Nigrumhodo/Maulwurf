"""Fragment timestamp math for S1.A6 (U-S1-SG-03).

Global time is the fragment offset plus the local time. The overlap is already
inside the local timeline, so it is not added again. This module does not
invent times and does not call a provider.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

SPEECH_END_TOLERANCE_S = 0.25
_RANGE_EPS_S = 1e-3
_MS_TO_S = 0.001

Precision = str


def global_time_s(fragment_offset_s: float, local_s: float, overlap_s: float) -> float:
    """Return ``fragment_offset_s + local_s``. ``overlap_s`` is not applied."""
    _require_seconds("fragment_offset_s", fragment_offset_s)
    _require_seconds("local_s", local_s)
    _require_seconds("overlap_s", overlap_s)
    return fragment_offset_s + local_s


def word_time_scale_s(client_source: str) -> float | None:
    """Seconds per raw unit, only when the installed client documents milliseconds.

    The client must label word times as milliseconds and divide by 1000 itself.
    Any other source returns ``None`` (``NO VERIFICADO``). This does not guess.
    """
    if "Start (ms)" in client_source and "start_time / 1000" in client_source:
        return _MS_TO_S
    return None


def scale_pairs(
    raw_pairs: Sequence[Sequence[float]],
    scale_s: float | None,
) -> list[tuple[float, float]]:
    """Apply a known scale. An unknown scale yields no spans."""
    if scale_s is None:
        return []
    spans: list[tuple[float, float]] = []
    for pair in raw_pairs:
        if len(pair) != 2:
            continue
        start = float(pair[0]) * scale_s
        end = float(pair[1]) * scale_s
        spans.append((start, end))
    return spans


def spans_are_valid(spans: Sequence[tuple[float, float]], duration_s: float) -> bool:
    """Seconds, strict start order, and ``0 ≤ start < end ≤ duration``."""
    if not spans or not math.isfinite(duration_s) or duration_s <= 0:
        return False
    previous_start: float | None = None
    for start_s, end_s in spans:
        if not math.isfinite(start_s) or not math.isfinite(end_s):
            return False
        if start_s < -_RANGE_EPS_S or end_s > duration_s + _RANGE_EPS_S:
            return False
        if start_s >= end_s:
            return False
        if previous_start is not None and start_s <= previous_start:
            return False
        previous_start = start_s
    return True


def ends_before_trailing_silence(
    spans: Sequence[tuple[float, float]],
    *,
    spoken_duration_s: float,
    total_duration_s: float,
    tolerance_s: float = SPEECH_END_TOLERANCE_S,
) -> bool:
    """The last offset stays in the spoken region when silence was appended."""
    if not spans:
        return False
    if not math.isfinite(spoken_duration_s) or not math.isfinite(total_duration_s):
        return False
    if total_duration_s <= spoken_duration_s:
        return False
    return spans[-1][1] <= spoken_duration_s + tolerance_s


def propose_precision(*, word_ok: bool, segment_ok: bool) -> Precision:
    """Prefer word, then segment. Otherwise ``none``. Never invents a fourth value."""
    if word_ok:
        return "word"
    if segment_ok:
        return "segment"
    return "none"


def _require_seconds(name: str, value: float) -> None:
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a non-negative finite number of seconds")

"""S2.2: el corte cae en un silencio, el solape se comparte y 10 min no es el techo."""

from __future__ import annotations

import math
import shutil
import wave
from pathlib import Path

import pytest

from maulwurf_ingest.audio.ffmpeg import DEFAULT_LIMITS
from maulwurf_ingest.audio.fragmenter import (
    OBSERVED_WORKING_DURATION_S,
    detect_silences,
    effective_fragment_seconds,
    plan_fragments,
)

_RATE = 16_000


def test_ten_minutes_is_clamped_to_the_observed_duration() -> None:
    assert effective_fragment_seconds(600) == OBSERVED_WORKING_DURATION_S
    assert OBSERVED_WORKING_DURATION_S == 30.0
    spans = plan_fragments(100.0, [], max_fragment_s=600, overlap_s=0.25)
    assert spans
    assert all(span.end_s - span.start_s <= OBSERVED_WORKING_DURATION_S + 1e-6 for span in spans)
    assert all(not math.isclose(span.end_s - span.start_s, 600) for span in spans)


def test_cut_falls_inside_silence_and_neighbors_overlap() -> None:
    silence = (1.6, 2.4)
    spans = plan_fragments(4.0, [silence], max_fragment_s=2.5, overlap_s=0.4)
    assert len(spans) == 2
    assert silence[0] <= spans[0].end_s <= silence[1]
    assert spans[1].start_s < spans[0].end_s
    assert spans[0].end_s - spans[1].start_s == pytest.approx(0.4)


def _tone_gap_tone(path: Path) -> tuple[float, float]:
    tone_s = 1.5
    gap_s = 0.8
    tone_n = int(_RATE * tone_s)
    gap_n = int(_RATE * gap_s)
    loud = b"\xff\x7f"
    pcm = loud * tone_n + b"\x00\x00" * gap_n + loud * tone_n
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(_RATE)
        writer.writeframes(pcm)
    return tone_s, tone_s + gap_s


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg no disponible")
def test_detected_silence_is_where_the_plan_cuts(tmp_path: Path) -> None:
    wav_path = tmp_path / "gap.wav"
    gap_start, gap_end = _tone_gap_tone(wav_path)
    silences = detect_silences(wav_path, limits=DEFAULT_LIMITS)
    assert any(start <= gap_start + 0.15 and end >= gap_end - 0.15 for start, end in silences)
    spans = plan_fragments(gap_end + 1.5, silences, max_fragment_s=2.0, overlap_s=0.25)
    assert len(spans) >= 2
    assert gap_start - 0.2 <= spans[0].end_s <= gap_end + 0.2
    assert spans[1].start_s < spans[0].end_s

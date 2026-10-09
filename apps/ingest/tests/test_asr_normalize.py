"""`-fs` trunca con código 0. La normalización tiene que fallar, no transcribir el corte."""

from __future__ import annotations

import shutil
import wave
from dataclasses import replace
from pathlib import Path

import pytest

from maulwurf_ingest.asr_job import (
    ORIGINAL,
    ensure_conversion_complete,
    ensure_pcm_fits,
    run,
)
from maulwurf_ingest.audio.ffmpeg import DEFAULT_LIMITS

_FFPROBE = shutil.which("ffprobe")


def test_pcm_over_the_output_ceiling_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="normalization_output_overflow"):
        ensure_pcm_fits(10.0, 100_000)


def test_a_short_conversion_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="normalization_truncated"):
        ensure_conversion_complete(30.0, 3.2)


def test_a_full_conversion_within_tolerance_is_kept() -> None:
    ensure_conversion_complete(30.0, 29.5)
    ensure_pcm_fits(1.0, DEFAULT_LIMITS.output_bytes)


def _tone(path: Path, *, seconds: float) -> None:
    frames = int(16_000 * seconds)
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16_000)
        writer.writeframes(b"\x00\x00" * frames)


@pytest.mark.skipif(_FFPROBE is None, reason="ffprobe no disponible")
def test_overflow_stops_before_writing_a_truncated_wav(tmp_path: Path) -> None:
    _tone(tmp_path / ORIGINAL, seconds=10.0)
    limits = replace(DEFAULT_LIMITS, output_bytes=100_000)
    with pytest.raises(RuntimeError, match="normalization_output_overflow"):
        run(tmp_path, realtime=False, limits=limits)
    assert not (tmp_path / "converted.wav").exists()

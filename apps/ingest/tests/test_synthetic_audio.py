"""U-S1-SG-01 — synthetic generator: known text, seeded, speech-like, RAM-only."""

from __future__ import annotations

import builtins
import cmath
import io
import math
import struct
import wave
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from maulwurf_ingest.audio import (
    DEFAULT_PHRASE_ES,
    generate_synthetic_utterance,
)
from maulwurf_ingest.audio import synthetic as synthetic_mod

# U-S1-SG-01: in-RAM synthetic speech with a known phrase, seeded, not a tone.


def test_same_seed_is_byte_identical() -> None:
    first = generate_synthetic_utterance(seed=7)
    second = generate_synthetic_utterance(seed=7)
    assert first.wav_bytes == second.wav_bytes
    assert first.expected_text == DEFAULT_PHRASE_ES


def test_different_seed_changes_bytes() -> None:
    a = generate_synthetic_utterance(seed=1)
    b = generate_synthetic_utterance(seed=2)
    assert a.expected_text == b.expected_text
    assert a.wav_bytes != b.wav_bytes


def test_wav_is_pcm_s16_mono() -> None:
    utterance = generate_synthetic_utterance(seed=0)
    with wave.open(io.BytesIO(utterance.wav_bytes), "rb") as reader:
        assert reader.getnchannels() == 1
        assert reader.getsampwidth() == 2
        assert reader.getcomptype() == "NONE"
        assert reader.getnframes() > 0
        assert reader.getframerate() == utterance.sample_rate_hz
        assert utterance.sample_rate_hz > 0


def test_expected_text_is_the_requested_phrase() -> None:
    phrase = "Hola mundo sintético."
    utterance = generate_synthetic_utterance(text=phrase, seed=3, language_code="es")
    assert utterance.expected_text == phrase
    assert utterance.language_code == "es"
    assert utterance.seed == 3


def test_rejects_empty_text() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        generate_synthetic_utterance(text="   ")


def test_spectrum_is_not_a_pure_tone() -> None:
    utterance = generate_synthetic_utterance(seed=0)
    assert _has_speech_like_spectrum(utterance.wav_bytes)
    tone = _pure_sine_wav(frequency_hz=440.0, duration_s=0.4, sample_rate_hz=22050)
    assert not _has_speech_like_spectrum(tone)


def test_does_not_write_wav_to_disk(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    writes: list[str] = []
    real_open = builtins.open

    def tracking_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if any(flag in mode for flag in ("w", "a", "x", "+")):
            writes.append(str(file))
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(builtins, "open", tracking_open)

    generate_synthetic_utterance(seed=0)

    assert writes == []
    assert list(tmp_path.rglob("*.wav")) == []


def test_leading_dash_is_spoken_not_espeak_flags() -> None:
    phrase = "-v de hola"
    utterance = generate_synthetic_utterance(text=phrase, seed=0)
    assert utterance.expected_text == phrase
    with wave.open(io.BytesIO(utterance.wav_bytes), "rb") as reader:
        assert reader.getnframes() > 0


def test_rejects_overlong_utterance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(synthetic_mod, "_MAX_OUTPUT_SECONDS", 0.01)
    with pytest.raises(RuntimeError, match="too long"):
        generate_synthetic_utterance(seed=0)


def _pcm_samples(wav_bytes: bytes) -> tuple[list[int], int]:
    with wave.open(io.BytesIO(wav_bytes), "rb") as reader:
        nframes = reader.getnframes()
        rate = reader.getframerate()
        raw = reader.readframes(nframes)
        width = reader.getsampwidth()
        channels = reader.getnchannels()
    assert width == 2
    assert channels == 1
    samples = list(struct.unpack(f"<{nframes}h", raw))
    return samples, rate


def _dft_magnitudes(window: list[int]) -> Iterator[float]:
    n = len(window)
    for k in range(n // 2):
        acc = 0j
        for t, sample in enumerate(window):
            acc += sample * cmath.exp(-2j * math.pi * k * t / n)
        yield abs(acc) / n


def _has_speech_like_spectrum(wav_bytes: bytes) -> bool:
    """True when energy is spread across several bands (speech), not one peak (tone)."""
    samples, rate = _pcm_samples(wav_bytes)
    size = 512
    if len(samples) < size:
        return False
    start = max(0, (len(samples) - size) // 2)
    window = samples[start : start + size]
    mags = list(_dft_magnitudes(window))
    peak = max(mags[1:], default=0.0)
    if peak <= 0:
        return False
    bands = (
        (300, 800),
        (800, 2000),
        (2000, min(4000, rate // 2 - 1)),
    )
    band_peaks: list[float] = []
    for lo, hi in bands:
        if hi <= lo:
            continue
        lo_bin = max(1, int(lo * size / rate))
        hi_bin = min(len(mags) - 1, int(hi * size / rate))
        if hi_bin <= lo_bin:
            continue
        band_peaks.append(max(mags[lo_bin:hi_bin]))
    strong = [p for p in band_peaks if p > 0.15 * peak]
    return len(strong) >= 2


def _pure_sine_wav(*, frequency_hz: float, duration_s: float, sample_rate_hz: int) -> bytes:
    nframes = int(duration_s * sample_rate_hz)
    frames = bytearray()
    for i in range(nframes):
        sample = int(16000 * math.sin(2 * math.pi * frequency_hz * i / sample_rate_hz))
        frames.extend(struct.pack("<h", sample))
    out = io.BytesIO()
    with wave.open(out, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate_hz)
        writer.writeframes(bytes(frames))
    return out.getvalue()

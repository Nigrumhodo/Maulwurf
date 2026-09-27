"""Local summary for S1.A6. Does not call NVIDIA."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "provider" / "riva_offsets.py"
_SPIKE = Path(__file__).resolve().parents[3] / "scripts" / "provider" / "riva_spike.py"


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _observed(
    *,
    code: str = "OK",
    words: list[list[float]] | None = None,
    segments: list[list[float]] | None = None,
) -> dict[str, object]:
    return {
        "grpc_code": code,
        "language_sent": "es",
        "word_times_raw": words or [],
        "segment_times_raw": segments or [],
        "hypothesis_char_len": 4,
        "wav_num_bytes": 32,
        "sample_rate_hz": 22050,
        "expected_text": "El perro corre en el parque todas las mañanas.",
    }


def test_word_proposal_stays_pending_and_omits_secret() -> None:
    offsets = _load(_SCRIPT, "riva_offsets")
    secret = "unit-test-nvidia-key-offsets"  # noqa: S105
    wav = b"RIFF-OFFSETS-TEST-AUDIO-BYTES-NOT-A-WAV"
    words = [[0, 400], [400, 900]]
    segments = [[0, 900]]
    known = offsets.probe_row(
        "known_phrase",
        observed=_observed(words=words, segments=segments),
        duration_s=2.0,
        spoken_duration_s=None,
        seconds_scale=0.001,
    )
    silence = offsets.probe_row(
        "silence_pad",
        observed=_observed(words=words, segments=segments),
        duration_s=4.0,
        spoken_duration_s=2.0,
        seconds_scale=0.001,
    )
    report = offsets.build_offsets_report([known, silence], seconds_scale=0.001)
    payload = json.dumps(report)
    assert report["d6_proposal"] == "word"
    assert report["d6_status"] == "pending"
    assert report["span_unit"] == "seconds"
    assert report["source_time_unit"] == "milliseconds"
    assert secret not in payload
    assert wav.decode("ascii") not in payload
    assert "Bearer" not in payload


def test_segment_when_words_fail_and_none_without_scale() -> None:
    offsets = _load(_SCRIPT, "riva_offsets_segment")
    words = [[100, 200], [0, 50]]
    segments = [[0, 500]]
    known = offsets.probe_row(
        "known_phrase",
        observed=_observed(words=words, segments=segments),
        duration_s=2.0,
        spoken_duration_s=None,
        seconds_scale=0.001,
    )
    silence = offsets.probe_row(
        "silence_pad",
        observed=_observed(words=words, segments=segments),
        duration_s=4.0,
        spoken_duration_s=2.0,
        seconds_scale=0.001,
    )
    assert known["word_ok"] is False
    assert known["segment_ok"] is True
    report = offsets.build_offsets_report([known, silence], seconds_scale=0.001)
    assert report["d6_proposal"] == "segment"
    assert report["d6_status"] == "pending"

    unscaled = offsets.probe_row(
        "known_phrase",
        observed=_observed(words=[[0, 400]], segments=[[0, 400]]),
        duration_s=2.0,
        spoken_duration_s=None,
        seconds_scale=None,
    )
    assert unscaled["word_spans_s"] == []
    assert unscaled["word_count"] == 1
    assert unscaled["word_ok"] is False
    missing = offsets.build_offsets_report([unscaled], seconds_scale=None)
    assert missing["d6_proposal"] == "none"
    assert missing["seconds_scale"] == "NO VERIFICADO"


def test_timing_keeps_numbers_and_drops_word_text() -> None:
    spike = _load(_SPIKE, "riva_spike")

    class _Word:
        def __init__(self, start: int, end: int, word: str) -> None:
            self.start_time = start
            self.end_time = end
            self.word = word

    class _Alt:
        transcript = "palabra secreta"
        words = [_Word(0, 100, "palabra"), _Word(100, 250, "secreta")]

    class _Result:
        alternatives = [_Alt()]

    class _Response:
        results = [_Result()]

    class _Call:
        def result(self, timeout: float | None = None) -> _Response:
            return _Response()

    outcome = spike.finish_recognize_call(_Call(), "unit-test-nvidia-key-words")  # noqa: S106
    timing = {
        "word_times_raw": outcome["word_times_raw"],
        "segment_times_raw": outcome["segment_times_raw"],
    }
    payload = json.dumps(timing)
    assert outcome["word_times_raw"] == [[0.0, 100.0], [100.0, 250.0]]
    assert outcome["segment_times_raw"] == [[0.0, 250.0]]
    assert "palabra" not in payload
    assert "secreta" not in payload

"""P-S1-SG-07 (S1.A6): word and segment offsets. D6 stays pending.

Two serial Recognize calls, no retries. Audio stays in RAM. The printed report
has numeric spans only, never word text, the API key, or WAV bytes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_PROVIDER = Path(__file__).resolve().parent
if str(_PROVIDER) not in sys.path:
    sys.path.insert(0, str(_PROVIDER))

import riva_limits  # noqa: E402
import riva_spike  # noqa: E402

SILENCE_PAD_S = 2.0
NOT_VERIFIED = riva_spike.NOT_VERIFIED
_PROBES = ("known_phrase", "silence_pad")


def installed_asr_source() -> str:
    """Source of the installed client printer, or empty when it cannot be read."""
    try:
        import inspect

        import riva.client.asr as asr
    except ImportError:
        return ""
    try:
        return inspect.getsource(asr)
    except OSError:
        return ""


def proposal_from_rows(rows: list[dict[str, Any]]) -> str:
    """Both probes must pass. Word wins over segment. Otherwise ``none``."""
    ingest = str(riva_spike.repo_root() / "apps" / "ingest")
    if ingest not in sys.path:
        sys.path.insert(0, ingest)
    from maulwurf_ingest.audio.timestamps import propose_precision

    by_name = {str(row["probe"]): row for row in rows}
    if any(name not in by_name for name in _PROBES):
        return "none"
    word_ok = all(bool(by_name[name]["word_ok"]) for name in _PROBES)
    segment_ok = all(bool(by_name[name]["segment_ok"]) for name in _PROBES)
    return propose_precision(word_ok=word_ok, segment_ok=segment_ok)


def build_offsets_report(
    rows: list[dict[str, Any]],
    *,
    seconds_scale: float | None,
) -> dict[str, Any]:
    """D6 is a proposal. Closing it is S1.A9."""
    if seconds_scale == 0.001:
        source_unit: str | float = "milliseconds"
        scale_field: str | float = seconds_scale
        span_unit: str = "seconds"
    else:
        source_unit = NOT_VERIFIED
        scale_field = NOT_VERIFIED
        span_unit = NOT_VERIFIED
    return {
        "test_id": "P-S1-SG-07",
        "ticket": "S1.A6",
        "d6_proposal": proposal_from_rows(rows),
        "d6_status": "pending",
        "source_time_unit": source_unit,
        "seconds_scale": scale_field,
        "span_unit": span_unit,
        "rows": rows,
    }


def probe_row(
    probe: str,
    *,
    observed: dict[str, Any],
    duration_s: float,
    spoken_duration_s: float | None,
    seconds_scale: float | None,
) -> dict[str, Any]:
    """One probe. Spans are seconds only when the client scale is known."""
    ingest = str(riva_spike.repo_root() / "apps" / "ingest")
    if ingest not in sys.path:
        sys.path.insert(0, ingest)
    from maulwurf_ingest.audio.timestamps import (
        ends_before_trailing_silence,
        scale_pairs,
        spans_are_valid,
    )

    words = scale_pairs(observed["word_times_raw"], seconds_scale)
    segments = scale_pairs(observed["segment_times_raw"], seconds_scale)
    ok_call = observed["grpc_code"] == "OK"
    word_ok = ok_call and spans_are_valid(words, duration_s)
    segment_ok = ok_call and spans_are_valid(segments, duration_s)
    if spoken_duration_s is not None:
        word_ok = word_ok and ends_before_trailing_silence(
            words,
            spoken_duration_s=spoken_duration_s,
            total_duration_s=duration_s,
        )
        segment_ok = segment_ok and ends_before_trailing_silence(
            segments,
            spoken_duration_s=spoken_duration_s,
            total_duration_s=duration_s,
        )
    spoken_field: float | None
    if spoken_duration_s is None:
        spoken_field = None
    else:
        spoken_field = round(spoken_duration_s, 3)
    return {
        "probe": probe,
        "grpc_code": observed["grpc_code"],
        "language_code": observed["language_sent"],
        "duration_s": round(duration_s, 3),
        "spoken_duration_s": spoken_field,
        "word_count": len(observed["word_times_raw"]),
        "segment_count": len(observed["segment_times_raw"]),
        "word_ok": word_ok,
        "segment_ok": segment_ok,
        "word_spans_s": [[start, end] for start, end in words],
        "segment_spans_s": [[start, end] for start, end in segments],
        "hypothesis_char_len": observed["hypothesis_char_len"],
        "wav_num_bytes": observed["wav_num_bytes"],
        "sample_rate_hz": observed["sample_rate_hz"],
        "expected_text": observed["expected_text"],
    }


def run_offset_probes(api_key: str) -> tuple[dict[str, Any], list[bytes]]:
    ingest = str(riva_spike.repo_root() / "apps" / "ingest")
    if ingest not in sys.path:
        sys.path.insert(0, ingest)
    from maulwurf_ingest.audio.synthetic import generate_synthetic_utterance
    from maulwurf_ingest.audio.timestamps import word_time_scale_s

    utterance = generate_synthetic_utterance()
    short = utterance.wav_bytes
    spoken_s = riva_limits.wav_duration_s(short)
    language = str(utterance.language_code)
    expected = str(utterance.expected_text)
    rate = int(utterance.sample_rate_hz)
    scale = word_time_scale_s(installed_asr_source())
    rows: list[dict[str, Any]] = []
    wavs = [short]

    known = riva_spike.recognize_wav(
        api_key,
        wav_bytes=short,
        language_code=language,
        sample_rate_hz=rate,
        expected_text=expected,
        enable_word_time_offsets=True,
    )
    rows.append(
        probe_row(
            "known_phrase",
            observed=known,
            duration_s=spoken_s,
            spoken_duration_s=None,
            seconds_scale=scale,
        )
    )

    padded = riva_limits.pad_wav_with_silence(short, spoken_s + SILENCE_PAD_S)
    wavs.append(padded)
    total_s = riva_limits.wav_duration_s(padded)
    silence = riva_spike.recognize_wav(
        api_key,
        wav_bytes=padded,
        language_code=language,
        sample_rate_hz=rate,
        expected_text=expected,
        enable_word_time_offsets=True,
    )
    rows.append(
        probe_row(
            "silence_pad",
            observed=silence,
            duration_s=total_s,
            spoken_duration_s=spoken_s,
            seconds_scale=scale,
        )
    )
    return build_offsets_report(rows, seconds_scale=scale), wavs


def main(argv: list[str] | None = None, dotenv_path: Path | None = None) -> int:
    args = list(sys.argv if argv is None else argv)
    if len(args) > 1:
        print(
            "riva_offsets.py accepts no arguments; set NVIDIA_API_KEY in the environment",
            file=sys.stderr,
        )
        return 2
    env_path = riva_spike.repo_root() / ".env" if dotenv_path is None else dotenv_path
    riva_spike.load_dotenv(env_path)
    try:
        api_key = riva_spike.require_api_key()
    except riva_spike.SpikeConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        report, wavs = run_offset_probes(api_key)
        payload = json.dumps(report, ensure_ascii=False, indent=2)
        if any(
            not riva_spike.report_is_redacted(payload, api_key=api_key, wav_bytes=wav)
            for wav in wavs
        ):
            print("refusing to print a report that contains a secret or audio", file=sys.stderr)
            return 2
        print(payload)
    except riva_spike.SpikeConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Riva offsets probe failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

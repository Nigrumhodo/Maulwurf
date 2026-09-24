"""P-S1-SG-07 (S1.A5): three short Riva probes. 200 MiB and 3 h stay unapproved.

No retries, no parallel calls, no quota burst. Audio stays in RAM.
"""

from __future__ import annotations

import io
import json
import sys
import wave
from pathlib import Path
from typing import Any

_PROVIDER = Path(__file__).resolve().parent
if str(_PROVIDER) not in sys.path:
    sys.path.insert(0, str(_PROVIDER))

import riva_spike  # noqa: E402

CLIENT_WAIT_S = 0.05
FLOOR_SECONDS = 30.0
FLOOR_HZ = 16000
NOT_VERIFIED = riva_spike.NOT_VERIFIED


def pad_wav_with_silence(wav_bytes: bytes, target_seconds: float) -> bytes:
    """Append PCM silence in RAM until the WAV lasts target_seconds."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as reader:
        channels = reader.getnchannels()
        width = reader.getsampwidth()
        rate = reader.getframerate()
        frames = reader.readframes(reader.getnframes())
    if channels != 1 or width != 2 or rate <= 0:
        raise ValueError("expected PCM s16 mono")
    target_frames = int(target_seconds * rate)
    current_frames = len(frames) // width
    if current_frames < target_frames:
        frames += b"\x00\x00" * (target_frames - current_frames)
    out = io.BytesIO()
    with wave.open(out, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(frames)
    return out.getvalue()


def wav_duration_s(wav_bytes: bytes) -> float:
    with wave.open(io.BytesIO(wav_bytes), "rb") as reader:
        rate = reader.getframerate()
        if rate <= 0:
            return 0.0
        return reader.getnframes() / rate


def build_limits_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Numbers for later capabilities. 200 MiB and 3 h are never approved here."""
    return {
        "test_id": "P-S1-SG-07",
        "ticket": "S1.A5",
        "approved_200_mib": False,
        "approved_3h": False,
        "max_payload_bytes": NOT_VERIFIED,
        "max_duration_s": NOT_VERIFIED,
        "quota": NOT_VERIFIED,
        "concurrency": NOT_VERIFIED,
        "server_deadline": NOT_VERIFIED,
        "cited_grpc_errors": [
            {"source": "S1.A3", "case": "invalid_code", "grpc_code": "INVALID_ARGUMENT"},
            {"source": "S1.A3", "case": "absent_language", "grpc_code": "INVALID_ARGUMENT"},
        ],
        "rows": rows,
    }


def _row(
    probe: str,
    *,
    observed: dict[str, Any],
    expected_code: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    row = {
        "probe": probe,
        "grpc_code": observed["grpc_code"],
        "wav_num_bytes": observed["wav_num_bytes"],
        "sample_rate_hz": observed["sample_rate_hz"],
        "hypothesis_char_len": observed["hypothesis_char_len"],
    }
    if expected_code is not None:
        row["expected_code"] = expected_code
        row["outcome_expected"] = observed["grpc_code"] == expected_code
    row.update(extra)
    return row


def run_limit_probes(api_key: str) -> tuple[dict[str, Any], list[bytes]]:
    ingest = str(riva_spike.repo_root() / "apps" / "ingest")
    if ingest not in sys.path:
        sys.path.insert(0, ingest)
    from riva_formats import resample_wav_pcm_s16_mono

    from maulwurf_ingest.audio.synthetic import generate_synthetic_utterance

    utterance = generate_synthetic_utterance()
    short = utterance.wav_bytes
    language = str(utterance.language_code)
    expected = str(utterance.expected_text)
    rows: list[dict[str, Any]] = []
    wavs = [short]

    waited = riva_spike.recognize_wav(
        api_key,
        wav_bytes=short,
        language_code=language,
        sample_rate_hz=int(utterance.sample_rate_hz),
        expected_text=expected,
        timeout_s=CLIENT_WAIT_S,
    )
    rows.append(
        _row(
            "client_wait_timeout",
            observed=waited,
            kind="client_wait_timeout",
            expected_code="DEADLINE_EXCEEDED",
            timeout_s=CLIENT_WAIT_S,
            server_deadline=NOT_VERIFIED,
        )
    )

    cancelled = riva_spike.recognize_wav(
        api_key,
        wav_bytes=short,
        language_code=language,
        sample_rate_hz=int(utterance.sample_rate_hz),
        expected_text=expected,
        cancel_immediately=True,
    )
    rows.append(
        _row(
            "cancel_immediately",
            observed=cancelled,
            expected_code="CANCELLED",
            kind="cancellation",
        )
    )

    resampled = resample_wav_pcm_s16_mono(short, FLOOR_HZ)
    floor = pad_wav_with_silence(resampled, FLOOR_SECONDS)
    wavs.append(floor)
    duration = wav_duration_s(floor)
    floor_obs = riva_spike.recognize_wav(
        api_key,
        wav_bytes=floor,
        language_code=language,
        sample_rate_hz=FLOOR_HZ,
        expected_text=expected,
    )
    bound = "minimum_observed" if floor_obs["grpc_code"] == "OK" else NOT_VERIFIED
    rows.append(
        _row(
            "payload_duration_floor",
            observed=floor_obs,
            kind="floor",
            duration_s=round(duration, 3),
            payload_bound=bound,
            duration_bound=bound,
            max_payload_bytes=NOT_VERIFIED,
            max_duration_s=NOT_VERIFIED,
        )
    )
    return build_limits_report(rows), wavs


def main(argv: list[str] | None = None, dotenv_path: Path | None = None) -> int:
    args = list(sys.argv if argv is None else argv)
    if len(args) > 1:
        print(
            "riva_limits.py accepts no arguments; set NVIDIA_API_KEY in the environment",
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
        report, wavs = run_limit_probes(api_key)
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
        print(f"Riva limits probe failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

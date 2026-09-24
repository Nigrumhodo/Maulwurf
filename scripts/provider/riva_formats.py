"""S1.A4: container decision table plus one 16 kHz Recognize.

ffmpeg/ffprobe run only through pipes. 22050 Hz is cited from S1.A2 and is not called again.
16 kHz remains a candidate, not an approved limit.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import warnings
import wave
from pathlib import Path
from typing import Any

_PROVIDER = Path(__file__).resolve().parent
if str(_PROVIDER) not in sys.path:
    sys.path.insert(0, str(_PROVIDER))

import riva_spike  # noqa: E402

CANDIDATE_HZ = 16000
PRIOR_HZ = 22050
_FFMPEG_TIMEOUT_S = 30
_CONTAINERS: tuple[tuple[str, str], ...] = (
    ("mp3", "mp3"),
    ("m4a", "ipod"),
    ("ogg", "ogg"),
    ("opus", "opus"),
    ("flac", "flac"),
    ("webm", "webm"),
)


def resample_wav_pcm_s16_mono(wav_bytes: bytes, target_hz: int) -> bytes:
    """Resample in RAM. The result stays PCM s16 mono."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        import audioop

    with wave.open(io.BytesIO(wav_bytes), "rb") as reader:
        channels = reader.getnchannels()
        width = reader.getsampwidth()
        rate = reader.getframerate()
        frames = reader.readframes(reader.getnframes())
    if channels != 1 or width != 2:
        raise ValueError("expected PCM s16 mono")
    if rate != target_hz:
        frames, _state = audioop.ratecv(frames, width, channels, rate, target_hz, None)
    out = io.BytesIO()
    with wave.open(out, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(target_hz)
        writer.writeframes(frames)
    return out.getvalue()


def _unverified(container: str) -> dict[str, Any]:
    return {
        "container": container,
        "codec": riva_spike.NOT_VERIFIED,
        "channels": riva_spike.NOT_VERIFIED,
        "app_decision": riva_spike.NOT_VERIFIED,
        "riva_payload": "wav_pcm_s16_mono",
    }


def _run(args: list[str], payload: bytes) -> subprocess.CompletedProcess[bytes] | None:
    try:
        if sys.platform == "win32":
            return subprocess.run(  # noqa: S603 — argv list, no shell
                args,
                input=payload,
                capture_output=True,
                check=False,
                timeout=_FFMPEG_TIMEOUT_S,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        return subprocess.run(  # noqa: S603 — argv list, no shell
            args,
            input=payload,
            capture_output=True,
            check=False,
            timeout=_FFMPEG_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _ffmpeg_to(payload: bytes, muxer: str) -> bytes | None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return None
    completed = _run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            "pipe:0",
            "-ac",
            "1",
            "-f",
            muxer,
            "pipe:1",
        ],
        payload,
    )
    if completed is None or completed.returncode != 0 or not completed.stdout:
        return None
    return completed.stdout


def _ffmpeg_to_wav_pcm(payload: bytes) -> bytes | None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return None
    completed = _run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            "pipe:0",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            "-f",
            "wav",
            "pipe:1",
        ],
        payload,
    )
    if completed is None or completed.returncode != 0 or not completed.stdout:
        return None
    return completed.stdout


def _ffprobe(payload: bytes) -> dict[str, Any] | None:
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        return None
    completed = _run(
        [
            ffprobe,
            "-hide_banner",
            "-loglevel",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "pipe:0",
        ],
        payload,
    )
    if completed is None or completed.returncode != 0 or not completed.stdout:
        return None
    try:
        parsed = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    streams = parsed.get("streams") or []
    if not streams:
        return None
    stream = streams[0]
    codec = stream.get("codec_name")
    channels = stream.get("channels")
    if not isinstance(codec, str) or not isinstance(channels, int):
        return None
    return {"codec": codec, "channels": channels}


def container_row(wav_bytes: bytes, container: str, muxer: str, classify: Any) -> dict[str, Any]:
    encoded = _ffmpeg_to(wav_bytes, muxer)
    if encoded is None:
        return _unverified(container)
    probed = _ffprobe(encoded)
    if probed is None:
        return _unverified(container)
    normalized = _ffmpeg_to_wav_pcm(encoded)
    if normalized is None or classify(normalized).decision != "accept":
        return _unverified(container)
    return {
        "container": container,
        "codec": probed["codec"],
        "channels": probed["channels"],
        "app_decision": "normalize",
        "riva_payload": "wav_pcm_s16_mono",
    }


def build_format_table(wav_bytes: bytes) -> list[dict[str, Any]]:
    ingest = str(riva_spike.repo_root() / "apps" / "ingest")
    if ingest not in sys.path:
        sys.path.insert(0, ingest)
    from maulwurf_ingest.audio.validate import classify_upload

    baseline = classify_upload(wav_bytes)
    rows = [
        {
            "container": "wav",
            "codec": "pcm_s16le",
            "channels": 1,
            "app_decision": "accept" if baseline.decision == "accept" else "reject",
            "riva_payload": "wav_pcm_s16_mono",
        }
    ]
    for container, muxer in _CONTAINERS:
        rows.append(container_row(wav_bytes, container, muxer, classify_upload))
    return rows


def run_format_probe(api_key: str) -> tuple[dict[str, Any], bytes]:
    ingest = str(riva_spike.repo_root() / "apps" / "ingest")
    if ingest not in sys.path:
        sys.path.insert(0, ingest)
    from maulwurf_ingest.audio.synthetic import generate_synthetic_utterance

    utterance = generate_synthetic_utterance()
    table = build_format_table(utterance.wav_bytes)
    wav_16k = resample_wav_pcm_s16_mono(utterance.wav_bytes, CANDIDATE_HZ)
    observed = riva_spike.recognize_wav(
        api_key,
        wav_bytes=wav_16k,
        language_code=str(utterance.language_code),
        sample_rate_hz=CANDIDATE_HZ,
        expected_text=str(utterance.expected_text),
    )
    report = {
        "test_id": "U-S1-SG-02",
        "ticket": "S1.A4",
        "client": observed["client"],
        "probe_tools": {
            "ffmpeg": "present" if shutil.which("ffmpeg") else riva_spike.NOT_VERIFIED,
            "ffprobe": "present" if shutil.which("ffprobe") else riva_spike.NOT_VERIFIED,
        },
        "containers": table,
        "sample_rate": {
            "candidate_hz": CANDIDATE_HZ,
            "candidate_is_approved_limit": False,
            "hz_22050": {
                "source": "S1.A2",
                "grpc_code": "OK",
                "recalled": False,
            },
            "hz_16000": {
                "grpc_code": observed["grpc_code"],
                "hypothesis_char_len": observed["hypothesis_char_len"],
                "expected_text_in_hypothesis": observed["expected_text_in_hypothesis"],
                "wav_num_bytes": observed["wav_num_bytes"],
                "sample_rate_hz": CANDIDATE_HZ,
            },
        },
    }
    return report, wav_16k


def main(argv: list[str] | None = None, dotenv_path: Path | None = None) -> int:
    args = list(sys.argv if argv is None else argv)
    if len(args) > 1:
        print(
            "riva_formats.py accepts no arguments; set NVIDIA_API_KEY in the environment",
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
        report, wav_bytes = run_format_probe(api_key)
        payload = json.dumps(report, ensure_ascii=False, indent=2)
        if not riva_spike.report_is_redacted(payload, api_key=api_key, wav_bytes=wav_bytes):
            print("refusing to print a report that contains a secret or audio", file=sys.stderr)
            return 2
        print(payload)
    except riva_spike.SpikeConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Riva format probe failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0 if report["sample_rate"]["hz_16000"]["grpc_code"] == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())

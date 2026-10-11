"""Upload classification for S1.A4 and ffprobe decisions for S2.1.

`classify_upload` stays in memory. `probe_file` reads a file already on the attempt
tmpfs and takes duration from ffprobe, never from a declared content length.
"""

from __future__ import annotations

import io
import json
import math
import shutil
import subprocess
import sys
import wave
from dataclasses import dataclass
from pathlib import Path

_URL_PREFIXES = (b"http://", b"https://")
_PROBE_TIMEOUT_S = 30.0
_PREFIX_BYTES = 64
# Containers the spike normalized. m4a stays out: S1.A4 left it NO VERIFICADO.
ACCEPT_CONTAINERS = ("wav",)
NORMALIZE_CONTAINERS = ("mp3", "ogg", "opus", "flac", "webm")
_UNVERIFIED_FORMATS = frozenset({"mov", "mp4", "m4a", "3gp", "3g2", "mj2"})


@dataclass(frozen=True)
class FormatDecision:
    decision: str
    reason: str


@dataclass(frozen=True)
class ProbeDecision:
    """ffprobe outcome. `duration_s` is `format.duration`, or None when rejected."""

    decision: str
    reason: str
    duration_s: float | None = None


def classify_upload(data: bytes) -> FormatDecision:
    """Accept WAV PCM s16 mono. Reject URL, playlist, other containers, and multichannel."""
    sniffed = sniff_prefix(data)
    if sniffed is not None:
        return sniffed
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return FormatDecision("reject", "unexpected_container")
    try:
        with wave.open(io.BytesIO(data), "rb") as reader:
            channels = reader.getnchannels()
            width = reader.getsampwidth()
            comptype = reader.getcomptype()
    except wave.Error:
        return FormatDecision("reject", "unexpected_container")
    if channels != 1:
        return FormatDecision("reject", "multi_channel")
    if width != 2 or comptype != "NONE":
        return FormatDecision("reject", "not_pcm_s16")
    return FormatDecision("accept", "wav_pcm_s16_mono")


def _looks_like_url(data: bytes) -> bool:
    lowered = data.lower()
    return any(lowered.startswith(prefix) for prefix in _URL_PREFIXES)


def _looks_like_playlist(data: bytes) -> bool:
    head = data[:64].lstrip().lower()
    markers = (b"#extm3u", b"#extinf", b"[playlist]")
    return any(head.startswith(marker) for marker in markers)


def sniff_prefix(data: bytes) -> FormatDecision | None:
    """Reject a URL or playlist before ffprobe can try to open it."""
    stripped = data.lstrip()
    if _looks_like_url(stripped):
        return FormatDecision("reject", "url")
    if _looks_like_playlist(stripped):
        return FormatDecision("reject", "playlist")
    return None


def decide_probe(
    *,
    format_name: str,
    codec: str,
    channels: int,
    duration_s: float,
) -> ProbeDecision:
    """Map one ffprobe audio stream to accept, normalize, or reject.

    Duration is the caller's `format.duration`. A missing or non-finite value
    is malformed; this function does not invent one.
    """
    names = {part.strip() for part in format_name.split(",") if part.strip()}
    if (
        not names
        or not codec
        or channels < 1
        or not math.isfinite(duration_s)
        or duration_s <= 0
    ):
        return ProbeDecision("reject", "malformed")
    if names & _UNVERIFIED_FORMATS:
        return ProbeDecision("reject", "unverified_container")
    if "wav" in names:
        if codec != "pcm_s16le":
            return ProbeDecision("reject", "not_pcm_s16")
        if channels != 1:
            return ProbeDecision("reject", "multi_channel")
        return ProbeDecision("accept", "wav_pcm_s16_mono", duration_s)
    if "matroska" in names and "webm" not in names:
        return ProbeDecision("reject", "unexpected_container")
    normalized = _normalize_reason(names, codec)
    if normalized is None:
        return ProbeDecision("reject", "unexpected_container")
    return ProbeDecision("normalize", normalized, duration_s)


def decision_from_payload(payload: object) -> ProbeDecision:
    """Read container, codec, channels, and `format.duration` from ffprobe JSON."""
    if not isinstance(payload, dict):
        return ProbeDecision("reject", "malformed")
    format_info = payload.get("format")
    streams = payload.get("streams")
    if not isinstance(format_info, dict) or not isinstance(streams, list):
        return ProbeDecision("reject", "malformed")
    format_name = format_info.get("format_name")
    duration_s = _as_float(format_info.get("duration"))
    if not isinstance(format_name, str) or duration_s is None:
        return ProbeDecision("reject", "malformed")
    audio: dict[object, object] | None = None
    for stream in streams:
        if isinstance(stream, dict) and stream.get("codec_type") == "audio":
            audio = stream
            break
    if audio is None:
        return ProbeDecision("reject", "malformed")
    codec = audio.get("codec_name")
    channels = audio.get("channels")
    if not isinstance(codec, str) or not isinstance(channels, int) or isinstance(channels, bool):
        return ProbeDecision("reject", "malformed")
    return decide_probe(
        format_name=format_name, codec=codec, channels=channels, duration_s=duration_s
    )


def probe_file(path: Path) -> ProbeDecision:
    """Probe `path`. Playlists and URLs never reach ffprobe. stderr is discarded."""
    sniffed = sniff_prefix(_read_prefix(path))
    if sniffed is not None:
        return ProbeDecision(sniffed.decision, sniffed.reason)
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        return ProbeDecision("reject", "probe_unavailable")
    argv = [
        ffprobe,
        "-hide_banner",
        "-loglevel",
        "error",
        "-protocol_whitelist",
        "file,crypto",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        if sys.platform == "win32":
            completed = subprocess.run(  # noqa: S603 — argv list, no shell
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=_PROBE_TIMEOUT_S,
                check=False,
                shell=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            completed = subprocess.run(  # noqa: S603 — argv list, no shell
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=_PROBE_TIMEOUT_S,
                check=False,
                shell=False,
            )
    except (OSError, subprocess.TimeoutExpired):
        return ProbeDecision("reject", "malformed")
    if completed.returncode != 0 or not completed.stdout:
        return ProbeDecision("reject", "malformed")
    try:
        parsed: object = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ProbeDecision("reject", "malformed")
    return decision_from_payload(parsed)


def _normalize_reason(names: set[str], codec: str) -> str | None:
    if "webm" in names:
        return "webm"
    if codec == "mp3" or "mp3" in names:
        return "mp3"
    if codec == "opus" or "opus" in names:
        return "opus"
    if codec == "flac" or "flac" in names:
        return "flac"
    if codec in {"vorbis", "opus"} or "ogg" in names:
        return "ogg"
    return None


def _as_float(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _read_prefix(path: Path) -> bytes:
    with path.open("rb") as handle:
        return handle.read(_PREFIX_BYTES)

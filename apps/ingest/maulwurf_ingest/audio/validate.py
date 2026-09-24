"""Upload classification for S1.A4. Bytes stay in memory; nothing is written."""

from __future__ import annotations

import io
import wave
from dataclasses import dataclass

_URL_PREFIXES = (b"http://", b"https://")


@dataclass(frozen=True)
class FormatDecision:
    decision: str
    reason: str


def classify_upload(data: bytes) -> FormatDecision:
    """Accept WAV PCM s16 mono. Reject URL, playlist, other containers, and multichannel."""
    stripped = data.lstrip()
    if _looks_like_url(stripped):
        return FormatDecision("reject", "url")
    if _looks_like_playlist(stripped):
        return FormatDecision("reject", "playlist")
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

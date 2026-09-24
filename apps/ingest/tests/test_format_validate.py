"""U-S1-SG-02 — WAV PCM s16 mono is accepted; playlist, URL, and multichannel are not."""

from __future__ import annotations

import io
import wave

from maulwurf_ingest.audio.validate import classify_upload


def _pcm_wav(*, channels: int = 1, sampwidth: int = 2) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(sampwidth)
        writer.setframerate(16000)
        frame = b"\x00" * sampwidth
        writer.writeframes(frame * channels * 16)
    return out.getvalue()


def test_accepts_wav_pcm_s16_mono() -> None:
    decision = classify_upload(_pcm_wav())
    assert decision.decision == "accept"
    assert decision.reason == "wav_pcm_s16_mono"


def test_rejects_playlist_url_container_and_multichannel() -> None:
    """U-S1-SG-02: rejection table for playlist, URL, unexpected container, and multichannel."""
    cases = {
        "playlist": (b"#EXTM3U\n#EXTINF:1,tone\n", "playlist"),
        "url": (b"https://example.invalid/audio.mp3", "url"),
        "container": (b"ID3" + b"\x00" * 32, "unexpected_container"),
        "channels": (_pcm_wav(channels=2), "multi_channel"),
    }
    for name, (payload, reason) in cases.items():
        decision = classify_upload(payload)
        assert decision.decision == "reject", name
        assert decision.reason == reason, name


def test_rejects_non_s16_pcm() -> None:
    decision = classify_upload(_pcm_wav(sampwidth=1))
    assert decision.decision == "reject"
    assert decision.reason == "not_pcm_s16"

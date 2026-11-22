"""U-S1-SG-02 — WAV PCM s16 mono is accepted; playlist, URL, and multichannel are not."""

from __future__ import annotations

import io
import wave

from maulwurf_ingest.audio.validate import classify_upload, sniff_prefix


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


def test_bom_does_not_hide_a_playlist_or_url() -> None:
    """A UTF-8/UTF-16 BOM or leading space must not skip the playlist and URL sniff."""
    playlist = b"#EXTM3U\n#EXTINF:1,tone\n"
    decision = sniff_prefix(b"\xef\xbb\xbf" + playlist)
    assert decision is not None
    assert (decision.decision, decision.reason) == ("reject", "playlist")
    utf16 = b"\xff\xfe" + playlist.decode("ascii").encode("utf-16-le")
    decision = sniff_prefix(utf16)
    assert decision is not None
    assert decision.reason == "playlist"
    spaced = sniff_prefix(b" \t\xef\xbb\xbf#EXTM3U\n")
    assert spaced is not None and spaced.reason == "playlist"
    url = sniff_prefix(b"\xef\xbb\xbfhttps://example.invalid/a.mp3")
    assert url is not None and url.reason == "url"
    file_url = sniff_prefix(b"\xfe\xff" + "file:///tmp/a.wav".encode("utf-16-be"))
    assert file_url is not None and file_url.reason == "url"
    rtsp = sniff_prefix(b"rtsp://camera.invalid/stream")
    assert rtsp is not None and rtsp.reason == "url"
    classified = classify_upload(b"\xef\xbb\xbf#EXTM3U\n")
    assert classified.reason == "playlist"


def test_rejects_non_s16_pcm() -> None:
    decision = classify_upload(_pcm_wav(sampwidth=1))
    assert decision.decision == "reject"
    assert decision.reason == "not_pcm_s16"

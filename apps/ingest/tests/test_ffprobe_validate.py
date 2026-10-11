"""U-S2-SG-02: tabla de decisión de ffprobe. La duración sale del probe, no del tamaño."""

from __future__ import annotations

import io
import shutil
import wave
from pathlib import Path

import pytest

from maulwurf_ingest.audio.validate import decide_probe, probe_file

_FFPROBE = shutil.which("ffprobe")
_FFMPEG = shutil.which("ffmpeg")


def test_decision_table_covers_spike_containers() -> None:
    accept = decide_probe(format_name="wav", codec="pcm_s16le", channels=1, duration_s=1.5)
    assert accept.decision == "accept"
    assert accept.reason == "wav_pcm_s16_mono"
    assert accept.duration_s == 1.5

    for format_name, codec, reason in (
        ("mp3", "mp3", "mp3"),
        ("ogg", "vorbis", "ogg"),
        ("ogg", "opus", "opus"),
        ("flac", "flac", "flac"),
        ("matroska,webm", "opus", "webm"),
    ):
        decision = decide_probe(
            format_name=format_name, codec=codec, channels=2, duration_s=2.0
        )
        assert decision.decision == "normalize", format_name
        assert decision.reason == reason, format_name
        assert decision.duration_s == 2.0

    unverified = decide_probe(
        format_name="mov,mp4,m4a,3gp,3g2,mj2", codec="aac", channels=2, duration_s=3.0
    )
    assert unverified.decision == "reject"
    assert unverified.reason == "unverified_container"
    assert unverified.duration_s is None


def test_malformed_duration_channels_and_unknown_container_are_rejected() -> None:
    cases = (
        decide_probe(format_name="wav", codec="pcm_s16le", channels=1, duration_s=float("nan")),
        decide_probe(format_name="wav", codec="pcm_s16le", channels=0, duration_s=1.0),
        decide_probe(format_name="wav", codec="pcm_s16le", channels=2, duration_s=1.0),
        decide_probe(format_name="wav", codec="pcm_u8", channels=1, duration_s=1.0),
        decide_probe(format_name="matroska", codec="aac", channels=1, duration_s=1.0),
        decide_probe(format_name="amr", codec="amr_nb", channels=1, duration_s=1.0),
    )
    reasons = [item.reason for item in cases]
    assert all(item.decision == "reject" for item in cases)
    assert reasons[2] == "multi_channel"
    assert reasons[3] == "not_pcm_s16"
    assert "malformed" in reasons


def _pcm_wav(*, channels: int = 1, seconds: float = 0.2) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(2)
        writer.setframerate(16_000)
        frames = int(16_000 * seconds)
        writer.writeframes(b"\x00\x00" * channels * frames)
    return out.getvalue()


@pytest.mark.skipif(_FFPROBE is None, reason="ffprobe no disponible")
def test_ffprobe_duration_is_not_the_byte_length(tmp_path: Path) -> None:
    """U-S2-SG-02: duración real de ffprobe; playlist, URL y malformado se rechazan."""
    wav_path = tmp_path / "tone.wav"
    payload = _pcm_wav()
    wav_path.write_bytes(payload)
    decision = probe_file(wav_path)
    assert decision.decision == "accept"
    assert decision.duration_s == pytest.approx(0.2, abs=0.05)
    assert decision.duration_s != len(payload)

    stereo = tmp_path / "stereo.wav"
    stereo.write_bytes(_pcm_wav(channels=2))
    assert probe_file(stereo).reason == "multi_channel"

    playlist = tmp_path / "list.m3u"
    playlist.write_bytes(b"#EXTM3U\n#EXTINF:1,tone\nhttp://example.invalid/a.mp3\n")
    assert probe_file(playlist).reason == "playlist"

    url = tmp_path / "link.txt"
    url.write_bytes(b"https://example.invalid/audio.mp3")
    assert probe_file(url).reason == "url"

    broken = tmp_path / "broken.bin"
    broken.write_bytes(b"this-is-not-a-media-container")
    assert probe_file(broken).reason == "malformed"


@pytest.mark.skipif(_FFPROBE is None or _FFMPEG is None, reason="ffmpeg/ffprobe no disponible")
def test_ffprobe_normalizes_mp3_and_rejects_m4a(tmp_path: Path) -> None:
    import subprocess

    wav_path = tmp_path / "tone.wav"
    wav_path.write_bytes(_pcm_wav(seconds=0.4))
    mp3_path = tmp_path / "tone.mp3"
    assert _FFMPEG is not None
    encoded = subprocess.run(  # noqa: S603 — argv lista, sin shell
        [
            _FFMPEG,
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(wav_path),
            str(mp3_path),
        ],
        check=False,
        capture_output=True,
        timeout=30,
    )
    if encoded.returncode != 0:
        pytest.skip("ffmpeg no pudo codificar mp3")
    assert probe_file(mp3_path).decision == "normalize"

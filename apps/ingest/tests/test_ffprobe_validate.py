"""U-S2-SG-02: tabla de decisión de ffprobe. La duración sale del probe, no del tamaño."""

from __future__ import annotations

import io
import shutil
import wave
from pathlib import Path

import pytest

from maulwurf_ingest.audio.ffmpeg import DEFAULT_LIMITS
from maulwurf_ingest.audio.ffmpeg import run as run_ffmpeg
from maulwurf_ingest.audio.fragmenter import FragmentSpan, fragment_argv
from maulwurf_ingest.audio.validate import decide_probe, decision_from_payload, probe_file

_FFPROBE = shutil.which("ffprobe")
_FFMPEG = shutil.which("ffmpeg")


def test_decision_table_covers_spike_containers() -> None:
    accept = decide_probe(format_name="wav", codec="pcm_s16le", channels=1, duration_s=1.5)
    assert accept.decision == "accept"
    assert accept.reason == "wav_pcm_s16_mono"
    assert accept.duration_s == 1.5

    for format_name, codec, reason in (
        ("mp3", "mp3", "mp3"),
        ("mov,mp4,m4a,3gp,3g2,mj2", "aac", "m4a"),
        ("mov,mp4,m4a,3gp,3g2,mj2", "alac", "m4a"),
        ("aac", "aac", "m4a"),
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
        format_name="mov,mp4,m4a,3gp,3g2,mj2", codec="amr_wb", channels=1, duration_s=3.0
    )
    assert unverified.decision == "reject"
    assert unverified.reason == "unverified_container"
    assert unverified.duration_s is None


def test_wav_non_mono_or_non_s16_is_normalized() -> None:
    for codec, channels in (("pcm_s16le", 2), ("pcm_u8", 1), ("pcm_s24le", 2), ("pcm_f32le", 2)):
        decision = decide_probe(
            format_name="wav", codec=codec, channels=channels, duration_s=1.5
        )
        assert decision.decision == "normalize", (codec, channels)
        assert decision.reason == "wav", (codec, channels)
        assert decision.duration_s == 1.5


def test_decision_from_payload_falls_back_to_stream_duration() -> None:
    payload = {
        "format": {"format_name": "wav"},
        "streams": [
            {
                "codec_type": "audio",
                "codec_name": "pcm_s24le",
                "channels": 2,
                "duration": "2.5",
            }
        ],
    }
    decision = decision_from_payload(payload)
    assert decision.decision == "normalize"
    assert decision.reason == "wav"
    assert decision.duration_s == 2.5


def test_malformed_duration_channels_and_unknown_container_are_rejected() -> None:
    cases = (
        decide_probe(format_name="wav", codec="pcm_s16le", channels=1, duration_s=float("nan")),
        decide_probe(format_name="wav", codec="pcm_s16le", channels=0, duration_s=1.0),
        decide_probe(format_name="matroska", codec="aac", channels=1, duration_s=1.0),
        decide_probe(format_name="amr", codec="amr_nb", channels=1, duration_s=1.0),
    )
    reasons = [item.reason for item in cases]
    assert all(item.decision == "reject" for item in cases)
    assert "malformed" in reasons
    assert reasons[2] == "unexpected_container"
    assert reasons[3] == "unexpected_container"


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
    stereo_decision = probe_file(stereo)
    assert stereo_decision.decision == "normalize"
    assert stereo_decision.reason == "wav"
    assert stereo_decision.duration_s == pytest.approx(0.2, abs=0.05)

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
def test_ffprobe_normalizes_mp3_and_m4a(tmp_path: Path) -> None:
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

    m4a_path = tmp_path / "tone.m4a"
    encoded_m4a = subprocess.run(  # noqa: S603 — argv lista, sin shell
        [
            _FFMPEG,
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(wav_path),
            "-c:a",
            "aac",
            str(m4a_path),
        ],
        check=False,
        capture_output=True,
        timeout=30,
    )
    assert encoded_m4a.returncode == 0
    probed_m4a = probe_file(m4a_path)
    assert probed_m4a.decision == "normalize"
    assert probed_m4a.reason == "m4a"
    assert probed_m4a.duration_s is not None and probed_m4a.duration_s > 0

    out = tmp_path / "from_m4a.wav"
    run_ffmpeg(
        fragment_argv(m4a_path, out, FragmentSpan(0.0, 0.35), limits=DEFAULT_LIMITS),
        limits=DEFAULT_LIMITS,
    )
    normalized = probe_file(out)
    assert normalized.decision == "accept"
    assert normalized.reason == "wav_pcm_s16_mono"


@pytest.mark.skipif(_FFPROBE is None or _FFMPEG is None, reason="ffmpeg/ffprobe no disponible")
def test_stereo_wav_probes_normalize_and_extracts_mono_s16(tmp_path: Path) -> None:
    stereo = tmp_path / "stereo.wav"
    stereo.write_bytes(_pcm_wav(channels=2, seconds=0.4))
    probed = probe_file(stereo)
    assert probed.decision == "normalize"
    assert probed.reason == "wav"
    assert probed.duration_s == pytest.approx(0.4, abs=0.05)

    out = tmp_path / "mono.wav"
    run_ffmpeg(
        fragment_argv(stereo, out, FragmentSpan(0.0, 0.4), limits=DEFAULT_LIMITS),
        limits=DEFAULT_LIMITS,
    )
    normalized = probe_file(out)
    assert normalized.decision == "accept"
    assert normalized.reason == "wav_pcm_s16_mono"

"""S2.3: un fragmento activo, seek de entrada y reconciliación en el pipeline."""

from __future__ import annotations

import shutil
import wave
from pathlib import Path

import pytest

from maulwurf_ingest.audio.ffmpeg import DEFAULT_LIMITS
from maulwurf_ingest.audio.fragmenter import FragmentSpan, fragment_argv
from maulwurf_ingest.audio.reconcile import LocalWord
from maulwurf_ingest.pipeline import FIRST_FRAGMENT, FragmentWords, process

_RATE = 16_000
_FFMPEG = shutil.which("ffmpeg")
_FFPROBE = shutil.which("ffprobe")


def test_fragment_argv_seeks_before_the_input() -> None:
    span = FragmentSpan(12.5, 15.0)
    argv = fragment_argv(Path("in.wav"), Path("out.wav"), span, limits=DEFAULT_LIMITS)
    assert argv.index("-ss") < argv.index("-i")
    assert argv[argv.index("-ss") + 1] == "12.500000"
    assert argv[argv.index("-t") + 1] == "2.500000"
    assert "-to" not in argv


def _tone(path: Path, seconds: float) -> None:
    frames = int(_RATE * seconds)
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(_RATE)
        writer.writeframes(b"\xff\x7f" * frames)


@pytest.mark.skipif(_FFMPEG is None or _FFPROBE is None, reason="ffmpeg o ffprobe no disponible")
def test_one_fragment_is_active_and_the_class_pcm_is_not_written(tmp_path: Path) -> None:
    source = tmp_path / "original"
    _tone(source, 4.0)
    seen: list[int] = []

    def _hear(path: Path) -> FragmentWords:
        live = list(tmp_path.glob("frag-*.wav"))
        seen.append(len(live))
        assert path.name in {item.name for item in live}
        assert not (tmp_path / "converted.wav").exists()
        if path.name == FIRST_FRAGMENT:
            return FragmentWords(words=(LocalWord("hoy", 0.1, 0.4),))
        return FragmentWords(words=(LocalWord("clase", 0.2, 0.5),))

    result = process(
        source,
        tmp_path,
        fragment_seconds=1.5,
        overlap_s=0.25,
        transcribe=_hear,
        limits=DEFAULT_LIMITS,
    )
    assert seen
    assert max(seen) == 1
    assert result.fragments >= 2
    assert not list(tmp_path.glob("frag-*.wav"))
    assert not (tmp_path / "converted.wav").exists()
    assert result.reconciled.precision == "word"
    assert result.reconciled.words[0].text == "hoy"
    assert result.reconciled.words[0].start_s == pytest.approx(0.1)


@pytest.mark.skipif(_FFMPEG is None, reason="ffmpeg no disponible")
def test_extract_duration_matches_the_span(tmp_path: Path) -> None:
    from maulwurf_ingest.audio.fragmenter import wav_duration_s
    from maulwurf_ingest.pipeline import _extract

    source = tmp_path / "original"
    _tone(source, 4.0)
    dest = tmp_path / "clip.wav"
    _extract(source, dest, FragmentSpan(1.0, 2.5), limits=DEFAULT_LIMITS, realtime=False)
    assert wav_duration_s(dest) == pytest.approx(1.5, abs=0.05)
    assert dest.stat().st_size < source.stat().st_size

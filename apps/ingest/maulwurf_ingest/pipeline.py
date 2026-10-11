"""Un fragmento activo a la vez (S2.3).

No se escribe el PCM de la clase. El silencio se detecta con salida nula y cada
tramo se extrae con seek de entrada. El WAV del tramo se borra antes del
siguiente: en disco quedan el original y, como mucho, un fragmento.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from maulwurf_ingest.audio.ffmpeg import (
    DEFAULT_LIMITS,
    FfmpegLimits,
    ensure_conversion_complete,
    ensure_pcm_fits,
)
from maulwurf_ingest.audio.ffmpeg import run as run_ffmpeg
from maulwurf_ingest.audio.fragmenter import (
    FragmentSpan,
    detect_silences,
    effective_fragment_seconds,
    fragment_argv,
    plan_fragments,
    wav_duration_s,
)
from maulwurf_ingest.audio.reconcile import (
    FragmentTranscript,
    LocalWord,
    ReconciledTranscript,
    reconcile,
)
from maulwurf_ingest.audio.validate import probe_file

FIRST_FRAGMENT = "frag-0000.wav"


@dataclass(frozen=True)
class FragmentWords:
    text: str = ""
    words: tuple[LocalWord, ...] = ()


@dataclass(frozen=True)
class PipelineResult:
    fragments: int
    reconciled: ReconciledTranscript


Transcribe = Callable[[Path], FragmentWords]


def process(
    source: Path,
    workdir: Path,
    *,
    fragment_seconds: float,
    overlap_s: float,
    transcribe: Transcribe,
    limits: FfmpegLimits = DEFAULT_LIMITS,
    realtime: bool = False,
) -> PipelineResult:
    """Planifica, extrae y reconcilia. `transcribe` ve un solo `frag-*.wav`."""
    duration_s = probe_file(source).duration_s
    if duration_s is None:
        raise RuntimeError("normalization_duration_unknown")
    cap = effective_fragment_seconds(fragment_seconds)
    spans = plan_fragments(
        duration_s,
        detect_silences(source, limits=limits),
        max_fragment_s=cap,
        overlap_s=overlap_s,
    )
    if not spans:
        raise RuntimeError("no_fragments")
    transcripts: list[FragmentTranscript] = []
    active: Path | None = None
    try:
        for index, span in enumerate(spans):
            dest = workdir / f"frag-{index:04d}.wav"
            _extract(source, dest, span, limits=limits, realtime=realtime)
            active = dest
            heard = transcribe(dest)
            transcripts.append(
                FragmentTranscript(span.start_s, span.end_s, heard.text, heard.words)
            )
            dest.unlink(missing_ok=True)
            active = None
    finally:
        if active is not None:
            active.unlink(missing_ok=True)
    return PipelineResult(len(spans), reconcile(transcripts))


def _extract(
    source: Path,
    dest: Path,
    span: FragmentSpan,
    *,
    limits: FfmpegLimits,
    realtime: bool,
) -> None:
    duration_s = span.end_s - span.start_s
    ensure_pcm_fits(duration_s, limits.output_bytes)
    try:
        run_ffmpeg(
            fragment_argv(source, dest, span, limits=limits, realtime=realtime),
            limits=limits,
        )
        ensure_conversion_complete(duration_s, wav_duration_s(dest))
    except Exception:
        dest.unlink(missing_ok=True)
        raise

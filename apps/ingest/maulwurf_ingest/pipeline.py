"""Un fragmento activo a la vez (S2.3).

No se escribe el PCM de la clase. El silencio se detecta con salida nula y cada
tramo se extrae con seek de entrada. El WAV del tramo se borra antes del
siguiente: en disco quedan el original y, como mucho, un fragmento.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from maulwurf_ingest.audio.ffmpeg import (
    DEFAULT_LIMITS,
    FfmpegFailed,
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
    resilient_fragment_argv,
    wav_duration_s,
)
from maulwurf_ingest.audio.reconcile import (
    FragmentTranscript,
    ReconciledTranscript,
    Word,
    reconcile,
)
from maulwurf_ingest.audio.validate import probe_file

FIRST_FRAGMENT = "frag-0000.wav"


@dataclass(frozen=True)
class FragmentWords:
    text: str = ""
    words: tuple[Word, ...] = ()


@dataclass(frozen=True)
class PipelineResult:
    fragments: int
    reconciled: ReconciledTranscript


Transcribe = Callable[[Path], FragmentWords]
ProgressCallback = Callable[[int, int], None]
StopCheck = Callable[[], bool]

# Margin below output_bytes proving `-fs` did not truncate the final fragment.
_TAIL_FS_MARGIN_BYTES = 65_536
# Maximum VBR/M4A container duration overshoot tolerated on the final fragment at EOF.
_TAIL_EOF_TOLERANCE_S = 5.0
_TAIL_MIN_AUDIO_S = 0.05


def process(
    source: Path,
    workdir: Path,
    *,
    fragment_seconds: float,
    overlap_s: float,
    transcribe: Transcribe,
    limits: FfmpegLimits = DEFAULT_LIMITS,
    realtime: bool = False,
    on_progress: ProgressCallback | None = None,
    should_stop: StopCheck | None = None,
) -> PipelineResult:
    """Planifica, extrae y reconcilia. `transcribe` ve un solo `frag-*.wav`.

    ``should_stop`` reports a cancelled job; once it is true no ffmpeg fallback runs.
    """
    duration_s = probe_file(source).duration_s
    if duration_s is None:
        raise RuntimeError("normalization_duration_unknown")
    cap = effective_fragment_seconds(fragment_seconds)
    try:
        silences = detect_silences(source, limits=limits)
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        if _fallback_forbidden(exc, should_stop):
            raise
        # Silence detection is only a cut-boundary hint; fall back to fixed spans instead of
        # failing an entire class upload on a long/unusual container.
        silences = []
    spans = plan_fragments(
        duration_s,
        silences,
        max_fragment_s=cap,
        overlap_s=overlap_s,
    )
    if not spans:
        raise RuntimeError("no_fragments")
    total = len(spans)
    if on_progress is not None:
        on_progress(0, total)
    transcripts: list[FragmentTranscript] = []
    processed_count = 0
    active: Path | None = None
    try:
        for index, span in enumerate(spans):
            is_last = index == total - 1 and total > 1
            dest = workdir / f"frag-{index:04d}.wav"
            extracted = _extract(
                source, dest, span, limits=limits, realtime=realtime, is_last=is_last,
                should_stop=should_stop,
            )
            if not extracted:
                if on_progress is not None:
                    on_progress(index + 1, total)
                continue
            active = dest
            heard = transcribe(dest)
            processed_count += 1
            transcripts.append(
                FragmentTranscript(span.start_s, span.end_s, heard.text, heard.words)
            )
            dest.unlink(missing_ok=True)
            active = None
            if on_progress is not None:
                on_progress(index + 1, total)
    finally:
        if active is not None:
            active.unlink(missing_ok=True)
    return PipelineResult(processed_count, reconcile(transcripts))


def _extract(
    source: Path,
    dest: Path,
    span: FragmentSpan,
    *,
    limits: FfmpegLimits,
    realtime: bool,
    is_last: bool = False,
    should_stop: StopCheck | None = None,
) -> bool:
    duration_s = span.end_s - span.start_s
    ensure_pcm_fits(duration_s, limits.output_bytes)
    try:
        try:
            run_ffmpeg(
                fragment_argv(source, dest, span, limits=limits, realtime=realtime),
                limits=limits,
            )
        except FfmpegFailed as exc:
            if _fallback_forbidden(exc, should_stop):
                raise
            dest.unlink(missing_ok=True)
            run_ffmpeg(
                resilient_fragment_argv(source, dest, span, limits=limits, realtime=realtime),
                limits=limits,
            )
        actual_s = wav_duration_s(dest)
        if is_last:
            if actual_s <= _TAIL_MIN_AUDIO_S:
                dest.unlink(missing_ok=True)
                return False
            not_fs_cut = dest.stat().st_size < max(0, limits.output_bytes - _TAIL_FS_MARGIN_BYTES)
            if not_fs_cut and actual_s + _TAIL_EOF_TOLERANCE_S >= duration_s:
                return True
        ensure_conversion_complete(duration_s, actual_s)
        return True
    except Exception:
        dest.unlink(missing_ok=True)
        raise


def _fallback_forbidden(exc: BaseException, should_stop: StopCheck | None) -> bool:
    """A group kill (lease lost, user cancel) must not be retried as a bad container.

    The SIGTERM can reach ffmpeg before the job's own handler marks it cancelled, so
    the signalled exit code is checked as well as ``should_stop``.
    """
    if isinstance(exc, FfmpegFailed) and exc.signalled:
        return True
    return should_stop is not None and should_stop()

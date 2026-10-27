"""Fragmentación por silencios con solape (S2.2).

El techo es la duración que Riva aceptó en el spike (30.0 s, S1.A5). No es un
máximo de servidor aprobado y no se sustituye por 10 minutos. Un pedido mayor
queda en 30 s. El solape es local y configurable.
"""

from __future__ import annotations

import math
import re
import wave
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from maulwurf_ingest.audio.ffmpeg import (
    CANDIDATE_SAMPLE_RATE_HZ,
    FfmpegLimits,
    bounded_argv,
    run,
)

# Piso observado con gRPC OK (informe §3.3). No es el máximo del endpoint.
OBSERVED_WORKING_DURATION_S = 30.0
_PROGRESS_S = 1e-3

_SILENCE_START = re.compile(rb"silence_start:\s*([0-9]+(?:\.[0-9]+)?)")
_SILENCE_END = re.compile(rb"silence_end:\s*([0-9]+(?:\.[0-9]+)?)")


@dataclass(frozen=True)
class FragmentSpan:
    start_s: float
    end_s: float


def effective_fragment_seconds(requested_s: float) -> float:
    """`min(pedido, 30)`. 600 s (10 min) no pasa."""
    if not math.isfinite(requested_s) or requested_s <= 0:
        raise ValueError("fragment seconds must be a positive finite number")
    return min(float(requested_s), OBSERVED_WORKING_DURATION_S)


def plan_fragments(
    duration_s: float,
    silences: Sequence[tuple[float, float]],
    *,
    max_fragment_s: float,
    overlap_s: float,
) -> list[FragmentSpan]:
    """Corta cerca de un silencio y hace que el siguiente fragmento solape el corte."""
    if not math.isfinite(duration_s) or duration_s <= 0:
        return []
    cap = effective_fragment_seconds(max_fragment_s)
    overlap = _overlap(overlap_s, cap)
    spans: list[FragmentSpan] = []
    start = 0.0
    while start < duration_s - _PROGRESS_S:
        window_end = min(duration_s, start + cap)
        if window_end >= duration_s - _PROGRESS_S:
            end = duration_s
        else:
            cut = _cut_in_silence(silences, start, window_end)
            end = window_end if cut is None else cut
        if end <= start + _PROGRESS_S:
            end = min(duration_s, start + cap)
        spans.append(FragmentSpan(start, end))
        if end >= duration_s - _PROGRESS_S:
            break
        next_start = end - overlap
        start = end if next_start <= start else next_start
    return spans


def detect_silences(path: Path, *, limits: FfmpegLimits) -> list[tuple[float, float]]:
    """Lee silencedetect. El stderr se parsea en memoria y no se registra."""
    argv = bounded_argv(
        ["-i", str(path), "-af", "silencedetect=noise=-40dB:d=0.3", "-f", "null"],
        output="-",
        limits=limits,
        loglevel="info",
    )
    stderr = run(argv, limits=limits, capture_stderr=True)
    starts = [float(match.group(1)) for match in _SILENCE_START.finditer(stderr)]
    ends = [float(match.group(1)) for match in _SILENCE_END.finditer(stderr)]
    pairs: list[tuple[float, float]] = []
    for index, silence_start in enumerate(starts):
        if index >= len(ends):
            break
        silence_end = ends[index]
        if silence_end > silence_start:
            pairs.append((silence_start, silence_end))
    return pairs


def fragment_argv(
    source: Path,
    dest: Path,
    span: FragmentSpan,
    *,
    limits: FfmpegLimits,
    realtime: bool = False,
) -> list[str]:
    """Seek de entrada y duración del tramo. `-ss` va antes de `-i`; no se usa `-to`."""
    duration_s = span.end_s - span.start_s
    operation: list[str] = ["-re"] if realtime else []
    operation.extend(
        [
            "-ss",
            f"{span.start_s:.6f}",
            "-i",
            str(source),
            "-t",
            f"{duration_s:.6f}",
            "-ac",
            "1",
            "-ar",
            str(CANDIDATE_SAMPLE_RATE_HZ),
            "-c:a",
            "pcm_s16le",
            "-f",
            "wav",
        ]
    )
    return bounded_argv(operation, output=str(dest), limits=limits)


def wav_duration_s(path: Path) -> float:
    with wave.open(str(path), "rb") as reader:
        rate = reader.getframerate()
        if rate <= 0:
            raise ValueError("wav frame rate must be positive")
        return reader.getnframes() / rate


def _overlap(overlap_s: float, cap_s: float) -> float:
    if not math.isfinite(overlap_s) or overlap_s < 0:
        raise ValueError("overlap seconds must be a non-negative finite number")
    if overlap_s >= cap_s:
        return cap_s / 2
    return overlap_s


def _cut_in_silence(
    silences: Sequence[tuple[float, float]], start: float, window_end: float
) -> float | None:
    """Punto medio del silencio más tardío que cabe en la ventana."""
    best: float | None = None
    for raw_start, raw_end in silences:
        if raw_end <= raw_start:
            continue
        left = max(raw_start, start)
        right = min(raw_end, window_end)
        if right - left < _PROGRESS_S:
            continue
        midpoint = (left + right) / 2
        if start + _PROGRESS_S < midpoint < window_end and (best is None or midpoint > best):
            best = midpoint
    return best

"""Trabajo ASR de un intento, en su propio proceso (S1.B3, fragmentación S2.2).

Se ejecuta como `python -m maulwurf_ingest.asr_job <workdir> [--realtime]`, en un grupo de
procesos propio que el supervisor puede matar entero. Sobre el tmpfs del intento:

1. Si el PCM a 16 kHz no cabe en el techo de salida, falla. `-fs` trunca con
   código 0, así que un WAV más corto que el original también falla. Si cabe,
   `ffmpeg` acotado normaliza `original` a PCM s16 mono 16 kHz (`converted.wav`).
2. Parte ese WAV en `frag-NNNN.wav`. El pedido `--fragment-seconds` sigue vigente
   (1 s en el stub de S1) y nunca supera los 30 s observados en el spike.
3. Un stub de ASR lee cada fragmento como lo haría el cliente Riva. No hay llamada al
   proveedor, así que no produce texto.

Por stdout solo sale un JSON con conteos. `--realtime` hace que la normalización lea a
velocidad real (`-re`): las pruebas lo usan para matar el trabajo con ficheros abiertos.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import wave
from pathlib import Path

from maulwurf_ingest.audio.ffmpeg import (
    CANDIDATE_SAMPLE_RATE_HZ,
    DEFAULT_LIMITS,
    FfmpegLimits,
    bounded_argv,
)
from maulwurf_ingest.audio.ffmpeg import run as run_ffmpeg
from maulwurf_ingest.audio.fragmenter import (
    detect_silences,
    effective_fragment_seconds,
    plan_fragments,
    wav_duration_s,
    write_fragments,
)
from maulwurf_ingest.audio.validate import probe_file
from maulwurf_ingest.config import settings

FRAGMENT_SECONDS = 1
ORIGINAL = "original"
CONVERTED = "converted.wav"
# s16 mono al sample rate candidato. El margen cubre la cabecera RIFF.
_PCM_BYTES_PER_SECOND = CANDIDATE_SAMPLE_RATE_HZ * 2
_HEADER_BYTES = 4096


def ensure_pcm_fits(duration_s: float, output_bytes: int) -> None:
    """`-fs` trunca con código 0. Si el PCM no cabe, hay que fallar antes de convertir."""
    if not math.isfinite(duration_s) or duration_s <= 0 or output_bytes < 1:
        raise RuntimeError("normalization_duration_unknown")
    expected = math.ceil(duration_s * _PCM_BYTES_PER_SECOND) + _HEADER_BYTES
    if expected > output_bytes:
        raise RuntimeError("normalization_output_overflow")


def ensure_conversion_complete(source_s: float, converted_s: float) -> None:
    """El WAV convertido no puede quedar corto respecto de la duración de ffprobe.

    Un segundo, o el 1 % si la clase es más larga, absorbe el error de un VBR
    sin cabecera. Un corte de `-fs` es mucho mayor y no pasa.
    """
    if not math.isfinite(source_s) or not math.isfinite(converted_s) or source_s <= 0:
        raise RuntimeError("normalization_duration_unknown")
    if converted_s + max(1.0, 0.01 * source_s) < source_s:
        raise RuntimeError("normalization_truncated")


def run(
    workdir: Path,
    *,
    realtime: bool,
    fragment_seconds: int = FRAGMENT_SECONDS,
    limits: FfmpegLimits = DEFAULT_LIMITS,
) -> dict[str, float | int]:
    original = workdir / ORIGINAL
    converted = workdir / CONVERTED
    source_s = _source_duration_s(original)
    ensure_pcm_fits(source_s, limits.output_bytes)
    operation = [
        "-i",
        str(original),
        "-ac",
        "1",
        "-ar",
        str(CANDIDATE_SAMPLE_RATE_HZ),
        "-c:a",
        "pcm_s16le",
        "-f",
        "wav",
    ]
    if realtime:
        operation = ["-re", *operation]
    run_ffmpeg(
        bounded_argv(operation, output=str(converted), limits=limits),
        limits=limits,
    )
    converted_s = wav_duration_s(converted)
    ensure_conversion_complete(source_s, converted_s)
    cap = effective_fragment_seconds(float(fragment_seconds))
    spans = plan_fragments(
        converted_s,
        detect_silences(converted, limits=limits),
        max_fragment_s=cap,
        overlap_s=settings.fragment_overlap_s,
    )
    write_fragments(converted, workdir, spans, limits=limits)

    fragments = sorted(workdir.glob("frag-*.wav"))
    seconds = 0.0
    for fragment in fragments:
        with wave.open(str(fragment), "rb") as reader:  # stub: lectura como el cliente ASR
            # getnframes/getframerate: no asume mono (solo -ac 1 aguas arriba lo garantizaba).
            seconds += reader.getnframes() / reader.getframerate()
    return {"fragments": len(fragments), "seconds": round(seconds, 3)}


def _source_duration_s(original: Path) -> float:
    """Duración de ffprobe. El supervisor ya rechazó lo malformado; aquí no se inventa."""
    duration_s = probe_file(original).duration_s
    if duration_s is None:
        raise RuntimeError("normalization_duration_unknown")
    return duration_s


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("workdir", type=Path)
    parser.add_argument("--realtime", action="store_true")
    parser.add_argument("--fragment-seconds", type=int, default=FRAGMENT_SECONDS)
    args = parser.parse_args(argv)
    try:
        result = run(args.workdir, realtime=args.realtime, fragment_seconds=args.fragment_seconds)
    except (RuntimeError, OSError, wave.Error, subprocess.TimeoutExpired) as exc:
        # Solo un código: nunca rutas ni contenido.
        print(json.dumps({"error": str(exc) if isinstance(exc, RuntimeError) else
                          type(exc).__name__}), file=sys.stderr)
        return 2
    if not result["fragments"]:
        print(json.dumps({"error": "no_fragments"}), file=sys.stderr)
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

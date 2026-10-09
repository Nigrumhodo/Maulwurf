"""Trabajo ASR de un intento, en su propio proceso (S1.B3, fragmentación S2.2).

Se ejecuta como `python -m maulwurf_ingest.asr_job <workdir> [--realtime]`, en un grupo de
procesos propio que el supervisor puede matar entero. Sobre el tmpfs del intento:

1. `ffmpeg` acotado normaliza `original` a PCM s16 mono 16 kHz (`converted.wav`).
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
import subprocess
import sys
import wave
from pathlib import Path

from maulwurf_ingest.audio.ffmpeg import (
    CANDIDATE_SAMPLE_RATE_HZ,
    DEFAULT_LIMITS,
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
from maulwurf_ingest.config import settings

FRAGMENT_SECONDS = 1
ORIGINAL = "original"
CONVERTED = "converted.wav"


def run(
    workdir: Path, *, realtime: bool, fragment_seconds: int = FRAGMENT_SECONDS
) -> dict[str, float | int]:
    original = workdir / ORIGINAL
    converted = workdir / CONVERTED
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
        bounded_argv(operation, output=str(converted), limits=DEFAULT_LIMITS),
        limits=DEFAULT_LIMITS,
    )
    cap = effective_fragment_seconds(float(fragment_seconds))
    spans = plan_fragments(
        wav_duration_s(converted),
        detect_silences(converted, limits=DEFAULT_LIMITS),
        max_fragment_s=cap,
        overlap_s=settings.fragment_overlap_s,
    )
    write_fragments(converted, workdir, spans, limits=DEFAULT_LIMITS)

    fragments = sorted(workdir.glob("frag-*.wav"))
    seconds = 0.0
    for fragment in fragments:
        with wave.open(str(fragment), "rb") as reader:  # stub: lectura como el cliente ASR
            # getnframes/getframerate: no asume mono (solo -ac 1 aguas arriba lo garantizaba).
            seconds += reader.getnframes() / reader.getframerate()
    return {"fragments": len(fragments), "seconds": round(seconds, 3)}


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

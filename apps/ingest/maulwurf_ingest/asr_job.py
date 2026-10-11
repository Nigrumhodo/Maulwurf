"""Trabajo ASR de un intento, en su propio proceso (S1.B3, pipeline S2.3).

Se ejecuta como `python -m maulwurf_ingest.asr_job <workdir> [--realtime]`, en un grupo de
procesos propio que el supervisor puede matar entero. Sobre el tmpfs del intento extrae un
fragmento a la vez desde `original`. No escribe el PCM de la clase (`converted.wav`).

El pedido `--fragment-seconds` nunca supera los 30 s observados en el spike. Si no se
pasa, sale de `INGEST_FRAGMENT_SECONDS`. Un stub lee cada fragmento como lo haría el
cliente Riva. No hay llamada al proveedor, así que no produce texto.

Por stdout solo sale un JSON con conteos. `--realtime` hace que cada extracto lea a
velocidad real (`-re`): las pruebas lo usan para matar el trabajo con ficheros abiertos.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import wave
from pathlib import Path

from maulwurf_ingest.audio.ffmpeg import DEFAULT_LIMITS, FfmpegLimits
from maulwurf_ingest.config import settings
from maulwurf_ingest.pipeline import FIRST_FRAGMENT, FragmentWords, process

ORIGINAL = "original"
# El primer extracto. Las pruebas de integración esperan este fichero, no el PCM entero.
ACTIVE_FRAGMENT = FIRST_FRAGMENT


def run(
    workdir: Path,
    *,
    realtime: bool,
    fragment_seconds: int | None = None,
    limits: FfmpegLimits = DEFAULT_LIMITS,
) -> dict[str, float | int]:
    requested = settings.fragment_seconds if fragment_seconds is None else fragment_seconds
    seconds = 0.0

    def _count(path: Path) -> FragmentWords:
        nonlocal seconds
        with wave.open(str(path), "rb") as reader:
            rate = reader.getframerate()
            if rate <= 0:
                raise RuntimeError("wav_frame_rate")
            seconds += reader.getnframes() / rate
        return FragmentWords()

    outcome = process(
        workdir / ORIGINAL,
        workdir,
        fragment_seconds=float(requested),
        overlap_s=settings.fragment_overlap_s,
        transcribe=_count,
        limits=limits,
        realtime=realtime,
    )
    return {"fragments": outcome.fragments, "seconds": round(seconds, 3)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("workdir", type=Path)
    parser.add_argument("--realtime", action="store_true")
    parser.add_argument("--fragment-seconds", type=int, default=None)
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

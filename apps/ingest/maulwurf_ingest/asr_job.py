"""Trabajo ASR de un intento, en su propio proceso (S1.B3).

Se ejecuta como `python -m maulwurf_ingest.asr_job <workdir> [--realtime]`, en un grupo de
procesos propio que el supervisor puede matar entero. Hace el trabajo real de S1 sobre el
tmpfs:

1. `ffmpeg` convierte `original` a PCM s16 mono 16 kHz (`converted.wav`).
2. `ffmpeg` lo parte en fragmentos (`frag-NNNN.wav`) de `--fragment-seconds`
   (1 s por defecto: stub de S1, a afinar con límites reales antes de S2).
3. Un stub de ASR lee cada fragmento como lo haría el cliente Riva. No hay llamada al
   proveedor en S1 (llega en S2), así que no produce texto.

Por stdout solo sale un JSON con conteos. `--realtime` hace que ffmpeg lea a velocidad
real (`-re`): las pruebas lo usan para matar el trabajo mientras hay ficheros abiertos.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import wave
from pathlib import Path

FRAGMENT_SECONDS = 1
FFMPEG_TIMEOUT_S = 600
ORIGINAL = "original"
CONVERTED = "converted.wav"


def _ffmpeg(*args: str) -> None:
    argv = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *args]
    # stderr descartado: los mensajes de ffmpeg pueden incluir metadatos del audio.
    completed = subprocess.run(  # noqa: S603 — argv fija, sin shell
        argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=FFMPEG_TIMEOUT_S, check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("ffmpeg_failed")


def run(
    workdir: Path, *, realtime: bool, fragment_seconds: int = FRAGMENT_SECONDS
) -> dict[str, float | int]:
    original = workdir / ORIGINAL
    converted = workdir / CONVERTED
    rate = ["-re"] if realtime else []
    _ffmpeg(*rate, "-i", str(original), "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
            str(converted))
    _ffmpeg("-i", str(converted), "-f", "segment", "-segment_time", str(fragment_seconds),
            "-c", "copy", str(workdir / "frag-%04d.wav"))

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

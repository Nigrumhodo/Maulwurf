"""Trabajo ASR de un intento, en su propio proceso (S1.B3, pipeline S2.3).

Se ejecuta como `python -m maulwurf_ingest.asr_job <workdir> [--realtime]`, en un grupo de
procesos propio que el supervisor puede matar entero. Sobre el tmpfs del intento extrae un
fragmento a la vez desde `original`. No escribe el PCM de la clase (`converted.wav`).

El pedido `--fragment-seconds` nunca supera los 30 s observados en el spike. Si no se
pasa, sale de `INGEST_FRAGMENT_SECONDS`. Un stub lee cada fragmento como lo haría el
cliente Riva. No hay llamada al proveedor, así que no produce texto.

Por stdout solo sale un JSON con conteos. `--realtime` hace que cada extracto lea a
velocidad real (`-re`): las pruebas lo usan para matar el trabajo con ficheros abiertos.

Stdout also carries the reconciled transcript. Without ``--read-stub-transcript``
that transcript is empty. The flag publishes the attempt-directory fixture; the
supervisor passes it only for the test double, and a planted file without the
flag is ignored.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import wave
from pathlib import Path
from typing import TypedDict

from maulwurf_ingest.audio.ffmpeg import DEFAULT_LIMITS, FfmpegLimits
from maulwurf_ingest.audio.reconcile import ReconciledTranscript
from maulwurf_ingest.config import settings
from maulwurf_ingest.pipeline import FIRST_FRAGMENT, FragmentWords, process

ORIGINAL = "original"
# El primer extracto. Las pruebas de integración esperan este fichero, no el PCM entero.
ACTIVE_FRAGMENT = FIRST_FRAGMENT
# Leído solo con --read-stub-transcript. El nombre no es audio.
STUB_TRANSCRIPT_NAME = "stub_transcript.json"


class JobWord(TypedDict):
    text: str
    start_s: float
    end_s: float


class JobTranscript(TypedDict):
    text: str
    precision: str
    words: list[JobWord]
    model: str | None
    client_version: str | None


class JobResult(TypedDict):
    fragments: int
    seconds: float
    transcript: JobTranscript


def run(
    workdir: Path,
    *,
    realtime: bool,
    fragment_seconds: int | None = None,
    limits: FfmpegLimits = DEFAULT_LIMITS,
    read_stub: bool = False,
) -> JobResult:
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
    transcript = _read_stub(workdir) if read_stub else _from_reconciled(outcome.reconciled)
    return {
        "fragments": outcome.fragments,
        "seconds": round(seconds, 3),
        "transcript": transcript,
    }


def _from_reconciled(reconciled: ReconciledTranscript) -> JobTranscript:
    return {
        "text": reconciled.text,
        "precision": reconciled.precision,
        "words": [
            {"text": word.text, "start_s": word.start_s, "end_s": word.end_s}
            for word in reconciled.words
        ],
        "model": None,
        "client_version": None,
    }


def _read_stub(workdir: Path) -> JobTranscript:
    """Publish the fixture as-is. Feeding it through every fragment would duplicate the text."""
    try:
        loaded: object = json.loads((workdir / STUB_TRANSCRIPT_NAME).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise RuntimeError("stub_transcript") from None
    if not isinstance(loaded, dict):
        raise RuntimeError("stub_transcript")
    text = loaded.get("text", "")
    precision = loaded.get("precision", "none")
    model = loaded.get("model")
    client = loaded.get("client_version")
    words_raw = loaded.get("words", [])
    if not isinstance(text, str) or precision not in {"word", "segment", "none"}:
        raise RuntimeError("stub_transcript")
    if model is not None and not isinstance(model, str):
        raise RuntimeError("stub_transcript")
    if client is not None and not isinstance(client, str):
        raise RuntimeError("stub_transcript")
    if not isinstance(words_raw, list):
        raise RuntimeError("stub_transcript")
    words: list[JobWord] = []
    for item in words_raw:
        if not isinstance(item, dict):
            raise RuntimeError("stub_transcript")
        word_text = item.get("text")
        start = item.get("start_s")
        end = item.get("end_s")
        if not isinstance(word_text, str) or isinstance(start, bool) or isinstance(end, bool):
            raise RuntimeError("stub_transcript")
        if not isinstance(start, int | float) or not isinstance(end, int | float):
            raise RuntimeError("stub_transcript")
        words.append({"text": word_text, "start_s": float(start), "end_s": float(end)})
    return {
        "text": text,
        "precision": precision,
        "words": words,
        "model": model if isinstance(model, str) else None,
        "client_version": client if isinstance(client, str) else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("workdir", type=Path)
    parser.add_argument("--realtime", action="store_true")
    parser.add_argument("--fragment-seconds", type=int, default=None)
    parser.add_argument("--read-stub-transcript", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run(
            args.workdir,
            realtime=args.realtime,
            fragment_seconds=args.fragment_seconds,
            read_stub=args.read_stub_transcript,
        )
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

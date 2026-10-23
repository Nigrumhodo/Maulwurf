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
import asyncio
import json
import signal
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path
from typing import TypedDict

from maulwurf_ingest.asr.riva_client import (
    AsrRejected,
    AsrReupload,
    NvidiaRivaTranscriptionService,
    RivaTransport,
    riva_transport_from_env,
)
from maulwurf_ingest.asr.scripted import load_scripted
from maulwurf_ingest.audio.ffmpeg import DEFAULT_LIMITS, FfmpegLimits
from maulwurf_ingest.audio.reconcile import ReconciledTranscript
from maulwurf_ingest.config import settings
from maulwurf_ingest.pipeline import FIRST_FRAGMENT, FragmentWords, process

ORIGINAL = "original"
# El primer extracto. Las pruebas de integración esperan este fichero, no el PCM entero.
ACTIVE_FRAGMENT = FIRST_FRAGMENT
# Leído solo con --read-stub-transcript. El nombre no es audio.
STUB_TRANSCRIPT_NAME = "stub_transcript.json"
# Leído solo con --scripted-transport. El nombre no es audio.
SCRIPTED_NAME = "scripted_transport.json"
MODEL_NAME = "whisper-large-v3"
CLIENT_VERSION = "2.27.0"
_STDERR_CODES = frozenset({
    "rpc_cancelled", "asr_rejected", "requires_reupload", "asr_failed",
})


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


class _JobError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code if code in _STDERR_CODES else "requires_reupload"


def run(
    workdir: Path,
    *,
    realtime: bool,
    fragment_seconds: int | None = None,
    limits: FfmpegLimits = DEFAULT_LIMITS,
    read_stub: bool = False,
    language: str | None = None,
    deadline_s: float | None = None,
    scripted: bool = False,
    transport: RivaTransport | None = None,
) -> JobResult:
    """Transcribe ``original``. The stub path does not construct a Riva client.

    ``transport`` is for in-process tests. The CLI never passes one.
    """
    requested = settings.fragment_seconds if fragment_seconds is None else fragment_seconds
    if read_stub:
        return _run_stub(
            workdir, realtime=realtime, fragment_seconds=requested, limits=limits,
        )
    if language is None or not language.strip() or deadline_s is None or deadline_s <= 0:
        raise _JobError("requires_reupload")
    return asyncio.run(_run_service(
        workdir, realtime=realtime, fragment_seconds=requested, limits=limits,
        language=language.strip(), deadline_s=deadline_s, scripted=scripted, transport=transport,
    ))


def _run_stub(
    workdir: Path, *, realtime: bool, fragment_seconds: int, limits: FfmpegLimits,
) -> JobResult:
    seconds = 0.0

    def _count(path: Path) -> FragmentWords:
        nonlocal seconds
        seconds += _wav_seconds(path)
        return FragmentWords()

    outcome = process(
        workdir / ORIGINAL, workdir, fragment_seconds=float(fragment_seconds),
        overlap_s=settings.fragment_overlap_s, transcribe=_count, limits=limits, realtime=realtime,
    )
    return {
        "fragments": outcome.fragments,
        "seconds": round(seconds, 3),
        "transcript": _read_stub(workdir),
    }


async def _run_service(
    workdir: Path,
    *,
    realtime: bool,
    fragment_seconds: int,
    limits: FfmpegLimits,
    language: str,
    deadline_s: float,
    scripted: bool,
    transport: RivaTransport | None,
) -> JobResult:
    """One event loop and one client. ``process`` runs off the loop so ``transcribe_async`` can."""
    deadline_at = time.monotonic() + deadline_s
    loop = asyncio.get_running_loop()
    service_box: dict[str, NvidiaRivaTranscriptionService] = {}
    cancelled = threading.Event()
    seconds = 0.0

    def _on_term(_signum: int, _frame: object) -> None:
        # The default action would die before the in-flight RPC sees cancel_nowait.
        cancelled.set()
        service = service_box.get("service")
        if service is not None:
            service.cancel_nowait()

    signal.signal(signal.SIGTERM, _on_term)

    def _service() -> NvidiaRivaTranscriptionService:
        # Built on the first fragment, after ffmpeg has started, so a kill during
        # extract never constructs a channel or reads a key.
        existing = service_box.get("service")
        if existing is not None:
            return existing
        chosen = transport
        if chosen is None and scripted:
            chosen = load_scripted(workdir, SCRIPTED_NAME)
        if chosen is None:
            chosen = riva_transport_from_env()
        created = NvidiaRivaTranscriptionService(chosen)
        service_box["service"] = created
        return created

    def _hear(path: Path) -> FragmentWords:
        nonlocal seconds
        seconds += _wav_seconds(path)
        service = _service()
        remaining = deadline_at - time.monotonic()
        wav = path.read_bytes()
        future = asyncio.run_coroutine_threadsafe(
            service.transcribe_async(wav, language_code=language, deadline_s=remaining),
            loop,
        )
        try:
            transcript = future.result()
        except AsrRejected:
            raise _JobError("asr_rejected") from None
        except AsrReupload:
            if cancelled.is_set():
                raise _JobError("rpc_cancelled") from None
            raise _JobError("requires_reupload") from None
        return FragmentWords(text=transcript.text, words=transcript.words)

    outcome = await asyncio.to_thread(
        process, workdir / ORIGINAL, workdir, fragment_seconds=float(fragment_seconds),
        overlap_s=settings.fragment_overlap_s, transcribe=_hear, limits=limits, realtime=realtime,
    )
    return {
        "fragments": outcome.fragments,
        "seconds": round(seconds, 3),
        "transcript": _from_service(outcome.reconciled),
    }


def _wav_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as reader:
        rate = reader.getframerate()
        if rate <= 0:
            raise _JobError("asr_failed")
        return reader.getnframes() / rate


def _from_service(reconciled: ReconciledTranscript) -> JobTranscript:
    return {
        "text": reconciled.text,
        "precision": reconciled.precision,
        "words": [
            {"text": word.text, "start_s": word.start_s, "end_s": word.end_s}
            for word in reconciled.words
        ],
        "model": MODEL_NAME,
        "client_version": CLIENT_VERSION,
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


def _stderr_code(exc: BaseException) -> str:
    if isinstance(exc, _JobError):
        return exc.code
    return "requires_reupload"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("workdir", type=Path)
    parser.add_argument("--realtime", action="store_true")
    parser.add_argument("--fragment-seconds", type=int, default=None)
    parser.add_argument("--read-stub-transcript", action="store_true")
    parser.add_argument("--language", default=None)
    parser.add_argument("--deadline-s", type=float, default=None)
    parser.add_argument("--scripted-transport", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run(
            args.workdir,
            realtime=args.realtime,
            fragment_seconds=args.fragment_seconds,
            read_stub=args.read_stub_transcript,
            language=args.language,
            deadline_s=args.deadline_s,
            scripted=args.scripted_transport,
        )
    except (RuntimeError, OSError, wave.Error, subprocess.TimeoutExpired) as exc:
        # A code only. The message can carry a path or, worse, a transcript.
        print(json.dumps({"error": _stderr_code(exc)}), file=sys.stderr)
        return 2
    if not result["fragments"]:
        print(json.dumps({"error": "asr_failed"}), file=sys.stderr)
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

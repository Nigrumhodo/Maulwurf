"""In-process transport double for I-S2-SG-13.

The supervisor passes ``--scripted-transport`` only from a test config. The file
it reads is JSON, not audio, and it is deleted with the attempt directory.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from maulwurf_ingest.asr.riva_client import RecognizeCall, Transcript, TransportError
from maulwurf_ingest.audio.reconcile import LocalWord

BLOCKED_NAME = "rpc_blocked"


class ScriptedCall:
    """One fragment. ``block`` waits until ``cancel`` or the deadline."""

    def __init__(
        self, transcript: Transcript, *, block: bool, marker: Path | None,
    ) -> None:
        self._transcript = transcript
        self._block = block
        self._marker = marker
        self._cancelled = threading.Event()

    def result(self, timeout: float) -> Transcript:
        if not self._block:
            return self._transcript
        if self._marker is not None:
            self._marker.write_bytes(b"")
        if self._cancelled.wait(timeout):
            raise TransportError("CANCELLED")
        raise TransportError("DEADLINE_EXCEEDED")

    def cancel(self) -> None:
        self._cancelled.set()


class ScriptedTransport:
    """Returns the fixture transcript. One instance is reused for every fragment."""

    def __init__(self, document: object, workdir: Path) -> None:
        if not isinstance(document, dict):
            raise RuntimeError("scripted_transport")
        text = document.get("text", "")
        block = document.get("block", False)
        words_raw = document.get("words", [])
        if not isinstance(text, str) or not isinstance(block, bool):
            raise RuntimeError("scripted_transport")
        if not isinstance(words_raw, list):
            raise RuntimeError("scripted_transport")
        words: list[LocalWord] = []
        for item in words_raw:
            if not isinstance(item, dict):
                raise RuntimeError("scripted_transport")
            word = item.get("text")
            start = item.get("start_s")
            end = item.get("end_s")
            if not isinstance(word, str) or isinstance(start, bool) or isinstance(end, bool):
                raise RuntimeError("scripted_transport")
            if not isinstance(start, int | float) or not isinstance(end, int | float):
                raise RuntimeError("scripted_transport")
            words.append(LocalWord(word, float(start), float(end)))
        precision = "word" if words else "none"
        self._transcript = Transcript(text, tuple(words), precision)
        self._block = block
        self._marker = workdir / BLOCKED_NAME
        self.starts = 0

    def start(self, wav: bytes, *, language_code: str, deadline_s: float) -> RecognizeCall:
        del wav, language_code, deadline_s
        self.starts += 1
        return ScriptedCall(self._transcript, block=self._block, marker=self._marker)

    def close(self) -> None:
        return None


def load_scripted(workdir: Path, name: str) -> ScriptedTransport:
    try:
        document: object = json.loads((workdir / name).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise RuntimeError("scripted_transport") from None
    return ScriptedTransport(document, workdir)

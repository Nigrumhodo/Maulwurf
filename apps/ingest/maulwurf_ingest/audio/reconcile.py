"""Reconciliación de fronteras entre fragmentos (S2.3).

El tiempo global es el offset del fragmento más el tiempo local. El solape no
se suma otra vez. En la zona que ya cubrió el fragmento anterior se descarta
una palabra solo si el texto y el intervalo coinciden con una ya emitida.
Sin palabras no se inventan tiempos.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from maulwurf_ingest.audio.timestamps import global_time_s

_EPS_S = 1e-3


@dataclass(frozen=True)
class LocalWord:
    text: str
    start_s: float
    end_s: float


@dataclass(frozen=True)
class FragmentTranscript:
    start_s: float
    end_s: float
    text: str = ""
    words: tuple[LocalWord, ...] = ()


@dataclass(frozen=True)
class GlobalWord:
    text: str
    start_s: float
    end_s: float


@dataclass(frozen=True)
class ReconciledTranscript:
    text: str
    words: tuple[GlobalWord, ...]
    precision: str


def reconcile(fragments: Sequence[FragmentTranscript]) -> ReconciledTranscript:
    """Une fragmentos en orden. La precisión es `word` solo si todo el texto tiene palabras."""
    pieces: list[str] = []
    kept: list[GlobalWord] = []
    saw_text_only = False
    previous_end: float | None = None
    for fragment in fragments:
        overlap_s = 0.0 if previous_end is None else max(0.0, previous_end - fragment.start_s)
        if fragment.words:
            for word in fragment.words:
                start_s = global_time_s(fragment.start_s, word.start_s, overlap_s)
                end_s = global_time_s(fragment.start_s, word.end_s, overlap_s)
                if end_s <= start_s or not word.text.strip():
                    continue
                if _duplicate(kept, word.text, start_s, end_s, previous_end):
                    continue
                kept.append(GlobalWord(word.text, start_s, end_s))
                pieces.append(word.text.strip())
        else:
            saw_text_only = True
            incoming = fragment.text.strip()
            covered = previous_end is not None and fragment.start_s < previous_end - _EPS_S
            if covered:
                incoming = _strip_repeated_prefix(" ".join(pieces), incoming)
            if incoming:
                pieces.append(incoming)
        previous_end = fragment.end_s
    precision = "word" if kept and not saw_text_only else "none"
    return ReconciledTranscript(" ".join(pieces), tuple(kept), precision)


def _duplicate(
    kept: Sequence[GlobalWord],
    text: str,
    start_s: float,
    end_s: float,
    previous_end: float | None,
) -> bool:
    if previous_end is None or start_s >= previous_end - _EPS_S:
        return False
    wanted = _normalize(text)
    for word in kept:
        if _normalize(word.text) != wanted:
            continue
        if start_s < word.end_s - _EPS_S and word.start_s < end_s - _EPS_S:
            return True
    return False


def _strip_repeated_prefix(previous: str, incoming: str) -> str:
    prev_words = _normalize(previous).split()
    next_words = incoming.split()
    next_norm = [word.casefold() for word in next_words]
    best = 0
    limit = min(len(prev_words), len(next_norm))
    for count in range(1, limit + 1):
        if prev_words[-count:] == next_norm[:count]:
            best = count
    return " ".join(next_words[best:])


def _normalize(text: str) -> str:
    return " ".join(text.casefold().split())

"""Citation markers and source labels for streaming chat (L2.5, M7).

The model is asked to cite transcript evidence as `[S1]`, `[S2]`, ... The server never trusts
those indices: it only turns a marker into a citation event when it maps to a source that was
actually selected from the database. Unknown or malformed markers are emitted as literal text.
Markers can be split across provider deltas, so the buffer keeps an incomplete suffix until the
next delta (or until the stream ends, where it becomes literal text).
"""

import re
from dataclasses import dataclass

from app.services.search import RetrievedChunk

_MARKER = re.compile(r"\[S([1-9][0-9]*)\]")
_MARKER_PREFIX = re.compile(r"^\[S?[0-9]*$")


def format_timestamp(seconds: float) -> str:
    """Deterministic `mm:ss` / `h:mm:ss`; timestamps are evidence, never interpolated."""
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def citation_label(chunk: RetrievedChunk) -> str:
    """Human label built only from hydrated columns; no model-provided title is trusted."""
    parts = [chunk.subject_name, chunk.class_date.isoformat()]
    if chunk.t_start is not None and chunk.t_end is not None:
        parts.append(format_timestamp(chunk.t_start))
    elif chunk.segment_ordinal is not None:
        parts.append(f"segment {chunk.segment_ordinal}")
    return f"[{' · '.join(parts)}]"


def citation_url(chunk: RetrievedChunk) -> str:
    base = f"/biblioteca/{chunk.audio_id}"
    if chunk.segment_id is None:
        return base
    return f"{base}?segment={chunk.segment_id}"


@dataclass(frozen=True)
class CitationRef:
    """A selected source with its 1-based model index and the character offset it appeared at."""

    index: int
    chunk: RetrievedChunk
    offset: int


def citation_event(ref: CitationRef) -> dict[str, object]:
    """SSE `citation` data. Payload IDs are durable row keys; label/url are server-derived."""
    chunk = ref.chunk
    return {
        "index": ref.index,
        "audio_id": str(chunk.audio_id),
        "transcript_version": chunk.transcript_version,
        "chunk_id": str(chunk.chunk_id),
        "segment_id": str(chunk.segment_id) if chunk.segment_id is not None else None,
        "t_start": chunk.t_start,
        "t_end": chunk.t_end,
        "label": citation_label(chunk),
        "url": citation_url(chunk),
    }


def citation_record(ref: CitationRef) -> dict[str, object]:
    """Persisted JSONB citation; L2.6 may add a validated `quote` field.

    `label` and `url` are stored so a completed message can be replayed without trusting or
    re-reading a chunk that may have been tombstoned later. They are computed from hydrated
    database columns, not from model output.
    """
    chunk = ref.chunk
    return {
        "index": ref.index,
        "offset": ref.offset,
        "audio_id": str(chunk.audio_id),
        "transcript_id": str(chunk.transcript_id) if chunk.transcript_id is not None else None,
        "transcript_version": chunk.transcript_version,
        "chunk_id": str(chunk.chunk_id),
        "segment_id": str(chunk.segment_id) if chunk.segment_id is not None else None,
        "t_start": chunk.t_start,
        "t_end": chunk.t_end,
        "label": citation_label(chunk),
        "url": citation_url(chunk),
        "quote": None,
    }


class CitationBuffer:
    """Incrementally replace valid `[Sn]` markers with citation indices.

    `feed` returns the safe text to emit plus the citation indices in order of appearance.
    Invalid markers are preserved verbatim. A suffix that could still become a marker is kept
    pending; everything else is emitted immediately so streaming latency stays bounded.
    """

    def __init__(self, source_count: int) -> None:
        if source_count < 0:
            raise ValueError("source_count no puede ser negativo")
        self._source_count = source_count
        self._pending = ""

    def feed(self, text: str) -> tuple[str, tuple[int, ...]]:
        if not text:
            return "", ()
        data = self._pending + text
        self._pending = ""
        output: list[str] = []
        citations: list[int] = []
        position = 0
        while position < len(data):
            start = data.find("[", position)
            if start < 0:
                output.append(data[position:])
                break
            output.append(data[position:start])
            close = data.find("]", start + 1)
            if close < 0:
                suffix = data[start:]
                if _MARKER_PREFIX.match(suffix):
                    self._pending = suffix
                else:
                    output.append(suffix)
                break
            candidate = data[start : close + 1]
            match = _MARKER.fullmatch(candidate)
            index = int(match.group(1)) if match is not None else 0
            if index and index <= self._source_count:
                citations.append(index)
            else:
                output.append(candidate)
            position = close + 1
        return "".join(output), tuple(citations)

    def flush(self) -> str:
        pending, self._pending = self._pending, ""
        return pending

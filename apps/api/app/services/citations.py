"""Citation markers and source labels for streaming chat (L2.5/L2.6, M7).

The model is asked to cite transcript evidence as `[S1]`, `[S2]`, ... The server never trusts
those indices: it only turns a marker into a citation event when it maps to a source that was
actually selected from the database and still exists, belongs to the tenant and uses the
active transcript/index version. Unknown or malformed markers are emitted as literal text.
Markers can be split across provider deltas, so the buffer keeps an incomplete suffix until the
next delta (or until the stream ends, where it becomes literal text).
"""

import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.citations import Citation, CitationEvent
from app.services.evidence import evidence_label
from app.services.evidence import format_timestamp as format_timestamp
from app.services.reader_links import class_reader_path
from app.services.retrieval import RetrievedChunk
from app.services.search import SearchService

_MARKER = re.compile(r"\[S([1-9][0-9]*)\]")
_MARKER_PREFIX = re.compile(r"^\[S?[0-9]*$")


def citation_label(chunk: RetrievedChunk) -> str:
    """Human label built only from hydrated columns; no model-provided title is trusted."""
    return evidence_label(
        subject_name=chunk.subject_name,
        class_date=chunk.class_date,
        segment_ordinal=chunk.segment_ordinal,
        t_start=chunk.t_start,
        t_end=chunk.t_end,
    )


def citation_url(chunk: RetrievedChunk) -> str:
    return class_reader_path(chunk.audio_id, chunk.segment_id)


@dataclass(frozen=True)
class CitationRef:
    """A selected source with its 1-based model index and the character offset it appeared at."""

    index: int
    chunk: RetrievedChunk
    offset: int


CitationValidator = Callable[[uuid.UUID, RetrievedChunk], Awaitable[RetrievedChunk | None]]


def citation_source_matches(current: RetrievedChunk, source: RetrievedChunk) -> bool:
    """The fresh DB row must be the exact source the model was shown.

    The model only chooses an index; the durable identity always comes from the server's
    selected source. A chunk, audio, transcript version or segment that changed between
    retrieval and emission is rejected instead of being silently substituted.
    """
    return (
        current.chunk_id == source.chunk_id
        and current.audio_id == source.audio_id
        and current.transcript_id == source.transcript_id
        and current.transcript_version == source.transcript_version
        and current.segment_id == source.segment_id
    )


async def validate_citation_source(
    db: AsyncSession, *, user_id: uuid.UUID, source: RetrievedChunk
) -> RetrievedChunk | None:
    """Re-read a selected source and return it only while the DB still authorizes it."""
    if source.transcript_id is None or source.transcript_version is None:
        return None
    current = await SearchService(db, provider=None).rehydrate_source(
        user_id=user_id, source=source
    )
    if current is None or not citation_source_matches(current, source):
        return None
    return current


async def validate_citation_record(
    db: AsyncSession, *, user_id: uuid.UUID, record: Citation
) -> RetrievedChunk | None:
    """Revalidate a persisted citation before replaying it.

    The fresh row is returned so the replay event uses current labels and timestamps.
    A row that no longer matches the stored identity is dropped.
    """
    current = await SearchService(db, provider=None).rehydrate_chunk_ids(
        user_id=user_id,
        chunk_id=record.chunk_id,
        audio_id=record.audio_id,
    )
    if current is None:
        return None
    if (
        current.chunk_id != record.chunk_id
        or current.audio_id != record.audio_id
        or current.transcript_id != record.transcript_id
        or current.transcript_version != record.transcript_version
        or current.segment_id != record.segment_id
    ):
        return None
    return current


async def resolve_citation(
    *,
    index: int,
    source: RetrievedChunk,
    offset: int,
    user_id: uuid.UUID,
    validate: CitationValidator,
) -> CitationRef | None:
    """Resolve one model marker through the DB validator; unauthorized markers disappear."""
    current = await validate(user_id, source)
    if current is None:
        return None
    return CitationRef(index=index, chunk=current, offset=offset)


def citation_event(ref: CitationRef) -> CitationEvent:
    """SSE `citation` data. Payload IDs are durable row keys; label/url are server-derived."""
    chunk = ref.chunk
    version = chunk.transcript_version if chunk.transcript_version is not None else 0
    return CitationEvent(
        index=ref.index,
        audio_id=chunk.audio_id,
        transcript_version=version,
        chunk_id=chunk.chunk_id,
        segment_id=chunk.segment_id,
        t_start=chunk.t_start,
        t_end=chunk.t_end,
        label=citation_label(chunk),
        url=citation_url(chunk),
    )


def citation_record(ref: CitationRef) -> Citation:
    """Persisted citation; `quote` stays null until a later ticket populates it.

    `label` and `url` are stored so a completed message can be replayed without trusting or
    re-reading a chunk that may have been tombstoned later. They are computed from hydrated
    database columns, not from model output.
    """
    chunk = ref.chunk
    if chunk.transcript_id is None or chunk.transcript_version is None:
        raise ValueError("citation requires a transcript identity")
    return Citation(
        index=ref.index,
        offset=ref.offset,
        audio_id=chunk.audio_id,
        transcript_id=chunk.transcript_id,
        transcript_version=chunk.transcript_version,
        chunk_id=chunk.chunk_id,
        segment_id=chunk.segment_id,
        t_start=chunk.t_start,
        t_end=chunk.t_end,
        label=citation_label(chunk),
        url=citation_url(chunk),
        quote=None,
    )


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

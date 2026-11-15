"""One persisted citation, used by the SSE event, the message row and replay.

`RetrievedChunk` stays the retrieval row. A citation is the smaller identity the server
computed from that row and stored on the assistant message. Invalid JSONB entries are
dropped: a partial record must not become a link.
"""

from collections.abc import Sequence
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class Citation(BaseModel):
    """JSONB citation written by the chat stream and read back for replay and history."""

    model_config = ConfigDict(extra="ignore")

    index: int = Field(gt=0, strict=True)
    offset: int = Field(ge=0, strict=True)
    audio_id: UUID
    transcript_id: UUID
    transcript_version: int = Field(gt=0, strict=True)
    chunk_id: UUID
    segment_id: UUID | None = None
    t_start: float | None = None
    t_end: float | None = None
    label: str
    url: str
    quote: str | None = None


class CitationEvent(BaseModel):
    """SSE `citation` frame. Offset and transcript id stay on the persisted row."""

    model_config = ConfigDict(extra="ignore")

    index: int
    audio_id: UUID
    transcript_version: int
    chunk_id: UUID
    segment_id: UUID | None = None
    t_start: float | None = None
    t_end: float | None = None
    label: str
    url: str


class CitedSource(BaseModel):
    """One audio named by a citation in the current answer. `done.sources` is this list."""

    kind: Literal["transcript"] = "transcript"
    audio_id: UUID


def parse_citations(raw: object) -> tuple[Citation, ...]:
    """Keep only records that match `Citation`. Anything else is ignored."""
    if not isinstance(raw, list):
        return ()
    parsed: list[Citation] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            parsed.append(Citation.model_validate(item))
        except ValidationError:
            continue
    return tuple(parsed)


def dump_citations(citations: Sequence[Citation]) -> list[dict[str, object]]:
    return [item.model_dump(mode="json") for item in citations]


def cited_sources(citations: Sequence[Citation]) -> tuple[CitedSource, ...]:
    """Unique audios in citation order. History rows are not included."""
    seen: set[UUID] = set()
    sources: list[CitedSource] = []
    for citation in citations:
        if citation.audio_id in seen:
            continue
        seen.add(citation.audio_id)
        sources.append(CitedSource(audio_id=citation.audio_id))
    return tuple(sources)

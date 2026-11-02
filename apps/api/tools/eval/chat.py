"""Answer G4 questions through the real RAG pipeline and audit every citation.

The evaluator creates one fresh conversation per item, consumes the SSE stream produced by
``RAGService`` and independently re-checks every emitted citation against the database and
the exact sources the model was shown. The answer and citation text stay in the raw workdir;
only aggregates and ids are ever published.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import bind_user
from app.models import Audio, Chunk, ChunkSegment, Conversation, Message, Segment, Transcript
from app.services.llm import LLMProvider
from app.services.rag import RAGService
from tests.fixtures.g4.schema import G4Item
from tools.eval.corpus import LanguageIndex
from tools.eval.dataset import Dataset

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SSEEvent:
    """One parsed SSE frame; heartbeats are dropped by the parser."""

    name: str
    event_id: int | None
    data: Mapping[str, Any]


@dataclass(frozen=True)
class CitationOutcome:
    """One citation emitted by the pipeline, with the source text for human review."""

    item_id: str
    language: str
    index: int
    chunk_id: str
    segment_id: str | None
    audio_id: str
    transcript_version: int | None
    quote: str
    unauthorized: bool


@dataclass(frozen=True)
class AnswerOutcome:
    """One item's answer, citations and provenance for scoring and review."""

    item_id: str
    language: str
    answerable: bool
    question: str
    status: str
    answer: str
    citations: tuple[CitationOutcome, ...]
    sources: int
    unauthorized: int
    error: str | None
    took_ms: float
    persisted_citations: int
    notes: tuple[str, ...] = field(default_factory=tuple)


def parse_sse(frame: str) -> SSEEvent | None:
    """Parse one ``encode_event`` frame; return ``None`` for comments/heartbeats."""
    if not frame.strip() or frame.lstrip().startswith(":"):
        return None
    name: str | None = None
    event_id: int | None = None
    data_lines: list[str] = []
    for line in frame.splitlines():
        if not line or line.startswith(":"):
            continue
        field_name, separator, value = line.partition(":")
        if not separator:
            continue
        value = value[1:] if value.startswith(" ") else value
        if field_name == "event":
            name = value
        elif field_name == "id":
            try:
                event_id = int(value)
            except ValueError:
                event_id = None
        elif field_name == "data":
            data_lines.append(value)
    if name is None or not data_lines:
        return None
    payload = json.loads("\n".join(data_lines))
    if not isinstance(payload, dict):
        return None
    return SSEEvent(name=name, event_id=event_id, data=payload)


async def iter_sse_events(frames: AsyncIterator[str]) -> AsyncIterator[SSEEvent]:
    """Yield parsed events from the generator returned by ``stream_events``."""
    async for frame in frames:
        event = parse_sse(frame)
        if event is not None:
            yield event


async def run_answers(
    session_factory: async_sessionmaker[AsyncSession],
    dataset: Dataset,
    language: str,
    index: LanguageIndex,
    *,
    embedding_provider: Any,
    llm_provider: LLMProvider,
    run_id: str,
    skip_item_ids: Collection[str] = (),
    attempt: int = 0,
    partition: str = "test",
) -> tuple[AnswerOutcome, ...]:
    """Answer every test item of one language, recording failures instead of dropping them.

    ``skip_item_ids`` implements resume: successful items are never re-answered, while a failed
    item can be retried with a new ``client_message_id`` suffix in a fresh conversation.
    """
    # A2.17: bind the eval tenant for every short session opened by the service.
    bind_user(index.user_id)
    service = RAGService(
        session_factory=session_factory,
        embedding_provider=embedding_provider,
        llm_provider=llm_provider,
    )
    skipped = set(skip_item_ids)
    outcomes: list[AnswerOutcome] = []
    for item in dataset.partition_items(language, partition):
        if item.id in skipped:
            continue
        try:
            outcomes.append(
                await _answer_item(
                    session_factory,
                    service,
                    item,
                    index,
                    run_id=run_id,
                    attempt=attempt,
                )
            )
        except Exception as exc:  # noqa: BLE001 - a failed item makes the run incomplete
            logger.warning("g4.answer_failed item=%s error=%s", item.id, type(exc).__name__)
            outcomes.append(
                AnswerOutcome(
                    item_id=item.id,
                    language=language,
                    answerable=item.answerable,
                    question=item.question,
                    status="error",
                    answer="",
                    citations=(),
                    sources=0,
                    unauthorized=0,
                    error=f"{type(exc).__name__}: {exc}",
                    took_ms=0.0,
                    persisted_citations=0,
                )
            )
    return tuple(outcomes)


async def _answer_item(
    session_factory: async_sessionmaker[AsyncSession],
    service: RAGService,
    item: G4Item,
    index: LanguageIndex,
    *,
    run_id: str,
    attempt: int = 0,
) -> AnswerOutcome:
    started = time.perf_counter()
    conversation_id = await _create_conversation(session_factory, index, item)
    client_message_id = f"{run_id}:{item.id}"
    if attempt:
        client_message_id = f"{client_message_id}:retry{attempt}"
    prepared = await service.prepare(
        user_id=index.user_id,
        conversation_id=conversation_id,
        content=item.question,
        client_message_id=client_message_id,
    )
    if prepared.error is not None:
        return AnswerOutcome(
            item_id=item.id,
            language=item.language,
            answerable=item.answerable,
            question=item.question,
            status="error",
            answer="",
            citations=(),
            sources=len(prepared.sources),
            unauthorized=0,
            error=f"{prepared.error.code}: {prepared.error.message}",
            took_ms=(time.perf_counter() - started) * 1000.0,
            persisted_citations=0,
        )

    answer_parts: list[str] = []
    citation_payloads: list[Mapping[str, Any]] = []
    status = "completed"
    error: str | None = None
    async for event in iter_sse_events(service.stream_events(prepared)):
        if event.name == "delta":
            answer_parts.append(str(event.data.get("text", "")))
        elif event.name == "citation":
            citation_payloads.append(event.data)
        elif event.name == "done":
            status = str(event.data.get("status", "completed"))
            break
        elif event.name == "error":
            status = "error"
            error = f"{event.data.get('code', 'error')}: {event.data.get('message', '')}"
            break
    answer = "".join(answer_parts)

    persisted = await _persisted_message(
        session_factory, index.user_id, prepared.assistant_message_id
    )
    notes: list[str] = []
    if persisted is not None:
        if persisted.content != answer:
            notes.append("persisted_content_differs_from_stream")
        if persisted.status != status:
            notes.append("persisted_status_differs_from_stream")
        answer = persisted.content or answer
        status = persisted.status
        persisted_citations = len(persisted.citas)
    else:
        notes.append("persisted_message_missing")
        persisted_citations = 0

    citations = await _audit_citations(
        session_factory,
        item,
        index,
        prepared,
        citation_payloads,
    )
    if len(citations) != persisted_citations:
        notes.append("citation_count_differs_from_persisted")
    unauthorized = sum(1 for citation in citations if citation.unauthorized)
    return AnswerOutcome(
        item_id=item.id,
        language=item.language,
        answerable=item.answerable,
        question=item.question,
        status=status,
        answer=answer,
        citations=citations,
        sources=len(prepared.sources),
        unauthorized=unauthorized,
        error=error,
        took_ms=(time.perf_counter() - started) * 1000.0,
        persisted_citations=persisted_citations,
        notes=tuple(notes),
    )


async def _create_conversation(
    session_factory: async_sessionmaker[AsyncSession],
    index: LanguageIndex,
    item: G4Item,
) -> uuid.UUID:
    async with session_factory() as db:
        conversation = Conversation(
            user_id=index.user_id,
            mode="all",
            title=f"G4 {item.id}",
        )
        db.add(conversation)
        await db.flush()
        conversation_id = conversation.id
        await db.commit()
    return conversation_id


async def _persisted_message(
    session_factory: async_sessionmaker[AsyncSession],
    user_id: uuid.UUID,
    message_id: uuid.UUID,
) -> Message | None:
    async with session_factory() as db:
        return cast(
            Message | None,
            await db.scalar(
                select(Message).where(
                    Message.user_id == user_id,
                    Message.id == message_id,
                )
            ),
        )


async def _audit_citations(
    session_factory: async_sessionmaker[AsyncSession],
    item: G4Item,
    index: LanguageIndex,
    prepared: Any,
    payloads: Sequence[Mapping[str, Any]],
) -> tuple[CitationOutcome, ...]:
    source_chunk_ids = {str(source.chunk.chunk_id) for source in prepared.sources}
    outcomes: list[CitationOutcome] = []
    async with session_factory() as db:
        for payload in payloads:
            chunk_id = str(payload.get("chunk_id", ""))
            segment_id = payload.get("segment_id")
            authorized, quote = await _authorized_citation(
                db,
                index,
                chunk_id=chunk_id,
                segment_id=None if segment_id is None else str(segment_id),
                source_chunk_ids=source_chunk_ids,
            )
            outcomes.append(
                CitationOutcome(
                    item_id=item.id,
                    language=item.language,
                    index=_as_int(payload.get("index"), default=0),
                    chunk_id=chunk_id,
                    segment_id=None if segment_id is None else str(segment_id),
                    audio_id=str(payload.get("audio_id", "")),
                    transcript_version=_as_optional_int(payload.get("transcript_version")),
                    quote=quote,
                    unauthorized=not authorized,
                )
            )
    return tuple(outcomes)


async def _authorized_citation(
    db: AsyncSession,
    index: LanguageIndex,
    *,
    chunk_id: str,
    segment_id: str | None,
    source_chunk_ids: set[str],
) -> tuple[bool, str]:
    try:
        parsed_chunk_id = uuid.UUID(chunk_id)
    except ValueError:
        return False, ""
    if chunk_id not in source_chunk_ids:
        return False, ""
    row = await db.execute(
        select(Chunk.content)
        .join(Audio, and_(Audio.user_id == Chunk.user_id, Audio.id == Chunk.audio_id))
        .join(
            Transcript,
            and_(
                Transcript.user_id == Chunk.user_id,
                Transcript.id == Chunk.transcript_id,
            ),
        )
        .where(
            Chunk.user_id == index.user_id,
            Chunk.id == parsed_chunk_id,
            Chunk.index_version == index.generation,
            Audio.deleted_at.is_(None),
            Audio.active_transcript_version == Transcript.version,
        )
    )
    content = row.scalar_one_or_none()
    if content is None:
        return False, ""
    if segment_id is not None:
        try:
            parsed_segment_id = uuid.UUID(segment_id)
        except ValueError:
            return False, content[:1000]
        linked = await db.scalar(
            select(ChunkSegment.segment_id)
            .join(
                Segment,
                and_(
                    Segment.user_id == ChunkSegment.user_id,
                    Segment.id == ChunkSegment.segment_id,
                ),
            )
            .where(
                ChunkSegment.user_id == index.user_id,
                ChunkSegment.chunk_id == parsed_chunk_id,
                ChunkSegment.segment_id == parsed_segment_id,
            )
        )
        if linked is None:
            return False, content[:1000]
    return True, content[:1000]


def _as_int(value: Any, *, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value


def _as_optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


class _CitationRecord(BaseModel):
    """Validated JSON form of one emitted citation."""

    model_config = ConfigDict(extra="forbid")

    item_id: str
    language: str
    index: int
    chunk_id: str
    segment_id: str | None
    audio_id: str
    transcript_version: int | None
    quote: str
    unauthorized: bool


class _AnswerRecord(BaseModel):
    """Validated JSON form of one answer artifact row."""

    model_config = ConfigDict(extra="forbid")

    item_id: str
    language: str
    answerable: bool
    question: str
    status: str
    answer: str
    citations: list[_CitationRecord]
    sources: int
    unauthorized: int
    error: str | None
    took_ms: float
    persisted_citations: int
    notes: list[str]


def answer_to_dict(outcome: AnswerOutcome) -> dict[str, Any]:
    """Serialize one outcome for ``answers.jsonl``."""
    return _AnswerRecord(
        item_id=outcome.item_id,
        language=outcome.language,
        answerable=outcome.answerable,
        question=outcome.question,
        status=outcome.status,
        answer=outcome.answer,
        citations=[
            _CitationRecord(
                item_id=citation.item_id,
                language=citation.language,
                index=citation.index,
                chunk_id=citation.chunk_id,
                segment_id=citation.segment_id,
                audio_id=citation.audio_id,
                transcript_version=citation.transcript_version,
                quote=citation.quote,
                unauthorized=citation.unauthorized,
            )
            for citation in outcome.citations
        ],
        sources=outcome.sources,
        unauthorized=outcome.unauthorized,
        error=outcome.error,
        took_ms=outcome.took_ms,
        persisted_citations=outcome.persisted_citations,
        notes=list(outcome.notes),
    ).model_dump(mode="json")


def answer_from_dict(data: Mapping[str, Any]) -> AnswerOutcome:
    """Rebuild one outcome from a persisted artifact row."""
    record = _AnswerRecord.model_validate(data)
    return AnswerOutcome(
        item_id=record.item_id,
        language=record.language,
        answerable=record.answerable,
        question=record.question,
        status=record.status,
        answer=record.answer,
        citations=tuple(
            CitationOutcome(
                item_id=citation.item_id,
                language=citation.language,
                index=citation.index,
                chunk_id=citation.chunk_id,
                segment_id=citation.segment_id,
                audio_id=citation.audio_id,
                transcript_version=citation.transcript_version,
                quote=citation.quote,
                unauthorized=citation.unauthorized,
            )
            for citation in record.citations
        ),
        sources=record.sources,
        unauthorized=record.unauthorized,
        error=record.error,
        took_ms=record.took_ms,
        persisted_citations=record.persisted_citations,
        notes=tuple(record.notes),
    )

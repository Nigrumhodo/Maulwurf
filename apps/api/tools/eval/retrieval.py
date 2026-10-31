"""Run G4 retrieval and match expected evidence against every chunk segment link.

``SearchService`` hydrates only one segment per chunk for display. Recall@8 must consider
all ``chunk_segments`` links, so this module loads them explicitly and matches an expected
evidence span by audio, transcript version, segment ordinal and literal quote.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import bind_user
from app.models import Chunk, ChunkSegment, Segment
from app.services.embeddings import EmbeddingProvider
from app.services.search import SearchService
from tests.fixtures.g4.schema import G4Item
from tools.eval.corpus import LanguageIndex
from tools.eval.dataset import Dataset
from tools.eval.metrics import RetrievalResult

TOP_K = 8


@dataclass(frozen=True)
class RetrievalOutcome:
    """One question's retrieval plus the ranked ids kept for the raw artifact."""

    item_id: str
    language: str
    answerable: bool
    expected: int
    matched: int
    took_ms: float
    top_chunk_ids: tuple[str, ...]

    def as_result(self) -> RetrievalResult:
        """Project to the pure metric input."""
        return RetrievalResult(
            item_id=self.item_id,
            language=self.language,
            answerable=self.answerable,
            expected=self.expected,
            matched=self.matched,
            took_ms=self.took_ms,
        )


@dataclass
class _ChunkLink:
    audio_id: uuid.UUID
    transcript_version: int
    content: str
    ordinals: set[int]


async def run_retrieval(
    session_factory: async_sessionmaker[AsyncSession],
    dataset: Dataset,
    language: str,
    index: LanguageIndex,
    *,
    provider: EmbeddingProvider,
    partition: str = "test",
    skip_item_ids: Collection[str] = (),
) -> tuple[RetrievalOutcome, ...]:
    """Retrieve the top 8 chunks for every item of one partition."""
    # A2.17: the harness opens sessions outside an HTTP request; bind the eval tenant so
    # the engine listener can set app.user_id on each one.
    bind_user(index.user_id)
    outcomes: list[RetrievalOutcome] = []
    skipped = set(skip_item_ids)
    async with session_factory() as db:
        service = SearchService(db, provider=provider)
        for item in dataset.partition_items(language, partition):
            if item.id in skipped:
                continue
            started = time.perf_counter()
            hits = await service.retrieve(
                user_id=index.user_id,
                query=item.question,
                top_k=TOP_K,
            )
            took_ms = (time.perf_counter() - started) * 1000.0
            links = await _load_links(db, index.user_id, [hit.chunk_id for hit in hits])
            matched = _match_evidence(item, index, links)
            outcomes.append(
                RetrievalOutcome(
                    item_id=item.id,
                    language=language,
                    answerable=item.answerable,
                    expected=len(item.expected_evidence),
                    matched=matched,
                    took_ms=took_ms,
                    top_chunk_ids=tuple(str(hit.chunk_id) for hit in hits),
                )
            )
    return tuple(outcomes)


async def _load_links(
    db: AsyncSession,
    user_id: uuid.UUID,
    chunk_ids: Sequence[uuid.UUID],
) -> Mapping[uuid.UUID, _ChunkLink]:
    if not chunk_ids:
        return {}
    rows = await db.execute(
        select(
            Chunk.id,
            Chunk.audio_id,
            Chunk.transcript_version,
            Chunk.content,
            Segment.ordinal,
        )
        .join(
            ChunkSegment,
            and_(
                ChunkSegment.user_id == Chunk.user_id,
                ChunkSegment.chunk_id == Chunk.id,
            ),
        )
        .join(
            Segment,
            and_(
                Segment.user_id == ChunkSegment.user_id,
                Segment.id == ChunkSegment.segment_id,
            ),
        )
        .where(Chunk.user_id == user_id, Chunk.id.in_(chunk_ids))
    )
    links: dict[uuid.UUID, _ChunkLink] = {}
    for chunk_id, audio_id, transcript_version, content, ordinal in rows:
        link = links.get(chunk_id)
        if link is None:
            links[chunk_id] = _ChunkLink(
                audio_id=audio_id,
                transcript_version=transcript_version,
                content=content,
                ordinals={ordinal},
            )
        else:
            link.ordinals.add(ordinal)
    return links


def _match_evidence(
    item: G4Item,
    index: LanguageIndex,
    links: Mapping[uuid.UUID, _ChunkLink],
) -> int:
    matched = 0
    for evidence in item.expected_evidence:
        audio_id = index.audio_id(evidence.class_ref)
        for link in links.values():
            if link.audio_id != audio_id:
                continue
            if link.transcript_version != evidence.transcript_version:
                continue
            if evidence.segment_ordinal not in link.ordinals:
                continue
            if evidence.cita_textual in link.content:
                matched += 1
                break
    return matched

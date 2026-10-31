"""Physical cleanup of index generations that are never served (J2.4, `services/indexing.py`).

- `building` with no live `index` run is a crashed build. It was never activated, so nothing reads
  it. Its chunks, embeddings and row are deleted; a later delivery for the same version rebuilds
  it from the transcript.
- `retired` generations are kept as the rollback target. Only the most recent one per user stays,
  because that is the one a rollback would reactivate. Older ones are deleted.
- `retired` generations with chunks cited by a delivered message are kept: `message_sources` is
  durable evidence, its `chunk_id` foreign key has no cascade, and deleting the chunk would
  either fail or erase provenance. A later retention/purge policy can decide otherwise.

Deletion takes the generation row lock first and rechecks its status, so an activation that
races with the cleanup wins and the cleanup skips the row.
"""

import uuid
from datetime import datetime
from typing import Final

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chunk, Embedding, IndexGeneration, MessageSource, ProcessingRun
from app.services.index_job import LEASE, STAGE_INDEX, index_version_for
from app.services.indexing import INDEX_BUILDING, INDEX_RETIRED

RETIRED_GENERATIONS_KEPT: Final = 1

GenerationKey = tuple[uuid.UUID, str]


async def live_index_versions(db: AsyncSession, *, now: datetime) -> set[GenerationKey]:
    """`(user_id, version)` of every generation with a running, unexpired `index` lease."""
    rows = (
        await db.execute(
            select(
                ProcessingRun.user_id,
                ProcessingRun.audio_id,
                ProcessingRun.transcript_version,
            ).where(
                ProcessingRun.stage == STAGE_INDEX,
                ProcessingRun.status == "running",
                ProcessingRun.started_at >= now - LEASE,
            )
        )
    ).all()
    return {
        (row.user_id, index_version_for(row.audio_id, row.transcript_version)) for row in rows
    }


async def select_orphan_building(
    db: AsyncSession, *, now: datetime, limit: int
) -> list[GenerationKey]:
    """Building generations without a live run, oldest first.

    The limit applies before the liveness filter. Live builds are the newest rows, so they rarely
    crowd out orphans; a crowded batch is picked up again in the next cycle.
    """
    live = await live_index_versions(db, now=now)
    rows = (
        await db.execute(
            select(IndexGeneration.user_id, IndexGeneration.version)
            .where(IndexGeneration.status == INDEX_BUILDING)
            .order_by(IndexGeneration.created_at, IndexGeneration.id)
            .limit(limit)
        )
    ).all()
    return [(row.user_id, row.version) for row in rows if (row.user_id, row.version) not in live]


async def discard_orphan_building(
    db: AsyncSession, *, user_id: uuid.UUID, version: str, now: datetime
) -> bool:
    """Deletes one orphan build. Returns False when it is no longer building or has a live run."""
    if (user_id, version) in await live_index_versions(db, now=now):
        return False
    return await _discard_generation(db, user_id=user_id, version=version, status=INDEX_BUILDING)


async def select_surplus_retired(
    db: AsyncSession, *, keep: int, limit: int
) -> list[GenerationKey]:
    """Retired generations beyond the `keep` most recent of each user.

    Generations with a chunk cited by a delivered message are excluded: their chunks are
    referenced by `message_sources` and must stay until a purge policy removes the citation.
    """
    cited = (
        select(Chunk.id)
        .join(
            MessageSource,
            (MessageSource.user_id == Chunk.user_id) & (MessageSource.chunk_id == Chunk.id),
        )
        .where(
            Chunk.user_id == IndexGeneration.user_id,
            Chunk.index_version == IndexGeneration.version,
        )
        .correlate(IndexGeneration)
        .exists()
    )
    rank = (
        func.row_number()
        .over(
            partition_by=IndexGeneration.user_id,
            order_by=(
                IndexGeneration.activated_at.desc().nulls_last(),
                IndexGeneration.created_at.desc(),
                IndexGeneration.id,
            ),
        )
        .label("generation_rank")
    )
    ranked = (
        select(IndexGeneration.user_id, IndexGeneration.version, rank)
        .where(IndexGeneration.status == INDEX_RETIRED, ~cited)
        .subquery()
    )
    rows = (
        await db.execute(
            select(ranked.c.user_id, ranked.c.version)
            .where(ranked.c.generation_rank > keep)
            .limit(limit)
        )
    ).all()
    return [(row.user_id, row.version) for row in rows]


async def discard_surplus_retired(
    db: AsyncSession, *, user_id: uuid.UUID, version: str
) -> bool:
    """Deletes one retired generation beyond the retained rollback target."""
    return await _discard_generation(db, user_id=user_id, version=version, status=INDEX_RETIRED)


async def _discard_generation(
    db: AsyncSession, *, user_id: uuid.UUID, version: str, status: str
) -> bool:
    locked = (
        await db.execute(
            select(IndexGeneration.id)
            .where(
                IndexGeneration.user_id == user_id,
                IndexGeneration.version == version,
                IndexGeneration.status == status,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if locked is None:
        return False
    cited = await db.scalar(
        select(Chunk.id)
        .join(
            MessageSource,
            (MessageSource.user_id == Chunk.user_id) & (MessageSource.chunk_id == Chunk.id),
        )
        .where(Chunk.user_id == user_id, Chunk.index_version == version)
        .limit(1)
    )
    if cited is not None:
        # Chunks referenced by message_sources are durable citation evidence and have no
        # ON DELETE CASCADE. Keep the whole generation instead of failing the delete forever.
        return False
    # Embeddings and chunk_segments follow their chunk through ON DELETE CASCADE; embeddings are
    # also deleted by version so no vector can outlive its generation.
    await db.execute(
        delete(Embedding)
        .where(Embedding.user_id == user_id, Embedding.index_version == version)
        .execution_options(synchronize_session=False)
    )
    await db.execute(
        delete(Chunk)
        .where(Chunk.user_id == user_id, Chunk.index_version == version)
        .execution_options(synchronize_session=False)
    )
    await db.execute(
        delete(IndexGeneration)
        .where(IndexGeneration.user_id == user_id, IndexGeneration.version == version)
        .execution_options(synchronize_session=False)
    )
    return True

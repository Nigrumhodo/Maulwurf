"""Versioned index generations with atomic activation (L2.2, ESPECIFICACION §M6).

What this module guarantees:

- **A single active generation per user.** Activation issues `retire` + `activate` + `audios`
  pointers and makes **one single commit**: a reader in `READ COMMITTED` sees the old version
  or the new one, never a mixture. The strong guarantee comes from the database
  (`index_generations_one_active`, partial unique index of migration 0002): two concurrent
  activations collide and the loser becomes `GenerationConflict` (`generation_conflict`).
- **The retired generation is kept, not deleted (review of PR #71).** Activation only flips
  status and pointers: the previous generation's chunks/embeddings stay materialized. A
  failure inside the activation still rolls everything back, and the retained rows are the
  prerequisite for a future rollback to the previous version; the search already excludes
  them because it filters by `active_generation`. Physical cleanup of retired generations is
  a retention policy owned by J2.4 (plan §5), not by the activation transaction.
- **A partial index is never activated.** Before activating, every `chunk` of the version
  must have its `embeddings` row and every eligible class must be covered; otherwise
  `generation_incomplete` and the generation stays in `building` (invisible: the search
  filters by `active_generation`, the only source of truth this ticket exports).
- **model/dimension/version are never mixed.** The registered values come from `settings` and
  are checked against the generation and the provider before writing anything.
- **Idempotency:** re-running `build_generation` duplicates neither chunks (already chunked
  classes are skipped for that version) nor embeddings (anti-join against `embeddings`).
- **Lexical vector materialized (L2.3):** every persisted `chunk` carries its `tsv` built with
  the document's `transcripts.fts_config` (`chunk_tsvector`), so the lexical search branch can
  use the `chunks_fts` GIN index with a per-language configuration.
- **Isolation:** every query and every written row carries the received `user_id`.
- **No content in logs:** only version, model, counts and error codes.
- **D3-Texto fail closed:** `text_kind="real"` is only accepted when the model is listed in
  `d3_text_closed_models` (empty by default, S2.md §0.6).

Caller-session note: the switch statements use `synchronize_session=False`, so the returned
`IndexGeneration` is updated in Python while other entities already loaded in the same session
are not synchronized; `active_generation()` is the authoritative read (each request has its
own session).

What it does NOT do (later tickets): `processing_runs` lifecycle and the ARQ job (J2.2/J2.1),
hybrid search/RRF (L2.3/L2.4), chat (L2.5), migrations (A2.18) and the physical cleanup /
retention of retired generations (J2.4).
"""

import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast

from sqlalchemy import (
    ColumnElement,
    CursorResult,
    Select,
    Update,
    and_,
    func,
    literal,
    select,
    update,
)
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import REGCONFIG
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import (
    Audio,
    Chunk,
    ChunkSegment,
    Embedding,
    IndexGeneration,
    Segment,
    Transcript,
)
from app.schemas.chunk import Chunk as ChunkContract
from app.services.chunking import SegmentInput, chunk_segments
from app.services.embeddings import (
    EmbeddingDimensionMismatch,
    EmbeddingProvider,
    EmbeddingProviderError,
)

logger = logging.getLogger(__name__)

INDEX_BUILDING = "building"
INDEX_ACTIVE = "active"
INDEX_RETIRED = "retired"
TEXT_KINDS = ("synthetic", "redacted", "real")
TextKind = Literal["synthetic", "redacted", "real"]
ProgressHook = Callable[["IndexProgress"], Awaitable[None]]


class IndexingError(Exception):
    """Typed index-generation error; `code` is what J2.2 will publish in the run."""

    code = "indexing_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class GenerationEmpty(IndexingError):
    code = "generation_empty"


class GenerationIncomplete(IndexingError):
    code = "generation_incomplete"


class GenerationConflict(IndexingError):
    code = "generation_conflict"


class IndexTextNotAllowed(IndexingError):
    code = "index_text_not_allowed"


@dataclass(frozen=True)
class IndexStats:
    version: str
    model: str
    dimension: int
    audios: int
    chunks: int
    embeddings: int
    batches: int


@dataclass(frozen=True)
class IndexProgress:
    version: str
    audio_id: uuid.UUID | None
    audios_done: int
    audios_total: int
    chunks: int
    embeddings: int


@dataclass(frozen=True)
class _Verification:
    chunks: int
    embeddings: int
    chunks_without_embeddings: int
    missing_audios: int


def require_text_kind(text_kind: str) -> TextKind:
    """Validate the origin of the text and apply the D3-Texto fail-closed gate."""
    if text_kind not in TEXT_KINDS:
        raise ValueError(f"text_kind inválido: {text_kind}")
    if text_kind == "real" and settings.embedding_model not in settings.d3_text_closed_models:
        raise IndexTextNotAllowed(
            "D3-Texto sigue abierta para este modelo; solo se indexa texto sintético o redactado.",
            details={"model": settings.embedding_model},
        )
    return cast(TextKind, text_kind)


async def active_generation(db: AsyncSession, user_id: uuid.UUID) -> IndexGeneration | None:
    """Active version of the user: the only source of truth for the search branches."""
    result = await db.execute(
        select(IndexGeneration).where(
            IndexGeneration.user_id == user_id,
            IndexGeneration.status == INDEX_ACTIVE,
        )
    )
    return result.scalar_one_or_none()


async def create_generation(
    db: AsyncSession, *, user_id: uuid.UUID, version: str, commit: bool = True
) -> IndexGeneration:
    """Open the generation in `building` with the registered model and dimension."""
    generation = IndexGeneration(
        user_id=user_id,
        version=version,
        model=settings.embedding_model,
        dimension=settings.embedding_dimension,
        status=INDEX_BUILDING,
        chunk_count=0,
    )
    db.add(generation)
    await db.flush()
    if commit:
        await db.commit()
    return generation


async def build_generation(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    version: str,
    provider: EmbeddingProvider,
    text_kind: TextKind,
    on_progress: ProgressHook | None = None,
) -> IndexStats:
    """Chunk and embed whatever the `version` generation is missing (idempotent)."""
    require_text_kind(text_kind)
    generation = await _building_generation(db, user_id=user_id, version=version)
    _require_provider_matches(generation, provider)
    transcripts = await _eligible_transcripts(db, user_id=user_id)
    chunked_audios = set(await _chunked_audio_ids(db, user_id=user_id, version=version))
    chunks = await _chunk_generation(
        db,
        user_id=user_id,
        version=version,
        transcripts=transcripts,
        chunked_audios=chunked_audios,
        on_progress=on_progress,
    )
    logger.debug("indexing.chunked version=%s chunks=%d", version, chunks)
    embeddings, batches = await _embed_generation(
        db,
        user_id=user_id,
        version=version,
        generation=generation,
        provider=provider,
        on_progress=on_progress,
    )
    total = await _count(db, _chunk_count_statement(user_id, version=version))
    generation.chunk_count = total
    await db.commit()
    logger.info(
        "indexing.generation_built version=%s model=%s chunks=%d embeddings=%d audios=%d",
        version,
        generation.model,
        total,
        embeddings,
        len(transcripts),
    )
    return IndexStats(
        version=version,
        model=generation.model,
        dimension=generation.dimension,
        audios=len(transcripts),
        chunks=total,
        embeddings=embeddings,
        batches=batches,
    )


async def activate_generation(
    db: AsyncSession, *, user_id: uuid.UUID, version: str, commit: bool = True
) -> IndexGeneration:
    """Activate the complete generation in a single transaction with a single commit."""
    generation = await _fetch_generation(db, user_id=user_id, version=version)
    if generation.status == INDEX_ACTIVE:
        return generation  # Idempotent retry: there is no second `retire`.
    if generation.status != INDEX_BUILDING:
        raise GenerationConflict(
            "La generación no está en construcción.",
            details={"reason": "not_building", "status": generation.status},
        )
    verification = await _verify_generation(db, user_id=user_id, version=version)
    if verification.chunks == 0 or verification.embeddings == 0:
        raise GenerationEmpty(
            "La generación no tiene contenido que activar.",
            details={"reason": "no_content", "chunks": verification.chunks},
        )
    if verification.chunks_without_embeddings or verification.missing_audios:
        raise GenerationIncomplete(
            "La generación está incompleta: activarla expondría un índice parcial.",
            details={
                "chunks_without_embeddings": verification.chunks_without_embeddings,
                "missing_audios": verification.missing_audios,
            },
        )
    activated_at = datetime.now(UTC)
    try:
        await db.execute(_retire_active_statement(user_id))
        activated = cast(
            CursorResult[Any],
            await db.execute(
                _activate_statement(
                    user_id=user_id,
                    version=version,
                    chunk_count=verification.chunks,
                    activated_at=activated_at,
                )
            ),
        )
        if activated.rowcount != 1:
            await db.rollback()
            raise GenerationConflict(
                "Otra activación ganó la carrera.", details={"reason": "activation_lost"}
            )
        await db.execute(_audio_versions_statement(user_id=user_id, version=version))
        await db.execute(_clear_stale_audio_versions_statement(user_id=user_id, version=version))
        # The ORM object reflects the new state for the caller; the real activation is the
        # UPDATE above, inside the same transaction.
        generation.status = INDEX_ACTIVE
        generation.activated_at = activated_at
        generation.chunk_count = verification.chunks
        if commit:
            await db.commit()
    except IntegrityError as exc:
        # `index_generations_one_active` rejected a second active version: the DB prevents the
        # mixture and the caller retries from the beginning.
        await db.rollback()
        raise GenerationConflict(
            "El índice activo cambió durante la activación.",
            details={"reason": "concurrent_activation"},
        ) from exc
    logger.info("indexing.generation_activated version=%s chunks=%d", version, verification.chunks)
    return generation


async def _building_generation(
    db: AsyncSession, *, user_id: uuid.UUID, version: str
) -> IndexGeneration:
    generation = await _fetch_generation(db, user_id=user_id, version=version)
    if generation.status != INDEX_BUILDING:
        raise GenerationConflict(
            "La generación no está en construcción.",
            details={"reason": "not_building", "status": generation.status},
        )
    return generation


async def _fetch_generation(
    db: AsyncSession, *, user_id: uuid.UUID, version: str
) -> IndexGeneration:
    result = await db.execute(_generation_statement(user_id, version=version))
    generation = result.scalar_one_or_none()
    if generation is None:
        raise GenerationConflict(
            "No existe esa generación de índice.",
            details={"reason": "unknown_version", "version": version},
        )
    return generation


def _require_provider_matches(generation: IndexGeneration, provider: EmbeddingProvider) -> None:
    if (
        generation.dimension != settings.embedding_dimension
        or provider.dimension != generation.dimension
    ):
        raise EmbeddingDimensionMismatch(
            "La dimensión del proveedor no coincide con la generación registrada.",
            details={
                "generation": generation.dimension,
                "provider": provider.dimension,
                "settings": settings.embedding_dimension,
            },
        )
    if provider.model != generation.model:
        raise GenerationConflict(
            "El modelo del proveedor no coincide con la generación registrada.",
            details={
                "reason": "model_drift",
                "generation": generation.model,
                "provider": provider.model,
            },
        )


async def _chunk_generation(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    version: str,
    transcripts: Sequence[Transcript],
    chunked_audios: set[uuid.UUID],
    on_progress: ProgressHook | None,
) -> int:
    created = 0
    done = 0
    for transcript in transcripts:
        done += 1
        if transcript.audio_id in chunked_audios:
            continue  # Already chunked in this version: a re-run does not duplicate chunks.
        segments = await _segments(db, user_id=user_id, transcript_id=transcript.id)
        for chunk in _chunks(transcript, version=version, segments=segments):
            _persist_chunk(
                db,
                user_id=user_id,
                fts_config=transcript.fts_config,
                chunk=chunk,
            )
            created += 1
        # One commit per class: progress stays durable without a transaction spanning the whole
        # corpus (the provider network never happens here).
        await db.commit()
        if on_progress is not None:
            await on_progress(
                IndexProgress(
                    version=version,
                    audio_id=transcript.audio_id,
                    audios_done=done,
                    audios_total=len(transcripts),
                    chunks=created,
                    embeddings=0,
                )
            )
    return created


async def _embed_generation(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    version: str,
    generation: IndexGeneration,
    provider: EmbeddingProvider,
    on_progress: ProgressHook | None,
) -> tuple[int, int]:
    pending = await _chunks_without_embeddings(db, user_id=user_id, version=version)
    created = 0
    batches = 0
    for start in range(0, len(pending), provider.batch_size):
        batch = pending[start : start + provider.batch_size]
        vectors = await provider.embed([chunk.content for chunk in batch])
        if len(vectors) != len(batch):
            raise EmbeddingProviderError(
                "El proveedor devolvió un número de vectores distinto del lote.",
                details={"expected": len(batch), "observed": len(vectors)},
            )
        for chunk, vector in zip(batch, vectors, strict=True):
            db.add(
                Embedding(
                    user_id=chunk.user_id,
                    chunk_id=chunk.id,
                    index_version=version,
                    model=generation.model,
                    dimension=generation.dimension,
                    vector=vector,
                )
            )
        await db.commit()
        batches += 1
        created += len(batch)
        if on_progress is not None:
            await on_progress(
                IndexProgress(
                    version=version,
                    audio_id=None,
                    audios_done=0,
                    audios_total=0,
                    chunks=len(pending),
                    embeddings=created,
                )
            )
    return created, batches


async def _verify_generation(
    db: AsyncSession, *, user_id: uuid.UUID, version: str
) -> _Verification:
    return _Verification(
        chunks=await _count(db, _chunk_count_statement(user_id, version=version)),
        embeddings=await _count(db, _embedding_count_statement(user_id, version=version)),
        chunks_without_embeddings=await _count(
            db, _chunks_without_embeddings_count_statement(user_id, version=version)
        ),
        missing_audios=len(await _missing_audio_ids(db, user_id=user_id, version=version)),
    )


async def _missing_audio_ids(
    db: AsyncSession, *, user_id: uuid.UUID, version: str
) -> list[uuid.UUID]:
    eligible = (await db.execute(_eligible_audio_ids_statement(user_id))).scalars().all()
    covered = set(await _chunked_audio_ids(db, user_id=user_id, version=version))
    return [audio_id for audio_id in eligible if audio_id not in covered]


async def _eligible_transcripts(db: AsyncSession, *, user_id: uuid.UUID) -> list[Transcript]:
    result = await db.execute(eligible_transcripts_statement(user_id))
    return list(result.scalars().all())


async def _segments(
    db: AsyncSession, *, user_id: uuid.UUID, transcript_id: uuid.UUID
) -> list[Segment]:
    result = await db.execute(_segments_statement(user_id, transcript_id=transcript_id))
    return list(result.scalars().all())


async def _chunked_audio_ids(
    db: AsyncSession, *, user_id: uuid.UUID, version: str
) -> list[uuid.UUID]:
    result = await db.execute(_chunked_audio_ids_statement(user_id, version=version))
    return list(result.scalars().all())


async def _chunks_without_embeddings(
    db: AsyncSession, *, user_id: uuid.UUID, version: str
) -> list[Chunk]:
    result = await db.execute(_chunks_without_embeddings_statement(user_id, version=version))
    return list(result.scalars().all())


async def _count(db: AsyncSession, statement: Select[tuple[int]]) -> int:
    return int((await db.execute(statement)).scalar_one())


def _chunks(
    transcript: Transcript, *, version: str, segments: Sequence[Segment]
) -> list[ChunkContract]:
    try:
        return chunk_segments(
            audio_id=transcript.audio_id,
            transcript_id=transcript.id,
            transcript_version=transcript.version,
            index_version=version,
            transcript_text=transcript.text,
            segments=[
                SegmentInput(
                    id=segment.id,
                    ordinal=segment.ordinal,
                    text=segment.text,
                    char_start=segment.char_start,
                    char_end=segment.char_end,
                    t_start=segment.t_start,
                    t_end=segment.t_end,
                )
                for segment in segments
            ],
        )
    except ValueError as exc:
        # A transcript that breaks the chunker invariants cannot build a complete generation:
        # the activation is refused instead of indexing a partial text.
        raise GenerationIncomplete(
            "No se pudo trocear el transcript.",
            details={"audio_id": str(transcript.audio_id), "reason": "chunking_invalid"},
        ) from exc


def chunk_tsvector(fts_config: str, content: str) -> ColumnElement[Any]:
    """`chunks.tsv` built with the document's `transcripts.fts_config` (M6, L2.3).

    Materializing the vector at write time is what lets the lexical branch use the
    `chunks_fts` GIN index (A2.1) with a per-language configuration: the query side then
    only needs `chunks.tsv @@ <tsquery>`. The `regconfig` cast is explicit because the
    column is `text` and PostgreSQL would not resolve `to_tsvector(text, text)`.
    """
    return func.to_tsvector(sql_cast(literal(fts_config), REGCONFIG), content)


def _persist_chunk(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    fts_config: str,
    chunk: ChunkContract,
) -> None:
    """Write one §M6 `Chunk`: fields land on their columns and the link payload is the jsonb.

    The only additions are the tenant key (`user_id`) and the derived `tsv`; no field is
    renamed or reshaped, which is what U-S2-JL-11 pins.
    """
    # PK generated here (not `server_default`) so `chunk_segments` can link without re-reading.
    chunk_id = uuid.uuid4()
    db.add(
        Chunk(
            id=chunk_id,
            user_id=user_id,
            audio_id=chunk.audio_id,
            transcript_id=chunk.transcript_id,
            transcript_version=chunk.transcript_version,
            index_version=chunk.index_version,
            ordinal=chunk.ordinal,
            content=chunk.content,
            char_start=chunk.char_start,
            char_end=chunk.char_end,
            token_count=chunk.token_count,
            t_start=chunk.t_start,
            t_end=chunk.t_end,
            tsv=chunk_tsvector(fts_config, chunk.content),
        )
    )
    for link in chunk.segments:
        db.add(
            ChunkSegment(
                user_id=user_id,
                chunk_id=chunk_id,
                segment_id=link.segment_id,
                overlap_span=link.overlap_span.model_dump(exclude_none=True),
            )
        )


def _transcript_join_condition() -> ColumnElement[bool]:
    return and_(Transcript.user_id == Audio.user_id, Transcript.audio_id == Audio.id)


def _eligible_conditions(user_id: uuid.UUID) -> list[ColumnElement[bool]]:
    """Eligible-class conditions for indexing; single definition for chunking and coverage."""
    return [
        Audio.user_id == user_id,
        Audio.deleted_at.is_(None),
        Audio.active_transcript_version == Transcript.version,
        func.length(func.btrim(Transcript.text)) > 0,
    ]


def eligible_transcripts_statement(user_id: uuid.UUID) -> Select[tuple[Transcript]]:
    """Indexable classes: not deleted, with an active transcript and non-empty text."""
    return (
        select(Transcript)
        .join(Audio, _transcript_join_condition())
        .where(*_eligible_conditions(user_id))
        .order_by(Transcript.audio_id, Transcript.version)
    )


def _eligible_audio_ids_statement(user_id: uuid.UUID) -> Select[tuple[uuid.UUID]]:
    return (
        select(Audio.id)
        .join(Transcript, _transcript_join_condition())
        .where(*_eligible_conditions(user_id))
    )


def _generation_statement(user_id: uuid.UUID, *, version: str) -> Select[tuple[IndexGeneration]]:
    return select(IndexGeneration).where(
        IndexGeneration.user_id == user_id, IndexGeneration.version == version
    )


def _segments_statement(user_id: uuid.UUID, *, transcript_id: uuid.UUID) -> Select[tuple[Segment]]:
    return (
        select(Segment)
        .where(Segment.user_id == user_id, Segment.transcript_id == transcript_id)
        .order_by(Segment.ordinal)
    )


def _chunk_count_statement(user_id: uuid.UUID, *, version: str) -> Select[tuple[int]]:
    return (
        select(func.count())
        .select_from(Chunk)
        .where(Chunk.user_id == user_id, Chunk.index_version == version)
    )


def _embedding_count_statement(user_id: uuid.UUID, *, version: str) -> Select[tuple[int]]:
    return (
        select(func.count())
        .select_from(Embedding)
        .where(Embedding.user_id == user_id, Embedding.index_version == version)
    )


def _embedding_join_condition() -> ColumnElement[bool]:
    return and_(
        Embedding.user_id == Chunk.user_id,
        Embedding.chunk_id == Chunk.id,
        Embedding.index_version == Chunk.index_version,
    )


def _chunks_without_embeddings_count_statement(
    user_id: uuid.UUID, *, version: str
) -> Select[tuple[int]]:
    return (
        select(func.count())
        .select_from(Chunk)
        .outerjoin(Embedding, _embedding_join_condition())
        .where(
            Chunk.user_id == user_id,
            Chunk.index_version == version,
            Embedding.chunk_id.is_(None),
        )
    )


def _chunks_without_embeddings_statement(
    user_id: uuid.UUID, *, version: str
) -> Select[tuple[Chunk]]:
    return (
        select(Chunk)
        .outerjoin(Embedding, _embedding_join_condition())
        .where(
            Chunk.user_id == user_id,
            Chunk.index_version == version,
            Embedding.chunk_id.is_(None),
        )
        .order_by(Chunk.audio_id, Chunk.ordinal)
    )


def _chunked_audio_ids_statement(user_id: uuid.UUID, *, version: str) -> Select[tuple[uuid.UUID]]:
    return (
        select(Chunk.audio_id)
        .where(Chunk.user_id == user_id, Chunk.index_version == version)
        .distinct()
    )


def _retire_active_statement(user_id: uuid.UUID) -> Update:
    return (
        update(IndexGeneration)
        .where(IndexGeneration.user_id == user_id, IndexGeneration.status == INDEX_ACTIVE)
        .values(status=INDEX_RETIRED)
        .execution_options(synchronize_session=False)
    )


def _activate_statement(
    *,
    user_id: uuid.UUID,
    version: str,
    chunk_count: int,
    activated_at: datetime,
) -> Update:
    return (
        update(IndexGeneration)
        .where(
            IndexGeneration.user_id == user_id,
            IndexGeneration.version == version,
            IndexGeneration.status == INDEX_BUILDING,
        )
        .values(status=INDEX_ACTIVE, activated_at=activated_at, chunk_count=chunk_count)
        .execution_options(synchronize_session=False)
    )


def _audio_versions_statement(*, user_id: uuid.UUID, version: str) -> Update:
    covered = (
        select(Chunk.id)
        .where(
            Chunk.user_id == Audio.user_id,
            Chunk.audio_id == Audio.id,
            Chunk.index_version == version,
        )
        .correlate(Audio)
        .exists()
    )
    return (
        update(Audio)
        .where(Audio.user_id == user_id, Audio.deleted_at.is_(None), covered)
        .values(active_index_version=version)
        .execution_options(synchronize_session=False)
    )


def _clear_stale_audio_versions_statement(*, user_id: uuid.UUID, version: str) -> Update:
    return (
        update(Audio)
        .where(
            Audio.user_id == user_id,
            Audio.deleted_at.is_(None),
            Audio.active_index_version.is_not(None),
            Audio.active_index_version != version,
        )
        .values(active_index_version=None)
        .execution_options(synchronize_session=False)
    )

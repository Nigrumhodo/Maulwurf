"""Hybrid retrieval: per-language FTS + pgvector cosine merged with RRF (L2.3, M6).

What this module guarantees (S2.md §1.3 and §L2.3, ESPECIFICACION §M6/DISENO §2.5/§3.9):

- **Filters before the top-k, in both branches.** The lexical and the vector query apply
  `user_id`, tombstone (`audios.deleted_at IS NULL`), active index generation
  (`chunks.index_version`), active transcript version
  (`transcripts.version = audios.active_transcript_version`) and the optional
  subject/date filters **inside each branch**, and only then their own `LIMIT`. There is never
  a global top-k that filters the tenant afterwards (G3).
- **Lexical branch with a per-language configuration.** The query is tokenized with the
  document's own `transcripts.fts_config` (`spanish`/`english`/`simple`) via
  `websearch_to_tsquery`, which parses arbitrary user text without syntax errors or
  injection. It matches `chunks.tsv`, materialized at write time by `indexing.chunk_tsvector`,
  so the `chunks_fts` GIN index (A2.1) is usable. The transcript is never translated.
- **Vector branch over the HNSW cosine index.** `embeddings.vector <=> :query_vector` with
  `SET LOCAL hnsw.iterative_scan = strict_order` so that selective filters do not leave the
  top-k short (DISENO §2.5); the caller owns the transaction of the request.
- **Deterministic RRF.** `score_rrf(chunk) = sum 1 / (k + rank)` with a documented `RRF_K`;
  the total order is `score_rrf DESC, chunk_id ASC`, so the same input always produces the
  same output and the `chunk_id` tiebreaker doubles as the pagination cursor (L2.4).
- **Dedupe and diversity before the top-k.** A chunk reached by both branches counts once
  (natural dedupe by `chunk_id`); overlapping content (the same normalized text inside one
  class) collapses to a single survivor, using the candidate content read before the fusion;
  diversity caps how many chunks a single class can contribute. Only then is the list cut to
  `top_k`, so diversity cannot leave the result set short.
- **`iterative_scan` is enabled inside a SAVEPOINT.** An unsupported GUC (pgvector < 0.8)
  rolls the savepoint back and surfaces as `SearchHnswUnsupported`, never as a poisoned
  caller transaction.
- **No content in logs.** Only counts, version and error codes; never `content`, the query
  text, vectors or the API key.

What it does NOT do (later tickets): the `GET /search` endpoint, snippet and cursor
pagination (L2.4/A2.11), the reusable public `SearchService` contract wired into chat (L2.4/
L2.5), validated citations (L2.6), embedding the question (L2.2 provider + L2.4/L2.5), the
G4 metrics (L2.7) and the latency harness (L2.8). It never writes: read-only retrieval.
"""

import logging
import uuid
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import ColumnElement, Select, and_, cast, func, select, text
from sqlalchemy.dialects.postgresql import REGCONFIG
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.models import Audio, Chunk, Embedding, Transcript
from app.services.indexing import active_generation

logger = logging.getLogger(__name__)

# Documented contract constants (plan §7, decisión 3). Changing them changes the retrieval
# contract, not a tuning knob: they are not read from the environment.
RRF_K = 60  # Cormack et al. RRF default; the k of `1 / (k + rank)`.
CANDIDATES_PER_BRANCH = 30  # ~30 per branch (S2.md §1.3, ESPECIFICACION M7).
DEFAULT_TOP_K = 8  # ~8 final chunks under the token budget (M7).
MAX_CHUNKS_PER_AUDIO = 2  # diversity cap per class (plan §7, decisión 4).

# Per-transaction GUC: with filters active, HNSW must keep scanning until it has `LIMIT`
# candidates, otherwise the top-k comes back short (DISENO §2.5).
ITERATIVE_SCAN_SQL = text("SET LOCAL hnsw.iterative_scan = strict_order")


class SearchError(Exception):
    """Typed retrieval error; `code` is what L2.4 will publish in the HTTP envelope."""

    code = "search_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class SearchInvalidQuery(SearchError):
    code = "search_invalid_query"


class SearchVectorRequired(SearchError):
    code = "search_vector_required"


class SearchNotConfigured(SearchError):
    code = "search_not_configured"


class SearchHnswUnsupported(SearchError):
    code = "search_hnsw_unsupported"


@dataclass(frozen=True)
class Candidate:
    """One hit of a single branch, already filtered by tenant/version, with its 1-based rank."""

    chunk_id: uuid.UUID
    audio_id: uuid.UUID
    rank: int
    score: float


@dataclass(frozen=True)
class FusedHit:
    """A fused result: the unit L2.4 turns into a `{"chunk_id", ..., "score_rrf"}` row."""

    chunk_id: uuid.UUID
    audio_id: uuid.UUID
    score_rrf: float


ContentKey = Callable[[uuid.UUID], Hashable]


def _common_conditions(
    user_id: uuid.UUID,
    version: str,
    *,
    subject_id: uuid.UUID | None,
    date_from: date | None,
    date_to: date | None,
) -> list[ColumnElement[bool]]:
    """Tenant/version/tombstone filters shared by both branches, applied before the LIMIT.

    Single definition (same idea as `indexing._eligible_conditions`) so the two branches can
    never drift: either both exclude a tombstoned or stale chunk, or neither does.
    """
    conditions: list[ColumnElement[bool]] = [
        Chunk.user_id == user_id,
        Chunk.index_version == version,
        Audio.user_id == user_id,
        Audio.deleted_at.is_(None),
        Transcript.user_id == user_id,
        Transcript.version == Audio.active_transcript_version,
        Chunk.transcript_version == Transcript.version,
    ]
    if subject_id is not None:
        conditions.append(Audio.subject_id == subject_id)
    if date_from is not None:
        conditions.append(Audio.class_date >= date_from)
    if date_to is not None:
        conditions.append(Audio.class_date <= date_to)
    return conditions


def _audio_join() -> ColumnElement[bool]:
    return and_(Audio.user_id == Chunk.user_id, Audio.id == Chunk.audio_id)


def _transcript_join() -> ColumnElement[bool]:
    return and_(Transcript.user_id == Chunk.user_id, Transcript.id == Chunk.transcript_id)


def _tsquery(query_text: str) -> ColumnElement[Any]:
    """Per-document tsquery: the config comes from the transcript, never a global language."""
    config: InstrumentedAttribute[str] = Transcript.fts_config
    return func.websearch_to_tsquery(cast(config, REGCONFIG), query_text)


def lexical_candidates_statement(
    *,
    user_id: uuid.UUID,
    version: str,
    query_text: str,
    limit: int = CANDIDATES_PER_BRANCH,
    subject_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Select[tuple[uuid.UUID, uuid.UUID, float]]:
    """Filtered lexical candidates: `chunks.tsv @@ <query>` ordered by `ts_rank_cd`.

    The tenant/version filters and the `LIMIT` live in this single statement: the branch
    yields at most `limit` already-authorized candidates, ordered `score DESC, chunk_id`.
    """
    tsquery = _tsquery(query_text)
    score = func.ts_rank_cd(Chunk.tsv, tsquery).label("score")
    return (
        select(Chunk.id.label("chunk_id"), Chunk.audio_id, score)
        .join(Audio, _audio_join())
        .join(Transcript, _transcript_join())
        .where(
            Chunk.tsv.op("@@")(tsquery),
            *_common_conditions(
                user_id,
                version,
                subject_id=subject_id,
                date_from=date_from,
                date_to=date_to,
            ),
        )
        .order_by(score.desc(), Chunk.id.asc())
        .limit(limit)
    )


def vector_candidates_statement(
    *,
    user_id: uuid.UUID,
    version: str,
    query_vector: Sequence[float],
    limit: int = CANDIDATES_PER_BRANCH,
    subject_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Select[tuple[uuid.UUID, uuid.UUID, float]]:
    """Filtered cosine candidates: `embeddings.vector <=> :query_vector` with the same filters."""
    distance = Embedding.vector.cosine_distance(list(query_vector)).label("score")
    return (
        select(Chunk.id.label("chunk_id"), Chunk.audio_id, distance)
        .join(
            Embedding,
            and_(
                Embedding.user_id == Chunk.user_id,
                Embedding.chunk_id == Chunk.id,
                Embedding.index_version == Chunk.index_version,
            ),
        )
        .join(Audio, _audio_join())
        .join(Transcript, _transcript_join())
        .where(
            Embedding.user_id == user_id,
            Embedding.index_version == version,
            *_common_conditions(
                user_id,
                version,
                subject_id=subject_id,
                date_from=date_from,
                date_to=date_to,
            ),
        )
        .order_by(distance.asc(), Chunk.id.asc())
        .limit(limit)
    )


def rrf_fuse(rankings: Sequence[Sequence[Candidate]], *, k: int = RRF_K) -> list[FusedHit]:
    """Reciprocal Rank Fusion with a deterministic total order.

    `score_rrf(chunk) = sum over branches of 1 / (k + rank)`, rank 1-based. Same input →
    same output: the accumulation order is the argument order and the sort is
    `score_rrf DESC, chunk_id ASC`, so ties never depend on dict or hash order.
    """
    scores: dict[uuid.UUID, float] = {}
    audio_of: dict[uuid.UUID, uuid.UUID] = {}
    for ranking in rankings:
        for candidate in ranking:
            audio_of.setdefault(candidate.chunk_id, candidate.audio_id)
            scores[candidate.chunk_id] = scores.get(candidate.chunk_id, 0.0) + 1.0 / (
                k + candidate.rank
            )
    fused = [
        FusedHit(chunk_id=chunk_id, audio_id=audio_of[chunk_id], score_rrf=score)
        for chunk_id, score in scores.items()
    ]
    fused.sort(key=lambda hit: (-hit.score_rrf, str(hit.chunk_id)))
    return fused


def dedupe(
    hits: Sequence[FusedHit], *, content_key: ContentKey | None = None
) -> list[FusedHit]:
    """Collapse duplicate chunks and overlapping content, keeping the best-ranked survivor.

    `hits` is expected best-first (the output of `rrf_fuse`). With `content_key`, two chunks
    of the same class carrying the same normalized content collapse to one; overlapping
    chunks produced by the chunker are the common case.
    """
    seen_chunks: set[uuid.UUID] = set()
    seen_content: set[tuple[uuid.UUID, Hashable]] = set()
    survivors: list[FusedHit] = []
    for hit in hits:
        if hit.chunk_id in seen_chunks:
            continue
        seen_chunks.add(hit.chunk_id)
        if content_key is not None:
            key = (hit.audio_id, content_key(hit.chunk_id))
            if key in seen_content:
                continue
            seen_content.add(key)
        survivors.append(hit)
    return survivors


def diversify(
    hits: Sequence[FusedHit], *, max_per_audio: int = MAX_CHUNKS_PER_AUDIO
) -> list[FusedHit]:
    """Cap how many chunks one class contributes, without shortening the result set.

    Two deterministic passes: the first respects `max_per_audio`, the second appends the
    overflow in its original (score) order so a small corpus still fills `top_k`.
    """
    if max_per_audio <= 0:
        return list(hits)
    counts: dict[uuid.UUID, int] = {}
    capped: list[FusedHit] = []
    overflow: list[FusedHit] = []
    for hit in hits:
        taken = counts.get(hit.audio_id, 0)
        if taken < max_per_audio:
            counts[hit.audio_id] = taken + 1
            capped.append(hit)
        else:
            overflow.append(hit)
    return capped + overflow


def merge(
    rankings: Sequence[Sequence[Candidate]],
    *,
    k: int = RRF_K,
    top_k: int = DEFAULT_TOP_K,
    max_per_audio: int = MAX_CHUNKS_PER_AUDIO,
    content_key: ContentKey | None = None,
) -> list[FusedHit]:
    """Fuse, dedupe and diversify the branch rankings, then cut to `top_k` (last step)."""
    if top_k <= 0:
        return []
    fused = rrf_fuse(rankings, k=k)
    unique = dedupe(fused, content_key=content_key)
    diverse = diversify(unique, max_per_audio=max_per_audio)
    return diverse[:top_k]


async def _candidates(
    db: AsyncSession, statement: Select[tuple[uuid.UUID, uuid.UUID, float]]
) -> list[Candidate]:
    rows = (await db.execute(statement)).all()
    return [
        Candidate(
            chunk_id=row.chunk_id,
            audio_id=row.audio_id,
            rank=position,
            score=float(row.score),
        )
        for position, row in enumerate(rows, start=1)
    ]


async def _enable_iterative_scan(db: AsyncSession) -> None:
    """Turn on the filtered HNSW scan without poisoning the caller's transaction.

    `SET LOCAL` is transaction-scoped, so it is issued inside a **SAVEPOINT**: if the GUC does
    not exist (pgvector < 0.8) PostgreSQL raises, the savepoint is rolled back and the caller's
    transaction stays usable. The failure is re-raised as a typed domain error instead of a
    driver error, so L2.4 can map it to `500 search_hnsw_unsupported` (contract §3.9 fails
    closed rather than returning a short top-k).
    """
    try:
        async with db.begin_nested():
            await db.execute(ITERATIVE_SCAN_SQL)
    except DBAPIError as exc:
        raise SearchHnswUnsupported(
            "pgvector >= 0.8 es necesario para hnsw.iterative_scan.",
            details={"reason": "iterative_scan_unavailable"},
        ) from exc


def _normalize_content(text: str) -> str:
    """Whitespace/case-insensitive key so overlapping chunks collapse regardless of spacing."""
    return " ".join(text.split()).casefold()


async def _content_keys(
    db: AsyncSession, *, user_id: uuid.UUID, chunk_ids: set[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """Normalized content of the candidate chunks, for the dedupe step of the fusion.

    Read after both branches (their ids are already tenant/version filtered) and before the
    fusion, so dedupe of overlapping content works in production, not only in unit tests. The
    tenant predicate is repeated as defense in depth.
    """
    if not chunk_ids:
        return {}
    rows = (
        await db.execute(
            select(Chunk.id, Chunk.content).where(
                Chunk.user_id == user_id, Chunk.id.in_(chunk_ids)
            )
        )
    ).all()
    return {row.id: _normalize_content(row.content) for row in rows}


async def hybrid_search(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    query_text: str,
    query_vector: Sequence[float] | None,
    version: str | None = None,
    top_k: int = DEFAULT_TOP_K,
    candidates_per_branch: int = CANDIDATES_PER_BRANCH,
    subject_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[FusedHit]:
    """Run both filtered branches in one transaction and return the fused top-k.

    `version` is the active index generation; when omitted it is resolved with
    `indexing.active_generation`, the single source of truth, and a missing generation is
    `search_not_configured` (the caller has nothing to search). The question embedding is the
    caller's responsibility (L2.4/L2.5); this function never calls the provider.
    """
    text_query = query_text.strip()
    if not text_query:
        raise SearchInvalidQuery("La consulta está vacía.", details={"reason": "empty_query"})
    if query_vector is None:
        raise SearchVectorRequired(
            "La búsqueda híbrida necesita el vector de la consulta.",
            details={"reason": "missing_query_vector"},
        )
    resolved = version
    if resolved is None:
        generation = await active_generation(db, user_id)
        if generation is None:
            raise SearchNotConfigured(
                "No hay un índice activo para este usuario.",
                details={"reason": "no_active_generation"},
            )
        resolved = generation.version

    lexical = await _candidates(
        db,
        lexical_candidates_statement(
            user_id=user_id,
            version=resolved,
            query_text=text_query,
            limit=candidates_per_branch,
            subject_id=subject_id,
            date_from=date_from,
            date_to=date_to,
        ),
    )
    # Before the vector SELECT: the GUC is transaction-scoped and must cover that query. The
    # savepoint keeps a driver error (pgvector < 0.8) from aborting the caller's transaction.
    await _enable_iterative_scan(db)
    vector = await _candidates(
        db,
        vector_candidates_statement(
            user_id=user_id,
            version=resolved,
            query_vector=query_vector,
            limit=candidates_per_branch,
            subject_id=subject_id,
            date_from=date_from,
            date_to=date_to,
        ),
    )
    content = await _content_keys(
        db,
        user_id=user_id,
        chunk_ids={candidate.chunk_id for candidate in (*lexical, *vector)},
    )
    hits = merge(
        [lexical, vector],
        top_k=top_k,
        content_key=lambda chunk_id: content.get(chunk_id, f"missing:{chunk_id}"),
    )
    logger.debug(
        "search.hybrid version=%s lexical=%d vector=%d hits=%d",
        resolved,
        len(lexical),
        len(vector),
        len(hits),
    )
    return hits

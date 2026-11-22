"""Filtered hybrid retrieval: SQL branches, RRF and chunk hydration.

The caller supplies the query vector and the active index generation. `version` is
required. This module does not call an embedding provider and does not build snippets.
Pagination, cursors and snippets stay in `search`.
"""

import logging
import uuid
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import ColumnElement, Select, and_, func, literal_column, select, text, union_all
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Audio,
    Chunk,
    ChunkSegment,
    Embedding,
    Segment,
    Subject,
    Transcript,
)
from app.services.evidence import reliable_times

logger = logging.getLogger(__name__)

# Documented contract constants (plan §7, decisión 3). Changing them changes the retrieval
# contract, not a tuning knob: they are not read from the environment.
RRF_K = 60  # Cormack et al. RRF default; the k of `1 / (k + rank)`.
CANDIDATES_PER_BRANCH = 30  # ~30 per branch (S2.md §1.3, ESPECIFICACION M7).
DEFAULT_TOP_K = 8  # ~8 final chunks under the token budget (M7).
MAX_CHUNKS_PER_AUDIO = 2  # diversity cap per class (plan §7, decisión 4).

# `/search` contract (S2.md §1.3, DISENO §3.9, L2.4/A2.11). The page is intentionally
# smaller than the global pagination convention: Ctrl+K is a quick lookup and the fused
# window below is bounded.
DEFAULT_PAGE_SIZE = DEFAULT_TOP_K
MAX_PAGE_SIZE = 50
MAX_RESULTS_WINDOW = CANDIDATES_PER_BRANCH * 2  # up to 60 fused hits, no deep pagination
SNIPPET_RADIUS = 80
SNIPPET_MAX_CHARS = 240
QUERY_MAX_CHARS = 500
CURSOR_VERSION = 1
CURSOR_MAX_LENGTH = 512
# Human-readable cursor errors: the router publishes these in `details.fields.cursor`.
_CURSOR_INVALID = "cursor inválido o malformado"
_CURSOR_STALE = "el cursor pertenece a un índice anterior; reinicia la búsqueda"

# Per-transaction GUC: with filters active, HNSW must keep scanning until it has `LIMIT`
# candidates, otherwise the top-k comes back short (DISENO §2.5).
# `hnsw.ef_search` and `hnsw.max_scan_tuples` are deliberately left at the PostgreSQL defaults:
# choosing them is a capacity/latency decision that needs the F0 measurements and the G4 quality
# gate (L2.7/L2.8). Hard-coding a value now would be an invented limit (guardrail 10).
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


class SearchInvalidCursor(SearchError):
    code = "search_invalid_cursor"


class SearchInvalidRange(SearchError):
    code = "search_invalid_range"


class SearchProviderMismatch(SearchError):
    """The configured provider cannot read the active generation (configuration error)."""

    code = "search_provider_mismatch"


@dataclass(frozen=True)
class Candidate:
    """One hit of a single branch, already filtered by tenant/version, with its 1-based rank.

    `branch_score` is the branch-native value and is **not comparable across branches**: the
    lexical branch orders by `ts_rank_cd` (higher is better) and the vector branch orders by
    cosine distance (lower is better). RRF is derived from `rank` alone, so this field never
    influences the fusion; it is kept for diagnostics only.
    """

    chunk_id: uuid.UUID
    audio_id: uuid.UUID
    rank: int
    branch_score: float


@dataclass(frozen=True)
class FusedHit:
    """A fused result: the unit L2.4 turns into a `{"chunk_id", ..., "score_rrf"}` row."""

    chunk_id: uuid.UUID
    audio_id: uuid.UUID
    score_rrf: float
@dataclass(frozen=True)
class RetrievedChunk:
    """Hydrated hit: chat reads `content`; `/search` serializes only `snippet`."""

    chunk_id: uuid.UUID
    audio_id: uuid.UUID
    subject_id: uuid.UUID
    subject_name: str
    subject_color: str
    class_date: date
    content: str
    snippet: str
    segment_id: uuid.UUID | None
    t_start: float | None
    t_end: float | None
    score_rrf: float
    # Chat needs the durable source identity to persist `message_sources` and the citation
    # event. They are optional only for backwards-compatible unit-test construction; SQL
    # hydration always fills them.
    transcript_id: uuid.UUID | None = None
    transcript_version: int | None = None
    segment_ordinal: int | None = None
ContentKey = Callable[[uuid.UUID], Hashable]

def _validated_top_k(top_k: int) -> None:
    if top_k < 1 or top_k > MAX_RESULTS_WINDOW:
        raise SearchInvalidQuery(
            "El número de resultados está fuera de rango.",
            details={"fields": {"limit": "limit fuera de rango"}},
        )


def _common_conditions(
    user_id: uuid.UUID,
    version: str,
    *,
    subject_id: uuid.UUID | None,
    audio_id: uuid.UUID | None = None,
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
        Subject.user_id == user_id,
        Subject.deleted_at.is_(None),
        Transcript.user_id == user_id,
        Transcript.version == Audio.active_transcript_version,
        Chunk.transcript_version == Transcript.version,
    ]
    if subject_id is not None:
        conditions.append(Audio.subject_id == subject_id)
    if audio_id is not None:
        # Chat mode `class`: the filter must run inside both branches before their LIMIT.
        conditions.append(Audio.id == audio_id)
    if date_from is not None:
        conditions.append(Audio.class_date >= date_from)
    if date_to is not None:
        conditions.append(Audio.class_date <= date_to)
    return conditions


def _audio_join() -> ColumnElement[bool]:
    return and_(Audio.user_id == Chunk.user_id, Audio.id == Chunk.audio_id)


def _subject_join() -> ColumnElement[bool]:
    return and_(Subject.user_id == Audio.user_id, Subject.id == Audio.subject_id)


def _transcript_join() -> ColumnElement[bool]:
    return and_(Transcript.user_id == Chunk.user_id, Transcript.id == Chunk.transcript_id)


# All known `fts_config` values (must match the CHECK constraint in 0002_s2_schema.py).
# Using a fixed list of string literals lets the planner use chunks_fts (GIN): when the
# config is a column expression the `@@` predicate cannot be indexed.
_FTS_CONFIGS: tuple[str, ...] = ("spanish", "english", "simple")


def lexical_candidates_statement(
    *,
    user_id: uuid.UUID,
    version: str,
    query_text: str,
    limit: int = CANDIDATES_PER_BRANCH,
    subject_id: uuid.UUID | None = None,
    audio_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Select[tuple[uuid.UUID, uuid.UUID, float]]:
    """Filtered lexical candidates: per-language UNION ALL ordered by `ts_rank_cd`.

    Each arm of the UNION uses a *constant* `::regconfig` literal so the planner can apply
    the `chunks_fts` GIN index for that arm's `@@` predicate.  The arm also filters
    `transcripts.fts_config = '<lang>'` so a Spanish query is never matched against an
    English tsvector and vice versa.  The results are merged, re-ranked by score and cut to
    `limit` in an outer SELECT.

    The per-language split is closed over the fixed `_FTS_CONFIGS` tuple; it cannot be
    influenced by user input (no injection surface).
    """
    common = _common_conditions(
        user_id,
        version,
        subject_id=subject_id,
        audio_id=audio_id,
        date_from=date_from,
        date_to=date_to,
    )
    arms: list[Select[tuple[uuid.UUID, uuid.UUID, float]]] = []
    for cfg in _FTS_CONFIGS:
        regconfig: ColumnElement[Any] = literal_column(f"'{cfg}'::regconfig")
        tsquery = func.websearch_to_tsquery(regconfig, query_text)
        score = func.ts_rank_cd(Chunk.tsv, tsquery).label("score")
        arm = (
            select(Chunk.id.label("chunk_id"), Chunk.audio_id, score)
            .join(Audio, _audio_join())
            .join(Subject, _subject_join())
            .join(Transcript, _transcript_join())
            .where(
                Transcript.fts_config == cfg,
                Chunk.tsv.op("@@")(tsquery),
                *common,
            )
        )
        arms.append(arm)
    combined = union_all(*arms).subquery()
    return (
        select(
            combined.c.chunk_id,
            combined.c.audio_id,
            combined.c.score,
        )
        .order_by(combined.c.score.desc(), combined.c.chunk_id.asc())
        .limit(limit)
    )


def vector_candidates_statement(
    *,
    user_id: uuid.UUID,
    version: str,
    query_vector: Sequence[float],
    limit: int = CANDIDATES_PER_BRANCH,
    subject_id: uuid.UUID | None = None,
    audio_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Select[tuple[uuid.UUID, uuid.UUID, float]]:
    """Filtered cosine candidates: `embeddings.vector <=> :query_vector` with the same filters.

    Only `distance ASC` in ORDER BY: a secondary sort key prevents the planner from using the
    HNSW index (it expects a pure `<=>` order).  The `chunk_id` tiebreak happens in Python
    inside `rrf_fuse`, which already sorts by `(-score_rrf, str(chunk_id))`.
    """
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
        .join(Subject, _subject_join())
        .join(Transcript, _transcript_join())
        .where(
            Embedding.user_id == user_id,
            Embedding.index_version == version,
            *_common_conditions(
                user_id,
                version,
                subject_id=subject_id,
                audio_id=audio_id,
                date_from=date_from,
                date_to=date_to,
            ),
        )
        .order_by(distance.asc())
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


def dedupe(hits: Sequence[FusedHit], *, content_key: ContentKey | None = None) -> list[FusedHit]:
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
    """Promote diversity with a **soft** cap per class, never shortening the result set.

    Contract: the first pass front-loads at most `max_per_audio` chunks of each class; a second
    pass appends the overflow in its original (RRF) order. So the cap is *not* a hard limit on
    the returned list — it only guarantees that, while other classes still have candidates, no
    class occupies more than `max_per_audio` of the front. Truncating the overflow instead would
    let `merge` return fewer than `top_k` whenever one class dominates the corpus, which is the
    case the plan explicitly avoids (plan §7, decisión 4). If L2.7/G4 later needs a hard cap,
    drop the overflow pass rather than slicing here.
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
            branch_score=float(row.score),
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
            select(Chunk.id, Chunk.content).where(Chunk.user_id == user_id, Chunk.id.in_(chunk_ids))
        )
    ).all()
    return {row.id: _normalize_content(row.content) for row in rows}


async def hybrid_search(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    query_text: str,
    query_vector: Sequence[float] | None,
    version: str,
    top_k: int = DEFAULT_TOP_K,
    candidates_per_branch: int = CANDIDATES_PER_BRANCH,
    subject_id: uuid.UUID | None = None,
    audio_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[FusedHit]:
    """Run both filtered branches in one transaction and return the fused top-k.

    `version` is the index generation to search. The caller resolves the active generation
    and embeds the question; this function never calls the provider and never opens a
    second transaction to look the generation up.
    """
    text_query = query_text.strip()
    if not text_query:
        raise SearchInvalidQuery("La consulta está vacía.", details={"reason": "empty_query"})
    # A degenerate (empty) vector would reach PostgreSQL as an uncastable bind and surface as
    # an untyped 500; the documented typed error for "no usable vector" is 400 (plan §2.5).
    if not query_vector:
        raise SearchVectorRequired(
            "La búsqueda híbrida necesita el vector de la consulta.",
            details={"reason": "missing_query_vector"},
        )
    lexical = await _candidates(
        db,
        lexical_candidates_statement(
            user_id=user_id,
            version=version,
            query_text=text_query,
            limit=candidates_per_branch,
            subject_id=subject_id,
            audio_id=audio_id,
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
            version=version,
            query_vector=query_vector,
            limit=candidates_per_branch,
            subject_id=subject_id,
            audio_id=audio_id,
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
        version,
        len(lexical),
        len(vector),
        len(hits),
    )
    return hits

async def _hydrate(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    query: str,
    version: str,
    hits: Sequence[FusedHit],
    audio_id: uuid.UUID | None = None,
) -> list[RetrievedChunk]:
    """Hydrate fused hits in RRF order with tenant/version checks repeated in SQL."""
    if not hits:
        return []
    statement = (
        select(
            Chunk.id.label("chunk_id"),
            Chunk.audio_id,
            Chunk.transcript_id,
            Chunk.transcript_version,
            Chunk.content,
            Chunk.char_start.label("chunk_char_start"),
            Chunk.char_end.label("chunk_char_end"),
            Audio.subject_id,
            Audio.class_date,
            Audio.duration_seconds,
            Subject.name.label("subject_name"),
            Subject.color.label("subject_color"),
            Transcript.timestamp_precision,
            Segment.id.label("segment_id"),
            Segment.ordinal.label("segment_ordinal"),
            Segment.char_start.label("segment_char_start"),
            Segment.char_end.label("segment_char_end"),
            Segment.t_start.label("segment_t_start"),
            Segment.t_end.label("segment_t_end"),
        )
        .select_from(Chunk)
        .join(Audio, and_(Audio.user_id == Chunk.user_id, Audio.id == Chunk.audio_id))
        .join(Subject, _subject_join())
        .join(
            Transcript,
            and_(Transcript.user_id == Chunk.user_id, Transcript.id == Chunk.transcript_id),
        )
        .outerjoin(
            ChunkSegment,
            and_(ChunkSegment.user_id == Chunk.user_id, ChunkSegment.chunk_id == Chunk.id),
        )
        .outerjoin(
            Segment,
            and_(
                Segment.user_id == ChunkSegment.user_id,
                Segment.id == ChunkSegment.segment_id,
                Segment.transcript_id == Chunk.transcript_id,
            ),
        )
        .where(
            Chunk.user_id == user_id,
            Chunk.id.in_([hit.chunk_id for hit in hits]),
            Chunk.index_version == version,
            Chunk.transcript_version == Transcript.version,
            Audio.user_id == user_id,
            Audio.deleted_at.is_(None),
            Subject.user_id == user_id,
            Subject.deleted_at.is_(None),
            Transcript.user_id == user_id,
            Transcript.version == Audio.active_transcript_version,
        )
    )
    if audio_id is not None:
        statement = statement.where(Chunk.audio_id == audio_id)
    rows = (await db.execute(statement)).all()
    metadata: dict[uuid.UUID, Any] = {}
    segments: dict[uuid.UUID, tuple[tuple[int, int], Any]] = {}
    for row in rows:
        chunk_id = row.chunk_id
        metadata.setdefault(chunk_id, row)
        if row.segment_id is None:
            continue
        overlap = min(row.chunk_char_end, row.segment_char_end) - max(
            row.chunk_char_start, row.segment_char_start
        )
        if overlap <= 0:
            continue
        rank = (overlap, -int(row.segment_ordinal))
        current = segments.get(chunk_id)
        if current is None or rank > current[0]:
            segments[chunk_id] = (rank, row)

    hydrated: list[RetrievedChunk] = []
    for hit in hits:
        hydrated_row = metadata.get(hit.chunk_id)
        if hydrated_row is None:
            # Tombstoned or reindexed between retrieval and hydration: drop, never expose.
            continue
        selected = segments.get(hit.chunk_id)
        segment = selected[1] if selected is not None else None
        segment_id = segment.segment_id if segment is not None else None
        t_start, t_end = reliable_times(
            timestamp_precision=hydrated_row.timestamp_precision,
            t_start=segment.segment_t_start if segment is not None else None,
            t_end=segment.segment_t_end if segment is not None else None,
            duration_seconds=hydrated_row.duration_seconds,
        )
        hydrated.append(
            RetrievedChunk(
                chunk_id=hydrated_row.chunk_id,
                audio_id=hydrated_row.audio_id,
                subject_id=hydrated_row.subject_id,
                subject_name=hydrated_row.subject_name,
                subject_color=hydrated_row.subject_color,
                class_date=hydrated_row.class_date,
                content=hydrated_row.content,
                snippet="",
                segment_id=segment_id,
                t_start=t_start,
                t_end=t_end,
                score_rrf=hit.score_rrf,
                transcript_id=hydrated_row.transcript_id,
                transcript_version=hydrated_row.transcript_version,
                segment_ordinal=segment.segment_ordinal if segment is not None else None,
            )
        )
    return hydrated

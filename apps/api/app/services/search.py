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
  diversity soft-caps how many chunks a single class contributes to the front of the list.
  Only then is the list cut to `top_k`.
- **`iterative_scan` is enabled inside a SAVEPOINT.** An unsupported GUC (pgvector < 0.8)
  rolls the savepoint back and surfaces as `SearchHnswUnsupported`, never as a poisoned
  caller transaction.
- **Retrieved content is untrusted data.** Every `FusedHit` is a `chunk_id`/`audio_id`/score;
  the chunk text is transcript data and must never be interpreted as instructions by a later
  consumer (L2.5 prompt construction, L2.6 citation validation).
- **No content in logs.** Only counts, version and error codes; never `content`, the query
  text, vectors or the API key.

L2.4/A2.11 adds the reusable public `SearchService` (chat calls `retrieve`, the endpoint
calls `page`), hydration, plain-text snippets, honest segment times and an opaque keyset
cursor over the bounded RRF window. L2.6 adds `rehydrate_chunk_ids`/`rehydrate_source` so a
citation is reread with the same filters immediately before it is emitted. What it still
does NOT do (later tickets): the G4 metrics (L2.7) and the latency harness (L2.8). It never
writes: read-only retrieval.
"""

import base64
import binascii
import json
import logging
import math
import re
import uuid
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass
from datetime import date
from time import perf_counter
from typing import Any

from sqlalchemy import ColumnElement, Select, and_, func, literal_column, select, text, union_all
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Audio,
    Chunk,
    ChunkSegment,
    Embedding,
    IndexGeneration,
    Segment,
    Subject,
    Transcript,
)
from app.services.embeddings import (
    EmbeddingDimensionMismatch,
    EmbeddingNotConfigured,
    EmbeddingProvider,
    embed_query,
)
from app.services.evidence import reliable_times
from app.services.indexing import active_generation

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
class SearchCursor:
    """Opaque keyset position: generation, exact RRF score and `chunk_id` tiebreaker."""

    index_version: str
    score_rrf: float
    chunk_id: uuid.UUID
    version: int = CURSOR_VERSION


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


@dataclass(frozen=True)
class SearchPage:
    results: list[RetrievedChunk]
    took_ms: int
    next_cursor: str | None


@dataclass(frozen=True)
class QueryVectorTarget:
    """Metadata needed to embed a query without holding a database transaction.

    `prepare_query` reads only this generation metadata; the caller closes the session,
    calls the embedding provider, and then opens a new session for `retrieve_with_vector`.
    The target deliberately carries no page, chunk or transcript data.
    """

    query_text: str
    model: str
    dimension: int


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
    version: str | None = None,
    top_k: int = DEFAULT_TOP_K,
    candidates_per_branch: int = CANDIDATES_PER_BRANCH,
    subject_id: uuid.UUID | None = None,
    audio_id: uuid.UUID | None = None,
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
    # A degenerate (empty) vector would reach PostgreSQL as an uncastable bind and surface as
    # an untyped 500; the documented typed error for "no usable vector" is 400 (plan §2.5).
    if not query_vector:
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
        if len(query_vector) != generation.dimension:
            raise SearchInvalidQuery(
                "Dimensión de vector inválida.",
                details={
                    "reason": "bad_dim",
                    "expected": generation.dimension,
                    "got": len(query_vector),
                },
            )

    lexical = await _candidates(
        db,
        lexical_candidates_statement(
            user_id=user_id,
            version=resolved,
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
            version=resolved,
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
        resolved,
        len(lexical),
        len(vector),
        len(hits),
    )
    return hits


# --- L2.4 / A2.11: public service, cursor pagination, snippet and hydration ---------------


def encode_cursor(cursor: SearchCursor) -> str:
    """Opaque base64url cursor; `float.hex()` preserves the exact RRF score.

    The cursor is not a secret and grants no access: every query still filters by `user_id`.
    It only records where the previous page stopped.
    """
    payload = {
        "v": cursor.version,
        "g": cursor.index_version,
        "s": cursor.score_rrf.hex(),
        "c": str(cursor.chunk_id),
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    if len(encoded) > CURSOR_MAX_LENGTH:
        raise SearchInvalidCursor(
            "El cursor excede la longitud máxima.",
            details={"fields": {"cursor": _CURSOR_INVALID}},
        )
    return encoded


def decode_cursor(value: str) -> SearchCursor:
    """Validate and decode an opaque cursor; any malformed value is a typed 422."""
    invalid = SearchInvalidCursor(
        _CURSOR_INVALID,
        details={"fields": {"cursor": _CURSOR_INVALID}},
    )
    if not value or len(value) > CURSOR_MAX_LENGTH:
        raise invalid
    try:
        padded = value + "=" * (-len(value) % 4)
        raw = base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
        payload: Any = json.loads(raw.decode("utf-8"))
    except (binascii.Error, UnicodeEncodeError, UnicodeDecodeError, ValueError) as exc:
        raise invalid from exc
    if not isinstance(payload, dict) or payload.get("v") != CURSOR_VERSION:
        raise invalid
    index_version = payload.get("g")
    score_hex = payload.get("s")
    chunk_raw = payload.get("c")
    if not isinstance(index_version, str) or not index_version:
        raise invalid
    if not isinstance(score_hex, str) or not isinstance(chunk_raw, str):
        raise invalid
    try:
        score = float.fromhex(score_hex)
    except ValueError as exc:
        raise invalid from exc
    if not math.isfinite(score) or score < 0:
        raise invalid
    try:
        chunk_id = uuid.UUID(chunk_raw)
    except ValueError as exc:
        raise invalid from exc
    return SearchCursor(
        index_version=index_version,
        score_rrf=score,
        chunk_id=chunk_id,
    )


def after_cursor(hit: FusedHit | RetrievedChunk, cursor: SearchCursor) -> bool:
    """Keyset comparison over the total order `score_rrf DESC, chunk_id ASC`."""
    if hit.score_rrf != cursor.score_rrf:
        return hit.score_rrf < cursor.score_rrf
    return str(hit.chunk_id) > str(cursor.chunk_id)


def _query_terms(query: str) -> list[str]:
    terms = {match.group(0) for match in re.finditer(r"\w+", query, re.UNICODE)}
    # Longest term first: it is the most specific literal context available without a
    # stemming-aware highlighter (`ts_headline` is a future alternative, L2.7/G4).
    return sorted(terms, key=lambda term: (-len(term), term))


def _word_start(text: str, index: int) -> int:
    if index <= 0 or index >= len(text) or text[index - 1].isspace():
        return index
    space = text.find(" ", index)
    return len(text) if space == -1 else space + 1


def _word_end(text: str, index: int) -> int:
    if index <= 0 or index >= len(text) or text[index].isspace():
        return index
    space = text.rfind(" ", 0, index)
    return 0 if space == -1 else space


def _window(text: str, *, match_start: int, match_end: int) -> tuple[int, int]:
    start = max(0, match_start - SNIPPET_RADIUS)
    end = min(len(text), match_end + SNIPPET_RADIUS)
    start = _word_start(text, start)
    end = _word_end(text, end)
    if start > match_start:
        start = match_start
    if end < match_end:
        end = match_end
    if end - start <= SNIPPET_MAX_CHARS:
        return start, end

    # Keep the match visible while respecting the hard 240-character ceiling. The final
    # ellipsis is added by the caller, so this budget is conservative.
    budget = SNIPPET_MAX_CHARS - 2
    match_length = match_end - match_start
    if match_length >= budget:
        start = match_start
        end = min(len(text), start + budget)
    else:
        padding = (budget - match_length) // 2
        start = max(0, match_start - padding)
        end = min(len(text), start + budget)
        if end == len(text):
            start = max(0, end - budget)
        start = _word_start(text, start)
        end = _word_end(text, end)
        if start > match_start:
            start = match_start
        if end < match_end:
            end = match_end
    return start, end


def make_snippet(content: str, query: str) -> str:
    """Plain-text context around the longest literal query term, bounded to 240 chars.

    The snippet is not HTML: the React client escapes by default. If the lexical branch
    matched only through stemming or accents, there is no literal match and the snippet
    falls back to the start of the chunk instead of inventing a highlight.
    """
    text = " ".join(content.split())
    if not text:
        return ""
    match: re.Match[str] | None = None
    for term in _query_terms(query):
        match = re.search(re.escape(term), text, re.IGNORECASE)
        if match is not None:
            break
    if match is None:
        end = min(len(text), SNIPPET_MAX_CHARS)
        return text[:end] + ("…" if end < len(text) else "")
    start, end = _window(text, match_start=match.start(), match_end=match.end())
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    snippet = prefix + text[start:end] + suffix
    if len(snippet) > SNIPPET_MAX_CHARS:
        keep = max(0, SNIPPET_MAX_CHARS - len(prefix) - 1)
        snippet = prefix + text[start : start + keep] + "…"
    return snippet


def _reliable_times(
    *,
    precision: str,
    t_start: float | None,
    t_end: float | None,
    duration_seconds: float | None,
) -> tuple[float | None, float | None]:
    """Return the segment times only when they are truthful; otherwise both are null."""
    return reliable_times(
        timestamp_precision=precision,
        t_start=t_start,
        t_end=t_end,
        duration_seconds=duration_seconds,
    )


def _validated_query(query: str, *, max_chars: int = QUERY_MAX_CHARS) -> str:
    text_query = query.strip()
    if not text_query or len(text_query) > max_chars:
        raise SearchInvalidQuery(
            "La consulta está vacía o excede el máximo.",
            details={"fields": {"q": "consulta inválida"}},
        )
    if any(ord(char) < 32 and char not in "\t\n\r" for char in text_query) or "\x7f" in text_query:
        raise SearchInvalidQuery(
            "La consulta contiene caracteres de control.",
            details={"fields": {"q": "consulta inválida"}},
        )
    return text_query


def _validated_range(date_from: date | None, date_to: date | None) -> None:
    if date_from is not None and date_to is not None and date_from > date_to:
        raise SearchInvalidRange(
            "El rango de fechas está invertido.",
            details={"fields": {"from": "from debe ser menor o igual que to"}},
        )


def _elapsed_ms(started: float) -> int:
    return max(0, int((perf_counter() - started) * 1000))


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
        .join(Subject, and_(Subject.user_id == Audio.user_id, Subject.id == Audio.subject_id))
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
        t_start, t_end = _reliable_times(
            precision=hydrated_row.timestamp_precision,
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
                snippet=make_snippet(hydrated_row.content, query),
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


class SearchService:
    """Reusable retrieval service: chat calls `retrieve`, `/search` calls `page`."""

    def __init__(self, db: AsyncSession, *, provider: EmbeddingProvider | None) -> None:
        self._db = db
        self._provider = provider

    async def retrieve(
        self,
        *,
        user_id: uuid.UUID,
        query: str,
        subject_id: uuid.UUID | None = None,
        audio_id: uuid.UUID | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        top_k: int = DEFAULT_TOP_K,
        max_query_chars: int = QUERY_MAX_CHARS,
    ) -> list[RetrievedChunk]:
        """Return hydrated hits for chat; no active generation is an empty result.

        `audio_id` is chat's `class` mode filter; it is applied inside both retrieval branches
        before their LIMIT, never as a post-filter over the top-k. `max_query_chars` lets chat
        accept its own question limit while `/search` keeps the frozen 500-character contract.
        """
        query_text = _validated_query(query, max_chars=max_query_chars)
        _validated_range(date_from, date_to)
        _validated_top_k(top_k)
        generation = await active_generation(self._db, user_id)
        if generation is None:
            return []
        results = await self._retrieve_for_generation(
            generation,
            user_id=user_id,
            query_text=query_text,
            subject_id=subject_id,
            audio_id=audio_id,
            date_from=date_from,
            date_to=date_to,
            top_k=top_k,
        )
        logger.debug("search.retrieve version=%s hits=%d", generation.version, len(results))
        return results

    async def prepare_query(
        self,
        *,
        user_id: uuid.UUID,
        query: str,
        max_query_chars: int = QUERY_MAX_CHARS,
    ) -> QueryVectorTarget | None:
        """Validate a query and return the generation metadata needed to embed it.

        This method performs no network call. The caller must close the database session
        before invoking the embedding provider; the returned target is valid for
        `retrieve_with_vector`, which re-reads the active generation in its own session.
        """
        query_text = _validated_query(query, max_chars=max_query_chars)
        generation = await active_generation(self._db, user_id)
        if generation is None:
            return None
        self._require_provider(generation)
        return QueryVectorTarget(
            query_text=query_text,
            model=generation.model,
            dimension=generation.dimension,
        )

    async def retrieve_with_vector(
        self,
        *,
        user_id: uuid.UUID,
        query_text: str,
        query_vector: Sequence[float],
        subject_id: uuid.UUID | None = None,
        audio_id: uuid.UUID | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        top_k: int = DEFAULT_TOP_K,
        max_query_chars: int = QUERY_MAX_CHARS,
    ) -> list[RetrievedChunk]:
        """Run retrieval with a vector embedded outside the current database session.

        The active generation is read again here, so a generation retired meanwhile produces
        an empty result instead of a stale read. The provider model/dimension and the vector
        length are still validated before SQL executes.
        """
        text_query = _validated_query(query_text, max_chars=max_query_chars)
        _validated_range(date_from, date_to)
        _validated_top_k(top_k)
        generation = await active_generation(self._db, user_id)
        if generation is None:
            return []
        self._require_provider(generation)
        if len(query_vector) != generation.dimension:
            raise EmbeddingDimensionMismatch(
                "El proveedor devolvió un vector de otra dimensión.",
                details={"expected": generation.dimension, "observed": len(query_vector)},
            )
        hits = await hybrid_search(
            self._db,
            user_id=user_id,
            query_text=text_query,
            query_vector=query_vector,
            version=generation.version,
            top_k=top_k,
            candidates_per_branch=CANDIDATES_PER_BRANCH,
            subject_id=subject_id,
            audio_id=audio_id,
            date_from=date_from,
            date_to=date_to,
        )
        results = await _hydrate(
            self._db,
            user_id=user_id,
            query=text_query,
            version=generation.version,
            hits=hits,
        )
        logger.debug(
            "search.retrieve_precomputed version=%s hits=%d", generation.version, len(results)
        )
        return results

    async def rehydrate_chunk_ids(
        self,
        *,
        user_id: uuid.UUID,
        chunk_id: uuid.UUID,
        audio_id: uuid.UUID,
        query: str = "",
        score_rrf: float = 0.0,
    ) -> RetrievedChunk | None:
        """Re-read one chunk with the same filters retrieval applies before the top-k.

        Citation validation calls this immediately before exposing a source. A chunk that
        was tombstoned, reindexed or moved to a stale transcript version after retrieval must
        not become a citation, so this path fails closed with `None`.
        """
        generation = await active_generation(self._db, user_id)
        if generation is None:
            return None
        hydrated = await _hydrate(
            self._db,
            user_id=user_id,
            query=query,
            version=generation.version,
            hits=[FusedHit(chunk_id=chunk_id, audio_id=audio_id, score_rrf=score_rrf)],
            audio_id=audio_id,
        )
        return hydrated[0] if hydrated else None

    async def rehydrate_source(
        self, *, user_id: uuid.UUID, source: RetrievedChunk
    ) -> RetrievedChunk | None:
        """Re-read a previously hydrated source; `None` means it is no longer authorized."""
        return await self.rehydrate_chunk_ids(
            user_id=user_id,
            chunk_id=source.chunk_id,
            audio_id=source.audio_id,
            # `query` only feeds make_snippet; reusing the old snippet would recompute a
            # snippet nobody reads.
            query="",
            score_rrf=source.score_rrf,
        )

    async def page(
        self,
        *,
        user_id: uuid.UUID,
        query: str,
        subject_id: uuid.UUID | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> SearchPage:
        """Paginated `/search` window: keyset over the bounded RRF window."""
        started = perf_counter()
        query_text = _validated_query(query)
        _validated_range(date_from, date_to)
        if limit < 1 or limit > MAX_PAGE_SIZE:
            raise SearchInvalidQuery(
                "El tamaño de página está fuera de rango.",
                details={"fields": {"limit": "limit fuera de rango"}},
            )
        cursor_state = decode_cursor(cursor) if cursor is not None else None
        generation = await active_generation(self._db, user_id)
        if generation is None:
            took_ms = _elapsed_ms(started)
            logger.info("search.page version=- results=0 took_ms=%d", took_ms)
            return SearchPage(results=[], took_ms=took_ms, next_cursor=None)
        if cursor_state is not None and cursor_state.index_version != generation.version:
            raise SearchInvalidCursor(
                _CURSOR_STALE,
                details={
                    "reason": "stale_generation",
                    "fields": {"cursor": _CURSOR_STALE},
                },
            )
        results = await self._retrieve_for_generation(
            generation,
            user_id=user_id,
            query_text=query_text,
            subject_id=subject_id,
            date_from=date_from,
            date_to=date_to,
            top_k=MAX_RESULTS_WINDOW,
        )
        if cursor_state is not None:
            results = [hit for hit in results if after_cursor(hit, cursor_state)]
        page_results = results[:limit]
        next_cursor = None
        if len(results) > limit:
            last = page_results[-1]
            next_cursor = encode_cursor(
                SearchCursor(
                    index_version=generation.version,
                    score_rrf=last.score_rrf,
                    chunk_id=last.chunk_id,
                )
            )
        took_ms = _elapsed_ms(started)
        logger.info(
            "search.page version=%s results=%d took_ms=%d",
            generation.version,
            len(page_results),
            took_ms,
        )
        return SearchPage(results=page_results, took_ms=took_ms, next_cursor=next_cursor)

    def _require_provider(self, generation: IndexGeneration) -> EmbeddingProvider:
        """Validate the configured provider against the active generation, fail closed."""
        provider = self._provider
        if provider is None:
            raise EmbeddingNotConfigured(
                "Falta configurar el proveedor de embeddings (MAULWURF_EMBEDDING_*)."
            )
        if provider.model != generation.model:
            raise SearchProviderMismatch(
                "El modelo del proveedor no coincide con la generación activa.",
                details={"reason": "model_mismatch"},
            )
        if provider.dimension != generation.dimension:
            raise SearchProviderMismatch(
                "La dimensión del proveedor no coincide con la generación activa.",
                details={"reason": "dimension_mismatch"},
            )
        return provider

    async def _retrieve_for_generation(
        self,
        generation: IndexGeneration,
        *,
        user_id: uuid.UUID,
        query_text: str,
        subject_id: uuid.UUID | None,
        date_from: date | None,
        date_to: date | None,
        top_k: int,
        audio_id: uuid.UUID | None = None,
    ) -> list[RetrievedChunk]:
        provider = self._require_provider(generation)
        vector = await embed_query(provider, query_text)
        if len(vector) != generation.dimension:
            raise EmbeddingDimensionMismatch(
                "El proveedor devolvió un vector de otra dimensión.",
                details={"expected": generation.dimension, "observed": len(vector)},
            )
        hits = await hybrid_search(
            self._db,
            user_id=user_id,
            query_text=query_text,
            query_vector=vector,
            version=generation.version,
            top_k=top_k,
            candidates_per_branch=CANDIDATES_PER_BRANCH,
            subject_id=subject_id,
            audio_id=audio_id,
            date_from=date_from,
            date_to=date_to,
        )
        return await _hydrate(
            self._db,
            user_id=user_id,
            query=query_text,
            version=generation.version,
            hits=hits,
        )

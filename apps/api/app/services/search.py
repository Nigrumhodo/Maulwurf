"""Paginated `/search`: cursors, snippets and the session split around embedding.

Filtered SQL, RRF and hydration live in `retrieval`. This module turns a fused window
into pages. The query embedding runs only while no database session is open.
"""

import base64
import binascii
import json
import logging
import math
import re
import uuid
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, replace
from datetime import date
from time import perf_counter
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import IndexGeneration
from app.services.embeddings import (
    EmbeddingDimensionMismatch,
    EmbeddingNotConfigured,
    EmbeddingProvider,
    embed_query,
)
from app.services.indexing import active_generation
from app.services.retrieval import (
    _CURSOR_INVALID,
    _CURSOR_STALE,
    CANDIDATES_PER_BRANCH,
    CURSOR_MAX_LENGTH,
    CURSOR_VERSION,
    DEFAULT_PAGE_SIZE,
    DEFAULT_TOP_K,
    MAX_PAGE_SIZE,
    MAX_RESULTS_WINDOW,
    QUERY_MAX_CHARS,
    SNIPPET_MAX_CHARS,
    SNIPPET_RADIUS,
    FusedHit,
    RetrievedChunk,
    SearchInvalidCursor,
    SearchInvalidQuery,
    SearchInvalidRange,
    SearchProviderMismatch,
    _hydrate,
    _validated_top_k,
    hybrid_search,
)

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class SearchCursor:
    """Opaque keyset position: generation, exact RRF score and `chunk_id` tiebreaker."""

    index_version: str
    score_rrf: float
    chunk_id: uuid.UUID
    version: int = CURSOR_VERSION

@dataclass(frozen=True)
class SearchPage:
    results: list[RetrievedChunk]
    took_ms: int
    next_cursor: str | None


# Opens a short session and closes it. `async_sessionmaker` satisfies this; tests can
# pass a callable that yields an already-open transaction without closing it.
SessionOpener = Callable[[], AbstractAsyncContextManager[AsyncSession]]

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

def _checked_provider(
    provider: EmbeddingProvider | None, *, model: str, dimension: int
) -> EmbeddingProvider:
    """Fail closed when the configured provider does not match the active generation."""
    if provider is None:
        raise EmbeddingNotConfigured(
            "Falta configurar el proveedor de embeddings (MAULWURF_EMBEDDING_*)."
        )
    if provider.model != model:
        raise SearchProviderMismatch(
            "El modelo del proveedor no coincide con la generación activa.",
            details={"reason": "model_mismatch"},
        )
    if provider.dimension != dimension:
        raise SearchProviderMismatch(
            "La dimensión del proveedor no coincide con la generación activa.",
            details={"reason": "dimension_mismatch"},
        )
    return provider


def _reject_stale_cursor(cursor_state: SearchCursor | None, version: str) -> None:
    if cursor_state is not None and cursor_state.index_version != version:
        raise SearchInvalidCursor(
            _CURSOR_STALE,
            details={
                "reason": "stale_generation",
                "fields": {"cursor": _CURSOR_STALE},
            },
        )


def _empty_search_page(started: float) -> SearchPage:
    took_ms = _elapsed_ms(started)
    logger.info("search.page version=- results=0 took_ms=%d", took_ms)
    return SearchPage(results=[], took_ms=took_ms, next_cursor=None)


class SearchService:
    """Reusable retrieval service.

    Chat calls `retrieve_with_vector` with a vector it embedded before opening a session.
    `GET /search` calls `page`, which does the same split itself.
    """

    def __init__(self, db: AsyncSession, *, provider: EmbeddingProvider | None) -> None:
        self._db = db
        self._provider = provider

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

    @staticmethod
    async def page(
        sessions: SessionOpener,
        *,
        provider: EmbeddingProvider | None,
        user_id: uuid.UUID,
        query: str,
        subject_id: uuid.UUID | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> SearchPage:
        """Paginated `/search` window: keyset over the bounded RRF window.

        The active generation is read on a short session that is closed before
        `embed_query`. Retrieval then runs on a new session via `retrieve_with_vector`,
        so the embedding HTTP call never holds a pooled connection.
        """
        started = perf_counter()
        query_text = _validated_query(query)
        _validated_range(date_from, date_to)
        if limit < 1 or limit > MAX_PAGE_SIZE:
            raise SearchInvalidQuery(
                "El tamaño de página está fuera de rango.",
                details={"fields": {"limit": "limit fuera de rango"}},
            )
        cursor_state = decode_cursor(cursor) if cursor is not None else None
        async with sessions() as db:
            generation = await active_generation(db, user_id)
            if generation is None:
                return _empty_search_page(started)
            _reject_stale_cursor(cursor_state, generation.version)
            # Copy the match inputs before the session closes and expires the row.
            model = generation.model
            dimension = generation.dimension
        checked = _checked_provider(provider, model=model, dimension=dimension)
        vector = await embed_query(checked, query_text)

        async with sessions() as db:
            generation = await active_generation(db, user_id)
            if generation is None:
                return _empty_search_page(started)
            _reject_stale_cursor(cursor_state, generation.version)
            version = generation.version
            results = await SearchService(db, provider=checked).retrieve_with_vector(
                user_id=user_id,
                query_text=query_text,
                query_vector=vector,
                subject_id=subject_id,
                date_from=date_from,
                date_to=date_to,
                top_k=MAX_RESULTS_WINDOW,
            )
        if cursor_state is not None:
            results = [hit for hit in results if after_cursor(hit, cursor_state)]
        page_results = [
            replace(hit, snippet=make_snippet(hit.content, query_text)) for hit in results[:limit]
        ]
        next_cursor = None
        if len(results) > limit:
            last = page_results[-1]
            next_cursor = encode_cursor(
                SearchCursor(
                    index_version=version,
                    score_rrf=last.score_rrf,
                    chunk_id=last.chunk_id,
                )
            )
        took_ms = _elapsed_ms(started)
        logger.info(
            "search.page version=%s results=%d took_ms=%d",
            version,
            len(page_results),
            took_ms,
        )
        return SearchPage(results=page_results, took_ms=took_ms, next_cursor=next_cursor)

    def _require_provider(self, generation: IndexGeneration) -> EmbeddingProvider:
        """Validate the configured provider against the active generation, fail closed."""
        return _checked_provider(
            self._provider, model=generation.model, dimension=generation.dimension
        )

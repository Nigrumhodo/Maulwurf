"""U-S2-JL-06 support: cursor, snippet and page window without a database (L2.4/A2.11).

These tests isolate the pure parts that the real PostgreSQL suite (`I-S2-JL-06`) cannot
assert cheaply: exact cursor round-trip, malformed cursors, keyset tie-breaking, snippet
boundaries and the bounded page window. They do not replace the integration suite.
"""

import base64
import json
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import date
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import search
from app.services.embeddings import EmbeddingDimensionMismatch
from app.services.retrieval import (
    _CURSOR_INVALID,
    _CURSOR_STALE,
    CURSOR_MAX_LENGTH,
    MAX_PAGE_SIZE,
    MAX_RESULTS_WINDOW,
    SNIPPET_MAX_CHARS,
    FusedHit,
    RetrievedChunk,
    SearchInvalidCursor,
    SearchInvalidQuery,
)
from app.services.search import (
    SearchCursor,
    SearchService,
    after_cursor,
    decode_cursor,
    encode_cursor,
    make_snippet,
)

USER_ID = uuid.uuid4()
VERSION = "idx-1"


def _cursor(score: float = 1 / 61, chunk_suffix: int = 2) -> SearchCursor:
    return SearchCursor(
        index_version=VERSION,
        score_rrf=score,
        chunk_id=uuid.UUID(f"00000000-0000-4000-8000-{chunk_suffix:012d}"),
    )


def _hit(score: float, suffix: int) -> FusedHit:
    return FusedHit(
        chunk_id=uuid.UUID(f"00000000-0000-4000-8000-{suffix:012d}"),
        audio_id=uuid.uuid4(),
        score_rrf=score,
    )


def _retrieved(score: float, suffix: int) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.UUID(f"00000000-0000-4000-8000-{suffix:012d}"),
        audio_id=uuid.uuid4(),
        subject_id=uuid.uuid4(),
        subject_name="Calculus II",
        subject_color="#6366f1",
        class_date=date(2026, 9, 21),
        content=f"contenido {suffix}",
        snippet=f"contenido {suffix}",
        segment_id=None,
        t_start=None,
        t_end=None,
        score_rrf=score,
    )


# --- 1. Cursor round-trip ---------------------------------------------------------------


def test_cursor_round_trip_preserves_float_and_uuid() -> None:
    original = _cursor(score=1 / 61)

    decoded = decode_cursor(encode_cursor(original))

    assert decoded == original
    assert decoded.score_rrf == original.score_rrf
    assert decoded.index_version == VERSION
    assert decoded.version == 1


def _encoded(payload: Any) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


# --- 2. Malformed cursors ---------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "",
        "not-base64!",
        "a" * (CURSOR_MAX_LENGTH + 1),
        _encoded([]),
        _encoded({"v": 2, "g": VERSION, "s": (1 / 61).hex(), "c": str(uuid.uuid4())}),
        _encoded({"v": 1, "g": "", "s": (1 / 61).hex(), "c": str(uuid.uuid4())}),
        _encoded({"v": 1, "g": VERSION, "s": "nan", "c": str(uuid.uuid4())}),
        _encoded({"v": 1, "g": VERSION, "s": "-0x1p+0", "c": str(uuid.uuid4())}),
        _encoded({"v": 1, "g": VERSION, "s": "not-hex", "c": str(uuid.uuid4())}),
        _encoded({"v": 1, "g": VERSION, "s": (1 / 61).hex(), "c": "not-a-uuid"}),
    ],
)
def test_cursor_rejects_malformed_payloads(value: str) -> None:
    with pytest.raises(SearchInvalidCursor) as exc:
        decode_cursor(value)

    assert exc.value.message == _CURSOR_INVALID
    assert exc.value.details["fields"]["cursor"] == _CURSOR_INVALID


# --- 3. Keyset order --------------------------------------------------------------------


def test_keyset_filter_uses_score_then_chunk_id() -> None:
    cursor = _cursor(score=0.5, chunk_suffix=2)

    assert after_cursor(_hit(0.4, 99), cursor)
    assert not after_cursor(_hit(0.5, 1), cursor)
    assert not after_cursor(_hit(0.5, 2), cursor)
    assert after_cursor(_hit(0.5, 3), cursor)
    assert not after_cursor(_hit(0.6, 99), cursor)


# --- 4. Snippet -------------------------------------------------------------------------


def test_snippet_centers_match_and_adds_ellipses() -> None:
    content = "inicio " + ("relleno " * 40) + "trigonometric substitution" + (" cola" * 40)

    snippet = make_snippet(content, "trigonometric")

    assert "trigonometric" in snippet
    assert snippet.startswith("…")
    assert snippet.endswith("…")
    assert len(snippet) <= SNIPPET_MAX_CHARS


def test_snippet_handles_start_end_and_no_literal_match() -> None:
    start = make_snippet("trigonometric " + ("relleno " * 60), "trigonometric")
    end = make_snippet(("relleno " * 60) + "trigonometric", "trigonometric")
    fallback = make_snippet("La integral definida y el teorema fundamental", "integrar")

    assert start.startswith("trigonometric")
    assert start.endswith("…")
    assert end.endswith("trigonometric")
    assert end.startswith("…")
    assert fallback == "La integral definida y el teorema fundamental"


def test_snippet_is_bounded_plain_text_and_unicode_safe() -> None:
    content = "La función trigonométrica " + ("x" * 500)
    snippet = make_snippet(content, "función")

    assert "función" in snippet
    assert len(snippet) <= SNIPPET_MAX_CHARS
    assert "<b>" not in snippet
    assert snippet.count("…") <= 2


# --- 5. Page window ---------------------------------------------------------------------


class _Generation:
    version = VERSION
    model = "test-model"
    dimension = 1536


class _Provider:
    """Matches `_Generation` so the provider check passes and `embed` is countable."""

    model = "test-model"
    dimension = 1536
    batch_size = 1

    def __init__(self) -> None:
        self.calls = 0

    async def embed(
        self, texts: Sequence[str], *, input_type: str | None = None
    ) -> list[list[float]]:
        self.calls += 1
        return [[0.0] * self.dimension for _ in texts]


def _sessions() -> Any:
    @asynccontextmanager
    async def open_session() -> AsyncIterator[AsyncSession]:
        yield cast_session()

    return open_session()


def cast_session() -> AsyncSession:
    return AsyncSession()


class _PageDouble:
    def __init__(self, hits: list[RetrievedChunk]) -> None:
        self.hits = hits
        self.seen_top_k: int | None = None
        self.provider = _Provider()

    def install(self, monkeypatch: pytest.MonkeyPatch, generation: _Generation | None) -> None:
        async def active(_: AsyncSession, __: uuid.UUID) -> _Generation | None:
            return generation

        async def retrieve(service: SearchService, **kwargs: Any) -> list[RetrievedChunk]:
            del service
            self.seen_top_k = kwargs["top_k"]
            return list(self.hits)

        monkeypatch.setattr(search, "active_generation", active)
        monkeypatch.setattr(SearchService, "retrieve_with_vector", retrieve)

    async def page(self, **kwargs: Any) -> Any:
        return await SearchService.page(
            _sessions, provider=self.provider, user_id=USER_ID, **kwargs
        )


async def test_page_uses_full_window_and_sets_next_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hits = [_retrieved(1 / (61 + rank), rank) for rank in range(1, 6)]
    double = _PageDouble(hits)
    double.install(monkeypatch, _Generation())

    first = await double.page(query="integral", limit=2)
    assert [hit.chunk_id for hit in first.results] == [hit.chunk_id for hit in hits[:2]]
    assert first.next_cursor is not None
    assert double.seen_top_k == MAX_RESULTS_WINDOW
    assert double.provider.calls == 1

    second = await double.page(query="integral", limit=2, cursor=first.next_cursor)
    assert [hit.chunk_id for hit in second.results] == [hit.chunk_id for hit in hits[2:4]]
    assert second.next_cursor is not None

    third = await double.page(query="integral", limit=2, cursor=second.next_cursor)
    assert [hit.chunk_id for hit in third.results] == [hits[4].chunk_id]
    assert third.next_cursor is None


async def test_page_without_generation_is_empty_and_skips_retrieval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    double = _PageDouble([_retrieved(0.5, 1)])
    double.install(monkeypatch, None)

    page = await double.page(query="integral")

    assert page.results == []
    assert page.next_cursor is None
    assert double.seen_top_k is None
    assert double.provider.calls == 0


async def test_page_rejects_stale_generation_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    double = _PageDouble([_retrieved(0.5, 1)])
    double.install(monkeypatch, _Generation())
    stale = SearchCursor(
        index_version="idx-old",
        score_rrf=0.5,
        chunk_id=uuid.uuid4(),
    )

    with pytest.raises(SearchInvalidCursor) as exc:
        await double.page(query="integral", cursor=encode_cursor(stale))

    assert exc.value.message == _CURSOR_STALE
    assert exc.value.details["reason"] == "stale_generation"
    assert exc.value.details["fields"]["cursor"] == _CURSOR_STALE
    assert double.provider.calls == 0
    assert double.seen_top_k is None


async def test_page_rejects_out_of_range_limit() -> None:
    provider = _Provider()

    with pytest.raises(SearchInvalidQuery):
        await SearchService.page(
            _sessions,
            provider=provider,
            user_id=USER_ID,
            query="integral",
            limit=MAX_PAGE_SIZE + 1,
        )

    assert provider.calls == 0


async def test_page_accepts_the_router_maximum_page_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    double = _PageDouble([])
    double.install(monkeypatch, _Generation())

    page = await double.page(query="integral", limit=MAX_PAGE_SIZE)

    assert page.results == []
    assert double.seen_top_k == MAX_RESULTS_WINDOW


async def test_retrieve_with_vector_rejects_a_short_vector_before_sql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def active(_: AsyncSession, __: uuid.UUID) -> _Generation:
        return _Generation()

    monkeypatch.setattr(search, "active_generation", active)
    service = SearchService(cast_session(), provider=_Provider())

    with pytest.raises(EmbeddingDimensionMismatch):
        await service.retrieve_with_vector(
            user_id=USER_ID,
            query_text="integral",
            query_vector=[0.1, 0.2],
        )

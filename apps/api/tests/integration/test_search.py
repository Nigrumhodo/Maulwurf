"""I-S2-JL-06: `GET /search` hybrid retrieval against real PostgreSQL+pgvector (L2.4/A2.11).

The corpus is synthetic/redacted (invented text, no PII) and the embeddings provider is a
deterministic fake: no network and no real class content while D3-Texto is open. The suite
checks the HTTP contract, tenant isolation before the top-k, tombstone/version/generation
filters, reliable segment times, cursor pagination, the per-user Redis quota, provider
error mapping and the `hnsw.iterative_scan` capability of the pinned pgvector image.
"""

import logging
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, date, datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session
from app.core.providers import get_embedding_provider
from app.main import app
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
from app.services.embeddings import EmbeddingProviderError, EmbeddingRateLimited
from app.services.indexing import chunk_tsvector
from app.services.search import _CURSOR_INVALID, _CURSOR_STALE, SearchService
from tests.integration import actors

pytestmark = pytest.mark.integration

DIMENSION = 1536
QUERY_VECTOR = [1.0] + [0.0] * (DIMENSION - 1)


def _vector(first: float, second: float = 0.0) -> list[float]:
    values = [0.0] * DIMENSION
    values[0] = first
    values[1] = second
    return values


class FakeEmbeddingProvider:
    """Deterministic provider: one vector per input, no network."""

    model = "test-model"
    dimension = DIMENSION
    batch_size = 100

    def __init__(
        self,
        *,
        vector: list[float] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.vector = vector or QUERY_VECTOR
        self.error = error
        self.calls = 0

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return [list(self.vector) for _ in texts]


@pytest.fixture
def provider() -> FakeEmbeddingProvider:
    return FakeEmbeddingProvider()


@pytest.fixture
async def client(
    db_session: AsyncSession, provider: FakeEmbeddingProvider
) -> AsyncIterator[AsyncClient]:
    async def override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = override
    app.dependency_overrides[get_embedding_provider] = lambda: provider
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=settings.allowed_origin
    ) as c:
        yield c
    app.dependency_overrides.clear()


async def _make_generation(
    db: AsyncSession,
    actor: actors.Actor,
    *,
    version: str = "test-v1",
    status: str = "active",
) -> IndexGeneration:
    if status == "active":
        await db.execute(
            update(IndexGeneration)
            .where(
                IndexGeneration.user_id == actor.user_id,
                IndexGeneration.status == "active",
            )
            .values(status="retired")
            .execution_options(synchronize_session=False)
        )
    generation = IndexGeneration(
        user_id=actor.user_id,
        version=version,
        model="test-model",
        dimension=DIMENSION,
        status=status,
        chunk_count=0,
    )
    db.add(generation)
    await db.flush()
    return generation


async def _indexed_class(
    db: AsyncSession,
    actor: actors.Actor,
    *,
    generation: IndexGeneration,
    content: str,
    subject: Subject | None = None,
    class_date: date = date(2026, 9, 21),
    vector: list[float] | None = None,
    ordinal: int = 1,
    precision: str = "none",
    t_start: float | None = None,
    t_end: float | None = None,
    duration_seconds: float | None = None,
    link_segment: bool = True,
    transcript_version: int = 1,
    active_transcript_version: int | None = None,
    deleted: bool = False,
) -> tuple[Audio, Transcript, Chunk, Segment | None]:
    """Create one active class with a transcript, chunk, embedding and optional segment."""
    audio = await actors.make_audio(db, actor, subject=subject)
    audio.class_date = class_date
    audio.duration_seconds = duration_seconds
    transcript = Transcript(
        user_id=actor.user_id,
        audio_id=audio.id,
        version=transcript_version,
        language_requested="es",
        model="test-model",
        model_config={},
        timestamp_precision=precision,
        text=content,
        char_count=len(content),
        fts_config="spanish",
    )
    db.add(transcript)
    await db.flush()
    audio.active_transcript_version = (
        active_transcript_version
        if active_transcript_version is not None
        else transcript_version
    )
    audio.active_index_version = generation.version
    chunk = Chunk(
        user_id=actor.user_id,
        audio_id=audio.id,
        transcript_id=transcript.id,
        transcript_version=transcript_version,
        index_version=generation.version,
        ordinal=ordinal,
        content=content,
        char_start=0,
        char_end=len(content),
        token_count=len(content.split()),
        tsv=chunk_tsvector("spanish", content),
    )
    db.add(chunk)
    await db.flush()
    db.add(
        Embedding(
            user_id=actor.user_id,
            chunk_id=chunk.id,
            index_version=generation.version,
            model=generation.model,
            dimension=generation.dimension,
            vector=vector or _vector(1.0),
        )
    )
    segment: Segment | None = None
    if link_segment:
        segment = Segment(
            user_id=actor.user_id,
            transcript_id=transcript.id,
            ordinal=ordinal,
            text=content,
            char_start=0,
            char_end=len(content),
            t_start=t_start,
            t_end=t_end,
        )
        db.add(segment)
        await db.flush()
        db.add(
            ChunkSegment(
                user_id=actor.user_id,
                chunk_id=chunk.id,
                segment_id=segment.id,
                overlap_span={"char_start": 0, "char_end": len(content)},
            )
        )
    if deleted:
        audio.deleted_at = datetime.now(UTC)
    await db.flush()
    return audio, transcript, chunk, segment


def _ids(response: Any) -> list[str]:
    return [result["chunk_id"] for result in response.json()["results"]]


# --- 1. Contract shape ----------------------------------------------------------------


async def test_search_returns_contract_shape_and_hybrid_results(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    generation = await _make_generation(db_session, actor)
    audio, _, chunk, segment = await _indexed_class(
        db_session,
        actor,
        generation=generation,
        content="alpha definite integral and the fundamental theorem",
        precision="segment",
        t_start=10.0,
        t_end=20.0,
        duration_seconds=100.0,
    )

    response = await client.get("/search", params={"q": "alpha"}, cookies=actor.cookies)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert isinstance(body["took_ms"], int)
    assert body["took_ms"] >= 0
    assert "next_cursor" in body
    assert len(body["results"]) == 1
    result = body["results"][0]
    assert set(result) == {
        "chunk_id",
        "audio_id",
        "subject",
        "class_date",
        "snippet",
        "segment_id",
        "t_start",
        "t_end",
        "score_rrf",
    }
    assert result["chunk_id"] == str(chunk.id)
    assert result["subject"]["id"] == str(audio.subject_id)
    assert result["class_date"] == "2026-09-21"
    assert segment is not None
    assert result["segment_id"] == str(segment.id)
    assert result["t_start"] == 10.0
    assert result["t_end"] == 20.0
    assert result["score_rrf"] > 0
    assert "content" not in result


# --- 2. Snippet -----------------------------------------------------------------------


async def test_search_snippet_contains_match_context(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    generation = await _make_generation(db_session, actor)
    content = (
        "inicio "
        + ("relleno " * 40)
        + "trigonometric substitution in definite integrals"
        + (" cola" * 40)
    )
    await _indexed_class(
        db_session, actor, generation=generation, content=content, link_segment=False
    )

    response = await client.get(
        "/search", params={"q": "trigonometric"}, cookies=actor.cookies
    )

    assert response.status_code == 200
    snippet = response.json()["results"][0]["snippet"]
    assert "trigonometric" in snippet
    assert snippet.startswith("…")
    assert len(snippet) <= 240
    assert "<b>" not in snippet


# --- 3. Honest timestamps -------------------------------------------------------------


async def test_search_returns_segment_times_only_when_reliable(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    generation = await _make_generation(db_session, actor)
    reliable = await _indexed_class(
        db_session,
        actor,
        generation=generation,
        content="alpha reliable times",
        precision="segment",
        t_start=10.0,
        t_end=20.0,
        duration_seconds=100.0,
    )
    no_precision = await _indexed_class(
        db_session,
        actor,
        generation=generation,
        content="alpha no precision",
        precision="none",
        t_start=10.0,
        t_end=20.0,
        duration_seconds=100.0,
    )
    no_times = await _indexed_class(
        db_session,
        actor,
        generation=generation,
        content="alpha no times",
        precision="segment",
        t_start=None,
        t_end=None,
        duration_seconds=100.0,
    )
    past_duration = await _indexed_class(
        db_session,
        actor,
        generation=generation,
        content="alpha past duration",
        precision="segment",
        t_start=90.0,
        t_end=101.0,
        duration_seconds=100.0,
    )

    response = await client.get(
        "/search", params={"q": "alpha", "limit": 10}, cookies=actor.cookies
    )

    assert response.status_code == 200
    by_chunk = {result["chunk_id"]: result for result in response.json()["results"]}
    assert by_chunk[str(reliable[2].id)]["t_start"] == 10.0
    assert by_chunk[str(reliable[2].id)]["t_end"] == 20.0
    assert by_chunk[str(no_precision[2].id)]["t_start"] is None
    assert by_chunk[str(no_precision[2].id)]["t_end"] is None
    assert by_chunk[str(no_times[2].id)]["t_start"] is None
    assert by_chunk[str(no_times[2].id)]["t_end"] is None
    assert by_chunk[str(past_duration[2].id)]["t_start"] is None
    assert by_chunk[str(past_duration[2].id)]["t_end"] is None


# --- 4. Subject and inclusive date filters --------------------------------------------


async def test_search_filters_by_subject_and_inclusive_date_range(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    generation = await _make_generation(db_session, actor)
    subject_a = await actors.make_subject(db_session, actor)
    subject_b = await actors.make_subject(db_session, actor)
    foreign_actor = await actors.make_actor(db_session)
    foreign_subject = await actors.make_subject(db_session, foreign_actor)
    first = await _indexed_class(
        db_session,
        actor,
        generation=generation,
        content="alpha first",
        subject=subject_a,
        class_date=date(2026, 9, 1),
    )
    boundary = await _indexed_class(
        db_session,
        actor,
        generation=generation,
        content="alpha boundary",
        subject=subject_a,
        class_date=date(2026, 9, 10),
    )
    await _indexed_class(
        db_session,
        actor,
        generation=generation,
        content="alpha other subject",
        subject=subject_b,
        class_date=date(2026, 9, 5),
    )

    only_a = await client.get(
        "/search", params={"q": "alpha", "subject_id": str(subject_a.id)}, cookies=actor.cookies
    )
    assert set(_ids(only_a)) == {str(first[2].id), str(boundary[2].id)}

    window = await client.get(
        "/search",
        params={
            "q": "alpha",
            "subject_id": str(subject_a.id),
            "from": "2026-09-05",
            "to": "2026-09-10",
        },
        cookies=actor.cookies,
    )
    assert _ids(window) == [str(boundary[2].id)]

    foreign = await client.get(
        "/search",
        params={"q": "alpha", "subject_id": str(foreign_subject.id)},
        cookies=actor.cookies,
    )
    assert foreign.status_code == 200
    assert foreign.json()["results"] == []


# --- 5. Tombstone, stale transcript and retired generation ----------------------------


async def test_search_excludes_tombstoned_stale_transcript_and_retired_generation(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    active_generation = await _make_generation(db_session, actor, version="test-v2")
    retired_generation = await _make_generation(
        db_session, actor, version="test-v1", status="retired"
    )
    active = await _indexed_class(
        db_session,
        actor,
        generation=active_generation,
        content="alpha active",
    )
    await _indexed_class(
        db_session,
        actor,
        generation=active_generation,
        content="alpha tombstoned",
        deleted=True,
    )
    await _indexed_class(
        db_session,
        actor,
        generation=active_generation,
        content="alpha stale transcript",
        active_transcript_version=2,
    )
    await _indexed_class(
        db_session,
        actor,
        generation=retired_generation,
        content="alpha retired generation",
    )

    response = await client.get(
        "/search", params={"q": "alpha", "limit": 10}, cookies=actor.cookies
    )

    assert response.status_code == 200
    assert _ids(response) == [str(active[2].id)]


# --- 6. Tenant filter before the top-k ------------------------------------------------


async def test_search_filters_tenant_before_top_k(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor_a = await actors.make_actor(db_session)
    actor_b = await actors.make_actor(db_session)
    generation_a = await _make_generation(db_session, actor_a, version="a-v1")
    generation_b = await _make_generation(db_session, actor_b, version="b-v1")
    own = [
        await _indexed_class(
            db_session,
            actor_a,
            generation=generation_a,
            content="alpha " * 1 + f"own {index}",
            vector=_vector(0.0, 1.0),
        )
        for index in range(2)
    ]
    for index in range(10):
        await _indexed_class(
            db_session,
            actor_b,
            generation=generation_b,
            content="alpha " * 5 + f"alien {index}",
            vector=_vector(1.0),
        )

    response = await client.get(
        "/search", params={"q": "alpha", "limit": 2}, cookies=actor_a.cookies
    )

    assert response.status_code == 200
    assert set(_ids(response)) == {str(item[2].id) for item in own}


# --- 7. Cursor pagination --------------------------------------------------------------


async def test_search_paginates_with_cursor_without_duplicates_or_omissions(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    generation = await _make_generation(db_session, actor)
    classes = [
        await _indexed_class(
            db_session,
            actor,
            generation=generation,
            content=("alpha " * (index + 1)) + f"pagination {index}",
            vector=_vector(1.0, 0.01 * index),
        )
        for index in range(5)
    ]
    expected = {str(item[2].id) for item in classes}

    single = await client.get(
        "/search", params={"q": "alpha", "limit": 10}, cookies=actor.cookies
    )
    assert set(_ids(single)) == expected

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(5):
        params: dict[str, str | int] = {"q": "alpha", "limit": 2}
        if cursor is not None:
            params["cursor"] = cursor
        page = await client.get("/search", params=params, cookies=actor.cookies)
        assert page.status_code == 200
        body = page.json()
        page_ids = [result["chunk_id"] for result in body["results"]]
        assert not set(seen) & set(page_ids)
        seen.extend(page_ids)
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert set(seen) == expected
    assert len(seen) == len(expected)


# --- 8. Stale cursor after a generation change ----------------------------------------


async def test_search_rejects_stale_cursor_after_generation_change(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    generation = await _make_generation(db_session, actor, version="test-v1")
    for index in range(4):
        await _indexed_class(
            db_session,
            actor,
            generation=generation,
            content=f"alpha stale cursor {index}",
            vector=_vector(1.0, 0.01 * index),
        )

    first = await client.get(
        "/search", params={"q": "alpha", "limit": 2}, cookies=actor.cookies
    )
    cursor = first.json()["next_cursor"]
    assert cursor is not None

    await _make_generation(db_session, actor, version="test-v2", status="active")

    stale = await client.get(
        "/search",
        params={"q": "alpha", "limit": 2, "cursor": cursor},
        cookies=actor.cookies,
    )

    assert stale.status_code == 422
    assert stale.json()["error"]["code"] == "validation_failed"
    assert stale.json()["error"]["details"]["fields"]["cursor"] == _CURSOR_STALE


# --- 9. No active generation -----------------------------------------------------------


async def test_search_returns_empty_page_without_active_generation(
    client: AsyncClient, db_session: AsyncSession, provider: FakeEmbeddingProvider
) -> None:
    actor = await actors.make_actor(db_session)

    response = await client.get("/search", params={"q": "alpha"}, cookies=actor.cookies)

    assert response.status_code == 200
    assert response.json()["results"] == []
    assert response.json()["next_cursor"] is None
    assert provider.calls == 0


# --- 10. Provider not configured -------------------------------------------------------


async def test_search_maps_missing_embedding_provider_to_503(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    generation = await _make_generation(db_session, actor)
    await _indexed_class(db_session, actor, generation=generation, content="alpha no provider")
    app.dependency_overrides[get_embedding_provider] = lambda: None

    response = await client.get("/search", params={"q": "alpha"}, cookies=actor.cookies)

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "provider_unavailable"
    assert error["details"]["reason"] == "not_configured"


# --- 11. Provider failures -------------------------------------------------------------


async def test_search_maps_embedding_provider_failure_to_503(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    generation = await _make_generation(db_session, actor)
    await _indexed_class(db_session, actor, generation=generation, content="alpha provider error")
    app.dependency_overrides[get_embedding_provider] = lambda: FakeEmbeddingProvider(
        error=EmbeddingProviderError("boom")
    )

    failed = await client.get("/search", params={"q": "alpha"}, cookies=actor.cookies)
    assert failed.status_code == 503
    assert failed.json()["error"]["code"] == "provider_unavailable"

    app.dependency_overrides[get_embedding_provider] = lambda: FakeEmbeddingProvider(
        error=EmbeddingRateLimited("quota", details={"retry_after": 7})
    )
    limited = await client.get("/search", params={"q": "alpha"}, cookies=actor.cookies)
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"
    assert limited.headers["retry-after"] == "7"


# --- 12. Per-user quota before the embeddings provider ---------------------------------


async def test_search_enforces_per_user_quota_before_embeddings(
    client: AsyncClient,
    db_session: AsyncSession,
    provider: FakeEmbeddingProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "search_rate_limit_per_minute", 2)
    actor = await actors.make_actor(db_session)
    generation = await _make_generation(db_session, actor)
    await _indexed_class(db_session, actor, generation=generation, content="alpha quota")

    for _ in range(2):
        allowed = await client.get("/search", params={"q": "alpha"}, cookies=actor.cookies)
        assert allowed.status_code == 200
    assert provider.calls == 2

    limited = await client.get("/search", params={"q": "alpha"}, cookies=actor.cookies)

    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"
    assert limited.json()["error"]["details"] == {"limit": 2, "window_seconds": 60}
    assert 1 <= int(limited.headers["retry-after"]) <= 60
    # The quota dependency runs before the handler: no extra paid embeddings call.
    assert provider.calls == 2

    other = await actors.make_actor(db_session)
    unaffected = await client.get("/search", params={"q": "alpha"}, cookies=other.cookies)
    assert unaffected.status_code == 200


# --- 13. Validation --------------------------------------------------------------------


async def test_search_validates_query_limit_dates_and_cursor(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    cases: list[dict[str, str | int]] = [
        {"q": ""},
        {"q": "   "},
        {"q": "alpha", "limit": 0},
        {"q": "alpha", "limit": 51},
        {"q": "alpha", "from": "not-a-date"},
        {"q": "alpha", "from": "2026-09-10", "to": "2026-09-01"},
        {"q": "alpha", "cursor": "not-a-cursor"},
        {"q": "a" * 501},
    ]

    for params in cases:
        response = await client.get("/search", params=params, cookies=actor.cookies)
        assert response.status_code == 422, params
        assert response.json()["error"]["code"] == "validation_failed", params

    malformed = await client.get(
        "/search", params={"q": "alpha", "cursor": "not-a-cursor"}, cookies=actor.cookies
    )
    assert malformed.json()["error"]["details"]["fields"]["cursor"] == _CURSOR_INVALID


# --- 14. Authentication ----------------------------------------------------------------


async def test_search_requires_authentication(client: AsyncClient) -> None:
    response = await client.get("/search", params={"q": "alpha"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "auth_required"


# --- 15. No content in logs ------------------------------------------------------------


async def test_search_sets_no_store_and_does_not_log_content(
    client: AsyncClient, db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    actor = await actors.make_actor(db_session)
    generation = await _make_generation(db_session, actor)
    await _indexed_class(
        db_session,
        actor,
        generation=generation,
        content="canaryquery canarycontent",
    )
    caplog.set_level(logging.DEBUG)

    response = await client.get(
        "/search", params={"q": "canaryquery"}, cookies=actor.cookies
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    app_logs = "\n".join(
        record.getMessage() for record in caplog.records if record.name.startswith("app.")
    )
    assert "canaryquery" not in app_logs
    assert "canarycontent" not in app_logs


# --- 16. Pinned pgvector capability ----------------------------------------------------


async def test_search_pinned_pgvector_supports_iterative_scan(
    db_session: AsyncSession,
) -> None:
    await db_session.execute(text("SET LOCAL hnsw.iterative_scan = strict_order"))

    value = (await db_session.execute(text("SHOW hnsw.iterative_scan"))).scalar_one()

    assert value == "strict_order"


# --- 17. Chat consumer gets the full content ------------------------------------------


async def test_search_service_returns_content_for_chat_consumers(
    client: AsyncClient,
    db_session: AsyncSession,
    provider: FakeEmbeddingProvider,
) -> None:
    actor = await actors.make_actor(db_session)
    generation = await _make_generation(db_session, actor)
    content = "alpha complete content for the chat prompt"
    await _indexed_class(db_session, actor, generation=generation, content=content)
    service = SearchService(db_session, provider=provider)

    results = await service.retrieve(user_id=actor.user_id, query="alpha")

    assert len(results) == 1
    assert results[0].content == content

    response = await client.get("/search", params={"q": "alpha"}, cookies=actor.cookies)
    assert response.status_code == 200
    assert "content" not in response.json()["results"][0]

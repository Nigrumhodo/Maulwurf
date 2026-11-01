"""I-S4-AN-06 (A4.6): final hardening across API, SSE, tools and jobs.

The ticket proves three guarantees that were previously route-by-route conventions:

1. ownership is checked on every channel: the API hides a foreign class behind the same
   `404 not_found` as a missing one, the progress SSE refuses it before streaming, and the
   index job re-reads the owner from the database (tombstoned/revoked owners are a no-op).
   The full cross-channel matrix is I-S4-AN-07 (A4.7); this module keeps the channels the
   hardening ticket names. The chat tools (`get_calendar_events`, `get_pending_tasks`,
   `propose_task`) are F2 and do not exist yet, so there is nothing to verify there.
2. `Cache-Control: no-store` is structural: private reads, errors, rate-limit rejections
   and SSE all forbid caching.
3. every active limit answers `429` (or `503` when the limiter itself is unavailable) with
   `Retry-After`. Numbers are the published ones: two chat streams (M7), the provisional
   search quota (M6/D5), the upload quota/slot (A2.3/A2.14) and the 60 s Calendar cooldown
   (M5). Authentication and incremental consent have no numeric decision yet (D4/D5 open),
   so their fixed-window mechanism is configuration-driven and unset means "no limit".
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import fakeredis.aioredis
import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.db import get_session
from app.core.deps import chat_session_factory, progress_session_factory
from app.core.errors import calendar_sync_cooldown
from app.core.redis import get_redis
from app.main import app
from app.models import Audio, Message, ProcessingRun, User
from app.services import audios as audios_service
from app.services.index_job import IndexSkip, revalidate
from app.services.rate_limiter import acquire_stream
from app.services.runs import INDEX_CONFIG_VERSION
from tests.integration import actors
from tests.integration.actors import Actor

pytestmark = pytest.mark.integration

NO_STORE = "no-store"
DATE_FROM = "2026-09-01"
DATE_TO = "2026-09-30"


@pytest.fixture
async def redis() -> AsyncIterator[fakeredis.aioredis.FakeRedis]:
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
async def client(
    db_session: AsyncSession, redis: fakeredis.aioredis.FakeRedis
) -> AsyncIterator[AsyncClient]:
    async def override_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def override_redis() -> AsyncIterator[fakeredis.aioredis.FakeRedis]:
        yield redis

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_redis] = override_redis
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=settings.allowed_origin
    ) as http:
        yield http
    app.dependency_overrides.clear()


@pytest.fixture
async def committed(
    migrated_database_url: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Factory on the migrated DB for the channels that open short-lived sessions."""
    engine = create_async_engine(migrated_database_url)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    try:
        yield factory
    finally:
        await engine.dispose()


async def _account(db: AsyncSession) -> tuple[Actor, uuid.UUID]:
    actor = await actors.make_actor(db)
    subject = await actors.make_subject(db, actor)
    await db.flush()
    return actor, subject.id


def _body(subject_id: uuid.UUID, **overrides: Any) -> dict[str, Any]:
    return {
        "subject_id": str(subject_id),
        "class_date": "2026-09-21",
        "class_timezone": "America/Bogota",
        "language_code": "es",
        "title": "Derivadas",
        "privacy_notice_version": settings.privacy_notice_version,
        "cloud_processing_accepted": True,
        "third_party_voice_acknowledged": True,
        **overrides,
    }


async def test_private_reads_and_error_envelopes_forbid_caching(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor, _ = await _account(db_session)
    audio = await actors.make_audio(db_session, actor)
    await actors.make_conversation(db_session, actor)

    private_reads = [
        ("/me", {}),
        ("/me/notifications", {}),
        ("/subjects", {}),
        ("/audios", {}),
        (f"/audios/{audio.id}", {}),
        (f"/audios/{audio.id}/runs", {}),
        ("/integrations/status", {}),
        ("/notifications", {}),
        ("/chat/conversations", {}),
        ("/calendar/events", {"from": DATE_FROM, "to": DATE_TO}),
        ("/calendar/conflicts", {"from": DATE_FROM, "to": DATE_TO}),
    ]
    for path, params in private_reads:
        response = await client.get(path, params=params, cookies=actor.cookies)
        assert response.status_code == 200, (path, response.text)
        assert response.headers["cache-control"] == NO_STORE, path

    # A foreign/missing resource and a wrong method are private failures too.
    foreign = await client.get(f"/audios/{uuid.uuid4()}", cookies=actor.cookies)
    assert foreign.status_code == 404
    assert foreign.headers["cache-control"] == NO_STORE
    missing = await client.get("/no-such-route", cookies=actor.cookies)
    assert missing.status_code == 404
    assert missing.headers["cache-control"] == NO_STORE
    unauthorized = await client.get("/me")
    assert unauthorized.status_code == 401
    assert unauthorized.headers["cache-control"] == NO_STORE


async def test_progress_sse_is_no_store_and_refuses_a_foreign_class(
    committed: async_sessionmaker[AsyncSession], redis: fakeredis.aioredis.FakeRedis
) -> None:
    async with committed() as db:
        alice = await actors.make_actor(db)
        audio, _, _ = await actors.make_class_with_transcript(db, alice)
        audio.duration_seconds = 60.0
        db.add(
            ProcessingRun(
                user_id=alice.user_id,
                audio_id=audio.id,
                stage="index",
                transcript_version=1,
                config_version=INDEX_CONFIG_VERSION,
                status="succeeded",
            )
        )
        bob = await actors.make_actor(db)
        await db.commit()
        alice_audio = audio.id

    async def override_redis() -> AsyncIterator[fakeredis.aioredis.FakeRedis]:
        yield redis

    app.dependency_overrides[get_session] = _forbidden_request_session
    app.dependency_overrides[progress_session_factory] = lambda: committed
    app.dependency_overrides[get_redis] = override_redis
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=settings.allowed_origin
        ) as http:
            own = await http.get(f"/audios/{alice_audio}/progress", cookies=alice.cookies)
            assert own.status_code == 200
            assert own.headers["content-type"].startswith("text/event-stream")
            assert own.headers["cache-control"] == NO_STORE

            foreign = await http.get(f"/audios/{alice_audio}/progress", cookies=bob.cookies)
            assert foreign.status_code == 404
            assert foreign.headers["cache-control"] == NO_STORE
    finally:
        app.dependency_overrides.clear()


async def _forbidden_request_session() -> AsyncIterator[AsyncSession]:
    raise AssertionError("the SSE route must use its short-lived factory")
    yield  # pragma: no cover - unreachable, keeps the async-generator shape


async def test_chat_third_concurrent_stream_is_429_with_retry_after(
    committed: async_sessionmaker[AsyncSession], redis: fakeredis.aioredis.FakeRedis
) -> None:
    async with committed() as db:
        actor = await actors.make_actor(db)
        conversation = await actors.make_conversation(db, actor)
        await db.commit()
        actor_cookies = actor.cookies
        actor_headers = actor.headers
        user_id = actor.user_id
        conversation_id = conversation.id
        before = await db.scalar(
            select(func.count()).select_from(Message).where(Message.user_id == user_id)
        )

    async def override_redis() -> AsyncIterator[fakeredis.aioredis.FakeRedis]:
        yield redis

    app.dependency_overrides[chat_session_factory] = lambda: committed
    app.dependency_overrides[get_redis] = override_redis
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=settings.allowed_origin
        ) as http:
            await acquire_stream(redis, user_id)
            await acquire_stream(redis, user_id)
            response = await http.post(
                f"/chat/conversations/{conversation_id}/messages",
                json={"content": "¿Qué es la integral?", "client_message_id": "turno-1"},
                cookies=actor_cookies,
                headers=actor_headers,
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limited"
    assert int(response.headers["Retry-After"]) >= 1
    assert response.headers["cache-control"] == NO_STORE
    async with committed() as db:
        after = await db.scalar(
            select(func.count()).select_from(Message).where(Message.user_id == user_id)
        )
    assert after == before


async def test_upload_quota_is_429_with_retry_after(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    actor, subject_id = await _account(db_session)
    monkeypatch.setattr(audios_service, "per_user_active_limit", 1)

    first = await client.post(
        "/audios", json=_body(subject_id), cookies=actor.cookies, headers=actor.headers
    )
    second = await client.post(
        "/audios", json=_body(subject_id), cookies=actor.cookies, headers=actor.headers
    )

    assert first.status_code == 201, first.text
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "rate_limited"
    assert int(second.headers["Retry-After"]) >= 1
    assert second.headers["cache-control"] == NO_STORE
    # A quota rejection does not reserve capacity.
    reserved = await db_session.scalar(
        select(func.count()).select_from(Audio).where(Audio.user_id == actor.user_id)
    )
    assert reserved == 1


async def test_search_quota_is_429_with_retry_after(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    actor, _ = await _account(db_session)
    monkeypatch.setattr(settings, "search_rate_limit_per_minute", 1)

    first = await client.get("/search", params={"q": "derivadas"}, cookies=actor.cookies)
    second = await client.get("/search", params={"q": "derivadas"}, cookies=actor.cookies)

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "rate_limited"
    assert int(second.headers["Retry-After"]) >= 1
    assert second.headers["cache-control"] == NO_STORE


async def test_auth_start_and_consent_are_429_with_retry_after_when_configured(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "google_client_id", "test-client-id")
    monkeypatch.setattr(settings, "google_client_secret", SecretStr("test-client-secret"))
    monkeypatch.setattr(settings, "auth_rate_limit_per_minute", 2)
    monkeypatch.setattr(settings, "integration_rate_limit_per_minute", 1)
    actor, _ = await _account(db_session)

    first = await client.get("/auth/google/start", follow_redirects=False)
    second = await client.get("/auth/google/start", follow_redirects=False)
    third = await client.get("/auth/google/start", follow_redirects=False)

    assert first.status_code == 302
    assert second.status_code == 302
    assert third.status_code == 429
    assert third.json()["error"]["code"] == "rate_limited"
    assert third.json()["error"]["details"]["scope"] == "auth"
    assert int(third.headers["Retry-After"]) >= 1
    assert third.headers["cache-control"] == NO_STORE

    consent = await client.post(
        "/integrations/google/connect", cookies=actor.cookies, headers=actor.headers
    )
    limited = await client.post(
        "/integrations/google/connect", cookies=actor.cookies, headers=actor.headers
    )
    assert consent.status_code == 302
    assert limited.status_code == 429
    assert limited.json()["error"]["details"]["scope"] == "integrations"
    assert int(limited.headers["Retry-After"]) >= 1

    # The consent budget is per user: another tenant is not blocked by this counter.
    bob, _ = await _account(db_session)
    other = await client.post(
        "/integrations/google/connect", cookies=bob.cookies, headers=bob.headers
    )
    assert other.status_code == 302


async def test_auth_limit_is_per_peer_address(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "google_client_id", "test-client-id")
    monkeypatch.setattr(settings, "google_client_secret", SecretStr("test-client-secret"))
    monkeypatch.setattr(settings, "auth_rate_limit_per_minute", 1)

    async def one_from(host: str) -> list[int]:
        async with AsyncClient(
            transport=ASGITransport(app=app, client=(host, 1000)),
            base_url=settings.allowed_origin,
        ) as http:
            codes = []
            for _ in range(2):
                response = await http.get("/auth/google/start", follow_redirects=False)
                codes.append(response.status_code)
            return codes

    assert await one_from("203.0.113.10") == [302, 429]
    # A hashed, per-peer counter: another address starts its own window instead of
    # inheriting the exhausted one.
    assert await one_from("198.51.100.20") == [302, 429]


async def test_limiter_outage_is_a_typed_503_with_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _DownRedis:
        def pipeline(self, *, transaction: bool) -> Any:
            raise RedisError("down")

    async def down_redis() -> AsyncIterator[Any]:
        yield _DownRedis()

    monkeypatch.setattr(settings, "google_client_id", "test-client-id")
    monkeypatch.setattr(settings, "google_client_secret", SecretStr("test-client-secret"))
    monkeypatch.setattr(settings, "auth_rate_limit_per_minute", 1)
    app.dependency_overrides[get_redis] = down_redis
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=settings.allowed_origin
        ) as http:
            response = await http.get("/auth/google/start", follow_redirects=False)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "capacity_unavailable"
    assert int(response.headers["Retry-After"]) >= 1
    assert response.headers["cache-control"] == NO_STORE


async def test_ownership_is_reverified_by_the_index_job(
    db_session: AsyncSession,
) -> None:
    actor, _ = await _account(db_session)
    audio, _, _ = await actors.make_class_with_transcript(db_session, actor)

    owner = await revalidate(db_session, audio_id=audio.id, transcript_version=1)
    assert owner == actor.user_id

    audio.deleted_at = datetime.now(UTC)
    await db_session.flush()
    assert (
        await revalidate(db_session, audio_id=audio.id, transcript_version=1)
        is IndexSkip.TOMBSTONED
    )

    audio.deleted_at = None
    user = await db_session.get(User, actor.user_id)
    assert user is not None
    user.status = "deleted"
    await db_session.flush()
    assert (
        await revalidate(db_session, audio_id=audio.id, transcript_version=1)
        is IndexSkip.OWNER_REVOKED
    )


def test_the_calendar_cooldown_is_documented_and_has_a_retry_hint() -> None:
    # The 60 s cooldown is J3.8's published decision; I-S3-JF-07 owns its HTTP proof.
    # A4.6 pins the contract shape here so a future refactor cannot drop the header.
    error = calendar_sync_cooldown(37)

    assert error.status_code == 429
    assert error.details == {"reason": "calendar_sync_cooldown", "retry_after_seconds": 37}
    assert error.headers["Retry-After"] == "37"

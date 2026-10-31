"""I-S2-AN-13 (A2.14): reservation lifecycle and `PUT` concurrency.

Real PostgreSQL on a dedicated migrated database with COMMITTED rows, so the parallel
sessions of the 20-request race really see each other (the shared `db_session` fixture is a
single rolled-back transaction and cannot exercise a lock). The routers are the real ones;
`ReceptionSink` is an in-memory double, and no byte is ever written to disk.

Cases:
- 20 concurrent `POST /audios` never exceed `active_slots` or `per_user_active_limit`;
- two concurrent `PUT`s on one attempt reach the sink exactly once (`409 attempt_not_active`);
- an abandoned reservation frees its slot after `upload_expires_at` (idempotent reaper), and
  the reaper leaves rows that `ingest` already owns alone;
- a failed `PUT` (sink unavailable, declared oversize, client disconnect) frees the slot
  immediately.
"""

import asyncio
import hashlib
import uuid
from collections import Counter
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import make_url, select, text, update
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from alembic import command
from app.core.config import settings
from app.core.db import get_session
from app.main import app
from app.models import IngestionAttempt
from app.models.ingestion import ACTIVE_INGESTION_STATES
from app.schemas.ingestion import IngestionAttemptTtl, IngestionCapabilities
from app.services import audios as audios_service
from app.services.audios import expire_stale_reservations
from app.services.ingest_sink import get_reception_sink
from app.services.uploads import Admission, DiscardReason, Received, SinkError
from tests.integration.actors import Actor, make_actor, make_attempt, make_subject

pytestmark = pytest.mark.integration

API_ROOT = Path(__file__).resolve().parents[2]
CONTENT = b"RIFF" + b"\x00" * 60


class ClientDisconnected(Exception):
    """Sentinel raised by the request-body generator to emulate the browser going away."""


# --- infrastructure ----------------------------------------------------------------------


async def _admin(sql: str) -> None:
    admin = create_async_engine(
        make_url(str(settings.database_url)).set(database="postgres"),
        isolation_level="AUTOCOMMIT",
    )
    async with admin.connect() as conn:
        await conn.execute(text(sql))
    await admin.dispose()


def _alembic_config(url: str) -> Config:
    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@pytest.fixture(scope="module")
def reservation_database_url() -> Iterator[str]:
    """Dedicated database: the parallel test COMMITS rows, so it cannot be a savepoint."""
    name = f"maulwurf_test_a214_{uuid.uuid4().hex[:12]}"
    asyncio.run(_admin(f'CREATE DATABASE "{name}"'))
    url = make_url(str(settings.database_url)).set(database=name).render_as_string(
        hide_password=False
    )
    try:
        command.upgrade(_alembic_config(url), "head")
        yield url
    finally:
        asyncio.run(_admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@pytest.fixture
async def engine(reservation_database_url: str) -> AsyncIterator[AsyncEngine]:
    # Large enough for the 20 concurrent requests of I-S2-AN-13; one engine per test keeps
    # the asyncpg connections on the pytest-asyncio function-scoped loop.
    engine = create_async_engine(reservation_database_url, pool_size=25, max_overflow=5)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def _clean_active_reservations(engine: AsyncEngine) -> AsyncIterator[None]:
    """Start every test from zero active attempts, so global counts are deterministic."""
    async with engine.begin() as conn:
        await conn.execute(
            update(IngestionAttempt)
            .where(IngestionAttempt.status.in_(ACTIVE_INGESTION_STATES))
            .values(status="cancelled", lease_expires_at=None)
        )
    yield


class RecordingSink:
    """`ReceptionSink` double: hashes in RAM, records order, can block or fail at `begin`."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.begins = 0
        self.written = 0
        self.retained = bytearray()
        self.hasher = hashlib.sha256()
        self.begin_error: str | None = None
        self.block_begin = False
        self.begin_started = asyncio.Event()
        self.release_begin = asyncio.Event()

    async def begin(
        self,
        *,
        user_id: uuid.UUID,
        audio_id: uuid.UUID,
        attempt_id: uuid.UUID,
        fencing_token: int,
        declared_bytes: int | None,
    ) -> None:
        self.begins += 1
        self.events.append("begin")
        self.hasher = hashlib.sha256()
        self.written = 0
        self.begin_started.set()
        if self.begin_error is not None:
            raise SinkError(self.begin_error)
        if self.block_begin:
            await self.release_begin.wait()

    async def write(self, chunk: bytes) -> None:
        self.events.append("write")
        self.hasher.update(chunk)
        self.written += len(chunk)
        self.retained += chunk

    async def finish(self) -> Received:
        self.events.append("finish")
        return Received(sha256=self.hasher.hexdigest(), received_bytes=self.written)

    async def abort(self) -> None:
        self.events.append("abort")
        self.retained.clear()

    async def discard(self, reason: DiscardReason) -> None:
        self.events.append(f"discard:{reason}")
        self.retained.clear()

    async def admit(self) -> Admission:
        self.events.append("admit")
        self.retained.clear()
        return Admission(status="transcribing")


@pytest.fixture
def sink() -> RecordingSink:
    return RecordingSink()


@pytest.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession], sink: RecordingSink
) -> AsyncIterator[AsyncClient]:
    async def override() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = override
    app.dependency_overrides[get_reception_sink] = lambda: sink
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=settings.allowed_origin
        ) as http:
            yield http
    finally:
        app.dependency_overrides.clear()


# --- helpers -----------------------------------------------------------------------------


async def _account(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[Actor, uuid.UUID]:
    """Committed user + session + subject: each concurrent request resolves them itself."""
    async with session_factory() as db:
        actor = await make_actor(db)
        subject = await make_subject(db, actor)
        await db.commit()
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


async def _post(
    client: AsyncClient, actor: Actor, subject_id: uuid.UUID, **overrides: Any
) -> Response:
    return await client.post(
        "/audios",
        json=_body(subject_id, **overrides),
        cookies=actor.cookies,
        headers=actor.headers,
    )


async def _reserve(
    client: AsyncClient, actor: Actor, subject_id: uuid.UUID
) -> dict[str, Any]:
    response = await _post(client, actor, subject_id)
    assert response.status_code == 201, response.text
    data: dict[str, Any] = response.json()
    return data


async def _put(
    client: AsyncClient, actor: Actor, url: str, *, content: Any = CONTENT
) -> Response:
    headers = {**actor.headers, "Content-Type": "audio/wav"}
    return await client.put(url, content=content, cookies=actor.cookies, headers=headers)


def _publish(monkeypatch: pytest.MonkeyPatch, *, active_slots: int) -> None:
    """Publish a validated capability set so the slot counter is really enforced."""
    capabilities = IngestionCapabilities(
        limits_validated=True,
        source_report="tests/integration/test_reservation_lifecycle.py",
        source_version="a2.14",
        max_upload_bytes=settings.upload_max_bytes,
        max_duration_seconds=3600,
        accepted_input_formats=("wav",),
        languages=("es",),
        ttl=IngestionAttemptTtl(
            upload_start_minutes=15, receive_minutes=15, asr_minutes=15
        ),
        active_slots=active_slots,
    )
    monkeypatch.setattr(settings, "ingestion_capabilities", capabilities)


async def _attempt(
    session_factory: async_sessionmaker[AsyncSession], attempt_id: uuid.UUID
) -> IngestionAttempt | None:
    async with session_factory() as db:
        return await db.get(IngestionAttempt, attempt_id)


# --- 1. concurrent POSTs -----------------------------------------------------------------


async def test_twenty_concurrent_posts_respect_both_published_limits(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alice, alice_subject = await _account(session_factory)
    bob, bob_subject = await _account(session_factory)
    _publish(monkeypatch, active_slots=5)
    monkeypatch.setattr(audios_service, "per_user_active_limit", 3)

    requests = [
        client.post(
            "/audios",
            json=_body(subject_id),
            cookies=actor.cookies,
            headers=actor.headers,
        )
        for actor, subject_id in [(alice, alice_subject)] * 10
        + [(bob, bob_subject)] * 10
    ]
    responses = await asyncio.gather(*requests)

    codes = [response.status_code for response in responses]
    assert codes.count(201) == 5  # exactly the published global capacity
    assert set(codes) - {201} <= {429, 503}  # only the documented rejections
    assert all(
        response.headers.get("Retry-After")
        for response in responses
        if response.status_code in {429, 503}
    )
    accepted = {
        response.json()["audio_id"]
        for response in responses
        if response.status_code == 201
    }
    assert len(accepted) == 5

    async with session_factory() as db:
        owners = list(
            (
                await db.execute(
                    select(IngestionAttempt.user_id).where(
                        IngestionAttempt.status.in_(ACTIVE_INGESTION_STATES)
                    )
                )
            )
            .scalars()
            .all()
        )
    per_user = Counter(owners)
    assert sum(per_user.values()) == 5  # never above active_slots
    assert max(per_user.values()) <= 3  # never above per_user_active_limit
    assert set(per_user.values()) == {2, 3}


# --- 2. concurrent PUTs ------------------------------------------------------------------


async def test_two_concurrent_puts_reach_the_sink_exactly_once(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    sink: RecordingSink,
) -> None:
    actor, subject_id = await _account(session_factory)
    reserved = await _reserve(client, actor, subject_id)
    sink.block_begin = True

    first = asyncio.create_task(_put(client, actor, reserved["upload_url"]))
    second = asyncio.create_task(_put(client, actor, reserved["upload_url"]))
    await asyncio.wait_for(sink.begin_started.wait(), timeout=5)
    sink.release_begin.set()
    responses = await asyncio.gather(first, second)

    assert sorted(response.status_code for response in responses) == [202, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["error"]["code"] == "attempt_not_active"
    # The loser stopped at the API: the sink saw exactly one reception, start to finish.
    assert sink.begins == 1
    assert sink.events.count("begin") == 1
    assert sink.events.count("admit") == 1
    assert sink.events[-1] == "admit"
    assert sink.written == len(CONTENT)
    # The fake does not model ingest's admit write, so the row stays in the claimed state.
    attempt = await _attempt(session_factory, uuid.UUID(reserved["attempt_id"]))
    assert attempt is not None and attempt.status == "receiving"

    # The claim is single-use: a later PUT is rejected too.
    third = await _put(client, actor, reserved["upload_url"])
    assert third.status_code == 409
    assert third.json()["error"]["code"] == "attempt_not_active"
    assert sink.begins == 1


# --- 3. stale reservations ---------------------------------------------------------------


async def test_abandoned_reservation_frees_its_slot_after_upload_expires_at(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor, subject_id = await _account(session_factory)
    first = await _reserve(client, actor, subject_id)
    _publish(monkeypatch, active_slots=1)

    assert (await _post(client, actor, subject_id)).status_code == 503
    async with session_factory() as db:
        attempt = await db.get(IngestionAttempt, uuid.UUID(first["attempt_id"]))
        assert attempt is not None
        attempt.upload_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await db.commit()
    # Still counted as active until the reaper runs: that is the abandoned reservation.
    assert (await _post(client, actor, subject_id)).status_code == 503

    async with session_factory() as db:
        now = datetime.now(UTC)
        assert await expire_stale_reservations(db, now=now) == 1
        await db.commit()
        # Idempotent: nothing left to expire with the same clock.
        assert await expire_stale_reservations(db, now=now) == 0
        await db.commit()

    expired = await _attempt(session_factory, uuid.UUID(first["attempt_id"]))
    assert expired is not None
    assert (expired.status, expired.error_code) == ("cancelled", "upload_expired")
    assert expired.lease_expires_at is None

    freed = await _post(client, actor, subject_id)
    assert freed.status_code == 201


async def test_expire_stale_reservations_leaves_rows_that_ingest_owns(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    actor, _subject_id = await _account(session_factory)
    past = datetime.now(UTC) - timedelta(minutes=1)
    future = datetime.now(UTC) + timedelta(minutes=5)
    async with session_factory() as db:
        owned = await make_attempt(db, actor)
        stale = await make_attempt(db, actor)
        waiting = await make_attempt(db, actor)
        owned.status = "receiving"
        owned.lease_expires_at = future
        owned.upload_expires_at = past
        stale.status = "receiving"
        stale.lease_expires_at = None
        stale.upload_expires_at = past
        waiting.upload_expires_at = future
        await db.commit()
        owned_id, stale_id, waiting_id = owned.id, stale.id, waiting.id

    async with session_factory() as db:
        assert await expire_stale_reservations(db, now=datetime.now(UTC)) == 1
        await db.commit()

    fresh_owned = await _attempt(session_factory, owned_id)
    fresh_stale = await _attempt(session_factory, stale_id)
    fresh_waiting = await _attempt(session_factory, waiting_id)
    # A live lease belongs to ingest (S2.7 handles its expiration); a waiting row still has
    # a valid window. Only the unowned, past-window row is reaped.
    assert fresh_owned is not None and fresh_owned.status == "receiving"
    assert fresh_owned.lease_expires_at is not None
    assert fresh_stale is not None
    assert (fresh_stale.status, fresh_stale.error_code) == ("cancelled", "upload_expired")
    assert fresh_waiting is not None and fresh_waiting.status == "awaiting_upload"


# --- 4. failed PUT -----------------------------------------------------------------------


async def test_failed_put_releases_the_slot_immediately(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    sink: RecordingSink,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor, subject_id = await _account(session_factory)
    _publish(monkeypatch, active_slots=1)
    first = await _reserve(client, actor, subject_id)
    sink.begin_error = "capacity_unavailable"

    unavailable = await _put(client, actor, first["upload_url"])
    assert unavailable.status_code == 503
    assert unavailable.headers.get("Retry-After")
    attempt = await _attempt(session_factory, uuid.UUID(first["attempt_id"]))
    assert attempt is not None and attempt.status == "cancelled"

    # The slot is free without waiting for the TTL.
    second = await _reserve(client, actor, subject_id)

    # A declared oversize is rejected before a byte and also frees the claim.
    oversized = await client.put(
        second["upload_url"],
        content=b"",
        cookies=actor.cookies,
        headers={**actor.headers, "Content-Length": str(settings.upload_max_bytes + 1)},
    )
    assert oversized.status_code == 413
    attempt = await _attempt(session_factory, uuid.UUID(second["attempt_id"]))
    assert attempt is not None and attempt.status == "cancelled"

    third = await _reserve(client, actor, subject_id)
    assert third["attempt_id"] != second["attempt_id"]


async def test_client_disconnect_releases_the_claim(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    sink: RecordingSink,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor, subject_id = await _account(session_factory)
    _publish(monkeypatch, active_slots=1)
    reserved = await _reserve(client, actor, subject_id)

    async def broken_body() -> AsyncIterator[bytes]:
        yield CONTENT
        raise ClientDisconnected

    with pytest.raises(ClientDisconnected):
        await _put(client, actor, reserved["upload_url"], content=broken_body())

    attempt = await _attempt(session_factory, uuid.UUID(reserved["attempt_id"]))
    assert attempt is not None and attempt.status == "cancelled"
    assert sink.retained == b""
    assert ("abort" in sink.events) or ("discard:client_disconnected" in sink.events)

    freed = await _post(client, actor, subject_id)
    assert freed.status_code == 201

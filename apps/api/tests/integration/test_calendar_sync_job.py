"""I-S3-JF-03 (J3.4): double delivery, lost response and retry produce one event.

Gate: G7-CAL / M5. The ARQ entry point runs against PostgreSQL (the frozen `tasks`
projection is created in the isolated test database) and a deterministic Google Calendar
double at the HTTP boundary. No real Google call, credential or secret is involved.

A3.1 owns the real `tasks` migration. Until it lands, the suite creates the same projection
that `services.task_event_store` declares; `checkfirst=True` makes the fixture harmless once
the migration exists.
"""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import unquote

import httpx
import pytest
from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.services.calendar_gateway import GoogleCalendarGateway
from app.services.google_calendar import GoogleCalendarService
from app.services.task_event_store import TASKS
from app.services.task_event_sync import (
    TASK_ID_PROPERTY,
    USER_ID_PROPERTY,
    CalendarGateway,
    TaskSnapshot,
    deterministic_event_id,
)
from app.workers.jobs.calendar_sync import (
    GATEWAYS_KEY,
    SESSIONS_KEY,
    sync_task_event,
)
from tests.integration.actors import Actor, make_actor

pytestmark = pytest.mark.integration

CALENDAR_ID = "maulwurf@group.calendar.example"
START = datetime(2026, 11, 2, 14, 0, tzinfo=UTC)


@pytest.fixture(scope="module", autouse=True)
def _tasks_projection(migrated_database_url: str) -> Iterator[None]:
    """Create the J3.4 projection of `tasks` for this module, then remove it.

    A3.1 owns the real table. The projection lives in the shared migrated database, so it
    is dropped after the module; otherwise the migration catalog tests (which compare the
    exact table set) would see an unknown table.
    """

    async def exists() -> bool:
        engine = create_async_engine(migrated_database_url)
        async with engine.connect() as conn:
            found = await conn.run_sync(lambda sync: inspect(sync).has_table("tasks"))
        await engine.dispose()
        return bool(found)

    async def create() -> None:
        engine = create_async_engine(migrated_database_url)
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync: TASKS.create(sync, checkfirst=True))
        await engine.dispose()

    async def drop() -> None:
        engine = create_async_engine(migrated_database_url)
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync: TASKS.drop(sync, checkfirst=True))
        await engine.dispose()

    existed = asyncio.run(exists())
    asyncio.run(create())
    try:
        yield
    finally:
        if not existed:
            asyncio.run(drop())


class FakeGoogle:
    """Minimal in-memory Calendar events endpoint with idempotent inserts."""

    def __init__(self) -> None:
        self.events: dict[tuple[str, str], dict[str, Any]] = {}
        self.insert_requests = 0
        self.get_requests = 0
        self.lose_next_response = False
        self.timeouts_before_insert = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        parts = [unquote(part) for part in request.url.path.strip("/").split("/")]
        # The service base URL may carry a prefix (the test uses `/v3`).
        start = parts.index("calendars")
        parts = parts[start:]
        assert parts[0] == "calendars" and parts[2] == "events", request.url
        calendar_id = parts[1]
        if request.method == "POST":
            self.insert_requests += 1
            body = json.loads(request.content)
            event_id = body["id"]
            if self.timeouts_before_insert:
                self.timeouts_before_insert -= 1
                raise httpx.ReadTimeout("insert timed out", request=request)
            if self.lose_next_response:
                self.lose_next_response = False
                self.events[(calendar_id, event_id)] = body
                raise httpx.ReadTimeout("response lost", request=request)
            if (calendar_id, event_id) in self.events:
                return httpx.Response(409, json={"error": {"code": 409}})
            self.events[(calendar_id, event_id)] = body
            return httpx.Response(200, json=body)
        event_id = parts[3]
        self.get_requests += 1
        event = self.events.get((calendar_id, event_id))
        if event is None:
            return httpx.Response(404, json={"error": {"code": 404}})
        return httpx.Response(200, json=event)


@asynccontextmanager
async def _same_session(db: AsyncSession) -> AsyncIterator[AsyncSession]:
    yield db


def _session_factory(db: AsyncSession) -> Callable[[], Any]:
    def factory() -> Any:
        return _same_session(db)

    return factory


def _gateway_factory(
    fake: FakeGoogle,
) -> Callable[[TaskSnapshot], Awaitable[CalendarGateway]]:
    async def factory(_snapshot: TaskSnapshot) -> CalendarGateway:
        service = GoogleCalendarService(
            access_token="test-access-token",  # noqa: S106 - fixture, not a credential
            base_url="https://calendar.example/v3",
            transport=httpx.MockTransport(fake.handler),
            max_retries=0,
            sleeper=lambda _delay: None,
        )
        return GoogleCalendarGateway(service)

    return factory


def _ctx(db: AsyncSession, fake: FakeGoogle | None = None) -> dict[str, Any]:
    ctx: dict[str, Any] = {SESSIONS_KEY: _session_factory(db), "job_try": 1}
    if fake is not None:
        ctx[GATEWAYS_KEY] = _gateway_factory(fake)
    return ctx


async def _insert_task(
    db: AsyncSession, actor: Actor, **overrides: Any
) -> tuple[uuid.UUID, int]:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "user_id": actor.user_id,
        "version": 1,
        "calendar_sync_status": "pending",
        "calendar_id": CALENDAR_ID,
        "calendar_event_id": None,
        "titulo": "Entregar informe",
        "detalle": None,
        "timezone": "America/Bogota",
        "all_day": False,
        "event_start_at": START,
        "event_end_at": START + timedelta(hours=1),
        "event_start_date": None,
        "event_end_date_exclusive": None,
        "updated_at": START,
    }
    values.update(overrides)
    await db.execute(TASKS.insert().values(**values))
    await db.flush()
    return values["id"], values["version"]


async def _status(db: AsyncSession, task_id: uuid.UUID) -> tuple[str, str | None]:
    row = (
        await db.execute(
            select(TASKS.c.calendar_sync_status, TASKS.c.calendar_event_id).where(
                TASKS.c.id == task_id
            )
        )
    ).one()
    return row.calendar_sync_status, row.calendar_event_id


def _identity_event(task_id: uuid.UUID, user_id: uuid.UUID, **event: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": deterministic_event_id(task_id),
        "summary": "previous body",
        "extendedProperties": {
            "private": {
                TASK_ID_PROPERTY: str(task_id),
                USER_ID_PROPERTY: str(user_id),
            }
        },
    }
    body.update(event)
    return body


async def test_double_delivery_creates_exactly_one_event(db_session: AsyncSession) -> None:
    """I-S3-JF-03: a repeated confirmation/delivery reconciles the same event."""
    fake = FakeGoogle()
    actor = await make_actor(db_session)
    task_id, version = await _insert_task(db_session, actor)
    ctx = _ctx(db_session, fake)

    await sync_task_event(ctx, str(task_id), version)
    await sync_task_event(ctx, str(task_id), version)

    assert fake.insert_requests == 2
    assert len(fake.events) == 1
    assert (await _status(db_session, task_id)) == (
        "synced",
        deterministic_event_id(task_id),
    )


async def test_lost_response_queries_the_event_before_giving_up(
    db_session: AsyncSession,
) -> None:
    """I-S3-JF-03: the insert succeeded but its response was lost."""
    fake = FakeGoogle()
    fake.lose_next_response = True
    actor = await make_actor(db_session)
    task_id, version = await _insert_task(db_session, actor)

    await sync_task_event(_ctx(db_session, fake), str(task_id), version)

    assert fake.insert_requests == 1
    assert fake.get_requests == 1
    assert len(fake.events) == 1
    assert (await _status(db_session, task_id))[0] == "synced"


async def test_timeout_without_creation_retries_the_same_id(
    db_session: AsyncSession,
) -> None:
    """I-S3-JF-03: a timeout that really did not write is retried after the lookup."""
    fake = FakeGoogle()
    fake.timeouts_before_insert = 1
    actor = await make_actor(db_session)
    task_id, version = await _insert_task(db_session, actor)

    await sync_task_event(_ctx(db_session, fake), str(task_id), version)

    assert fake.insert_requests == 2
    assert fake.get_requests == 1
    assert len(fake.events) == 1
    assert set(fake.events) == {(CALENDAR_ID, deterministic_event_id(task_id))}
    assert (await _status(db_session, task_id))[0] == "synced"


async def test_conflict_with_matching_identity_is_reconciled(
    db_session: AsyncSession,
) -> None:
    """I-S3-JF-03: 409 from an earlier delivery does not duplicate or overwrite."""
    fake = FakeGoogle()
    actor = await make_actor(db_session)
    task_id, version = await _insert_task(db_session, actor)
    original = _identity_event(task_id, actor.user_id)
    fake.events[(CALENDAR_ID, deterministic_event_id(task_id))] = original

    await sync_task_event(_ctx(db_session, fake), str(task_id), version)

    assert fake.events[(CALENDAR_ID, deterministic_event_id(task_id))] == original
    assert (await _status(db_session, task_id)) == (
        "synced",
        deterministic_event_id(task_id),
    )


async def test_conflict_with_foreign_identity_is_not_adopted(
    db_session: AsyncSession,
) -> None:
    """I-S3-JF-03: a foreign event that occupies the ID is never overwritten."""
    fake = FakeGoogle()
    actor = await make_actor(db_session)
    task_id, version = await _insert_task(db_session, actor)
    foreign = _identity_event(uuid.uuid4(), actor.user_id)
    fake.events[(CALENDAR_ID, deterministic_event_id(task_id))] = foreign

    await sync_task_event(_ctx(db_session, fake), str(task_id), version)

    assert fake.events[(CALENDAR_ID, deterministic_event_id(task_id))] == foreign
    assert (await _status(db_session, task_id)) == ("failed", None)


async def test_obsolete_task_version_is_discarded(db_session: AsyncSession) -> None:
    """I-S3-JF-03: a job for an old version makes no Google call and no write."""
    fake = FakeGoogle()
    actor = await make_actor(db_session)
    task_id, _version = await _insert_task(db_session, actor, version=2)

    await sync_task_event(_ctx(db_session, fake), str(task_id), 1)

    assert fake.insert_requests == fake.get_requests == 0
    assert fake.events == {}
    assert (await _status(db_session, task_id)) == ("pending", None)


async def test_missing_google_integration_blocks_the_task(
    db_session: AsyncSession,
) -> None:
    """I-S3-JF-03: without a connected credential the job writes nothing and blocks."""
    actor = await make_actor(db_session)
    task_id, version = await _insert_task(db_session, actor)

    await sync_task_event(_ctx(db_session), str(task_id), version)

    assert (await _status(db_session, task_id)) == ("blocked", None)

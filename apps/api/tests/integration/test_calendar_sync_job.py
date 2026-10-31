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
from pydantic import SecretStr
from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import settings
from app.core.crypto import decrypt, encrypt
from app.core.db import set_current_user
from app.models import GoogleCredential
from app.services.calendar_gateway import GoogleCalendarGateway
from app.services.google_calendar import GoogleCalendarService
from app.services.task_event_store import TASKS, SqlTaskEventStore
from app.services.task_event_sync import (
    TASK_ID_PROPERTY,
    USER_ID_PROPERTY,
    CalendarGateway,
    TaskSnapshot,
    build_event_body,
    deterministic_event_id,
)
from app.workers.jobs.calendar_sync import (
    CALENDAR_TRANSPORT_KEY,
    GATEWAYS_KEY,
    SESSIONS_KEY,
    TOKEN_TRANSPORT_KEY,
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
        self.patch_requests = 0
        self.unauthorized_before_insert = 0
        self.requests: list[httpx.Request] = []
        self.lose_next_response = False
        self.timeouts_before_insert = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
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
            if self.unauthorized_before_insert:
                self.unauthorized_before_insert -= 1
                return httpx.Response(401, json={"error": {"code": 401}})
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
        if request.method == "PATCH":
            self.patch_requests += 1
            stored = self.events.get((calendar_id, event_id))
            if stored is None:
                return httpx.Response(404, json={"error": {"code": 404}})
            stored.update(json.loads(request.content))
            return httpx.Response(200, json=stored)
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


def _scoped_session_factory(db: AsyncSession) -> Callable[[], Any]:
    """Sessions with `session_scope`'s commit/rollback, on a real (savepoint) transaction.

    The test's outer transaction keeps cases isolated; `create_savepoint` makes the nested
    commit/rollback real SQL, so a rollback actually discards writes such as a disconnect.
    """

    @asynccontextmanager
    async def factory() -> AsyncIterator[AsyncSession]:
        assert db.bind is not None
        nested = AsyncSession(bind=db.bind, join_transaction_mode="create_savepoint")
        try:
            yield nested
            await nested.commit()
        except BaseException:
            await nested.rollback()
            raise
        finally:
            await nested.close()

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


async def test_conflict_with_matching_identity_and_body_is_reconciled(
    db_session: AsyncSession,
) -> None:
    """I-S3-JF-03: 409 from an identical earlier delivery does not duplicate or patch."""
    fake = FakeGoogle()
    actor = await make_actor(db_session)
    task_id, version = await _insert_task(db_session, actor)
    snapshot = await _snapshot(db_session, task_id)
    original = {"id": deterministic_event_id(task_id), **build_event_body(snapshot)}
    fake.events[(CALENDAR_ID, deterministic_event_id(task_id))] = original

    await sync_task_event(_ctx(db_session, fake), str(task_id), version)

    assert fake.events[(CALENDAR_ID, deterministic_event_id(task_id))] == original
    assert fake.patch_requests == 0
    assert (await _status(db_session, task_id)) == (
        "synced",
        deterministic_event_id(task_id),
    )


async def test_a_stale_event_body_is_patched_during_the_reconcile(
    db_session: AsyncSession,
) -> None:
    """I-S3-JF-03 fix: the task must not stay `synced` while the event shows old content."""
    fake = FakeGoogle()
    actor = await make_actor(db_session)
    task_id, version = await _insert_task(db_session, actor)
    # J3.6 fix1: the reconcile only PATCHes an event whose ETag it reads.
    stale = _identity_event(task_id, actor.user_id, etag='"etag-stale"')
    fake.events[(CALENDAR_ID, deterministic_event_id(task_id))] = stale

    await sync_task_event(_ctx(db_session, fake), str(task_id), version)

    stored = fake.events[(CALENDAR_ID, deterministic_event_id(task_id))]
    assert stored["summary"] == "Entregar informe"
    assert stored["start"] == {
        "dateTime": "2026-11-02T14:00:00Z",
        "timeZone": "America/Bogota",
    }
    assert stored["end"] == {
        "dateTime": "2026-11-02T15:00:00Z",
        "timeZone": "America/Bogota",
    }
    assert fake.patch_requests == 1
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
    assert fake.patch_requests == 0
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


async def _snapshot(db_session: AsyncSession, task_id: uuid.UUID) -> TaskSnapshot:
    def factory() -> Any:
        return _same_session(db_session)

    snapshot = await SqlTaskEventStore(factory).load(task_id)
    assert snapshot is not None
    return snapshot


class TokenEndpoint:
    """Deterministic Google token endpoint double (refresh_token grant only)."""

    def __init__(self, *, access_token: str = "fresh-access") -> None:  # noqa: S107
        self.access_token = access_token
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(
            200, json={"access_token": self.access_token, "expires_in": 3599}
        )


class RejectingTokenEndpoint:
    """Token endpoint double that answers `invalid_grant` (revoked refresh token)."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(
            400, json={"error": "invalid_grant", "error_description": "revoked"}
        )


TokenResponder = Callable[[httpx.Request], httpx.Response]


def _google_transport(fake: FakeGoogle, tokens: TokenResponder) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return tokens(request)
        return fake.handler(request)

    return httpx.MockTransport(handler)


async def _connected_credential(
    db_session: AsyncSession,
    actor: Actor,
    *,
    access_token: str,
    expires_at: datetime,
) -> GoogleCredential:
    access_blob, key_version = encrypt(
        access_token, user_id=actor.user_id, field="google_credentials.access_token"
    )
    refresh_blob, _ = encrypt(
        "refresh-token", user_id=actor.user_id, field="google_credentials.refresh_token"
    )
    credential = GoogleCredential(
        user_id=actor.user_id,
        access_token_enc=access_blob,
        refresh_token_enc=refresh_blob,
        key_version=key_version,
        status="connected",
        scopes=["https://www.googleapis.com/auth/calendar.events"],
        token_expires_at=expires_at,
    )
    db_session.add(credential)
    await db_session.flush()
    # The job binds the tenant itself; the fixture's open transaction needs the same GUC.
    await set_current_user(db_session, actor.user_id)
    return credential


def _real_google_ctx(
    db_session: AsyncSession, fake: FakeGoogle, tokens: TokenResponder
) -> dict[str, Any]:
    transport = _google_transport(fake, tokens)
    return {
        SESSIONS_KEY: _session_factory(db_session),
        CALENDAR_TRANSPORT_KEY: transport,
        TOKEN_TRANSPORT_KEY: transport,
        "job_try": 1,
    }


def _scoped_google_ctx(
    db_session: AsyncSession, fake: FakeGoogle, tokens: TokenResponder
) -> dict[str, Any]:
    """Real commit/rollback sessions, so a disconnect is rolled back or persisted for real."""
    transport = _google_transport(fake, tokens)
    return {
        SESSIONS_KEY: _scoped_session_factory(db_session),
        CALENDAR_TRANSPORT_KEY: transport,
        TOKEN_TRANSPORT_KEY: transport,
        "job_try": 1,
    }


@pytest.fixture
def google_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "google_client_id", "test-client-id")
    monkeypatch.setattr(settings, "google_client_secret", SecretStr("test-client-secret"))


async def test_an_expired_access_token_is_refreshed_before_the_call(
    db_session: AsyncSession, google_client: None
) -> None:
    """J3.4 fix: an expired token is renewed instead of failing the task."""
    fake = FakeGoogle()
    tokens = TokenEndpoint()
    actor = await make_actor(db_session)
    task_id, version = await _insert_task(db_session, actor)
    credential = await _connected_credential(
        db_session,
        actor,
        access_token="expired-access",
        expires_at=datetime.now(UTC) - timedelta(minutes=5),
    )

    await sync_task_event(_real_google_ctx(db_session, fake, tokens), str(task_id), version)

    assert len(tokens.requests) == 1
    form = dict(httpx.QueryParams(tokens.requests[0].content.decode()))
    assert form["grant_type"] == "refresh_token"
    assert form["refresh_token"] == "refresh-token"
    assert len(fake.events) == 1
    request = fake.requests[0]
    assert request.headers["authorization"] == "Bearer fresh-access"
    assert (await _status(db_session, task_id))[0] == "synced"
    refreshed = decrypt(
        credential.access_token_enc,
        key_version=credential.key_version,
        user_id=actor.user_id,
        field="google_credentials.access_token",
    )
    assert refreshed == "fresh-access"
    assert credential.last_refresh_at is not None
    assert credential.token_expires_at is not None
    assert credential.token_expires_at > datetime.now(UTC)


async def test_a_401_refreshes_once_and_the_delivery_succeeds(
    db_session: AsyncSession, google_client: None
) -> None:
    """J3.4 fix: a token invalidated before its expiry is renewed on the 401."""
    fake = FakeGoogle()
    fake.unauthorized_before_insert = 1
    tokens = TokenEndpoint()
    actor = await make_actor(db_session)
    task_id, version = await _insert_task(db_session, actor)
    await _connected_credential(
        db_session,
        actor,
        access_token="still-fresh",
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )

    await sync_task_event(_real_google_ctx(db_session, fake, tokens), str(task_id), version)

    assert len(tokens.requests) == 1
    assert fake.insert_requests == 2
    assert fake.requests[0].headers["authorization"] == "Bearer still-fresh"
    assert fake.requests[1].headers["authorization"] == "Bearer fresh-access"
    assert len(fake.events) == 1
    assert (await _status(db_session, task_id))[0] == "synced"


async def test_a_revoked_refresh_token_disconnects_through_the_real_scope(
    db_session: AsyncSession, google_client: None
) -> None:
    """J3.4 fix: the disconnect survives `session_scope`'s commit instead of being rolled back."""
    fake = FakeGoogle()
    tokens = RejectingTokenEndpoint()
    actor = await make_actor(db_session)
    task_id, version = await _insert_task(db_session, actor)
    credential = await _connected_credential(
        db_session,
        actor,
        access_token="expired-access",
        expires_at=datetime.now(UTC) - timedelta(minutes=5),
    )

    await sync_task_event(
        _scoped_google_ctx(db_session, fake, tokens), str(task_id), version
    )

    assert len(tokens.requests) == 1
    assert fake.insert_requests == 0
    assert (await _status(db_session, task_id)) == ("blocked", None)
    await db_session.refresh(credential)
    assert credential.status == "disconnected"


async def test_a_revoked_refresh_token_on_the_401_path_also_disconnects(
    db_session: AsyncSession, google_client: None
) -> None:
    """J3.4 fix: the reactive refresh commits its disconnect too."""
    fake = FakeGoogle()
    fake.unauthorized_before_insert = 1
    tokens = RejectingTokenEndpoint()
    actor = await make_actor(db_session)
    task_id, version = await _insert_task(db_session, actor)
    credential = await _connected_credential(
        db_session,
        actor,
        access_token="still-fresh",
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )

    await sync_task_event(
        _scoped_google_ctx(db_session, fake, tokens), str(task_id), version
    )

    assert len(tokens.requests) == 1
    assert fake.insert_requests == 1
    assert (await _status(db_session, task_id)) == ("blocked", None)
    await db_session.refresh(credential)
    assert credential.status == "disconnected"


async def test_an_unexpected_failure_is_raised_for_the_arq_retry_budget(
    db_session: AsyncSession,
) -> None:
    """J3.4 fix: a transient database/provider bug must reach ARQ instead of being swallowed."""
    actor = await make_actor(db_session)
    task_id, version = await _insert_task(db_session, actor)

    async def broken(_snapshot: TaskSnapshot) -> CalendarGateway:
        raise RuntimeError("database unavailable")

    ctx = {
        SESSIONS_KEY: _session_factory(db_session),
        GATEWAYS_KEY: broken,
        "job_try": 1,
    }

    with pytest.raises(RuntimeError, match="database unavailable"):
        await sync_task_event(ctx, str(task_id), version)

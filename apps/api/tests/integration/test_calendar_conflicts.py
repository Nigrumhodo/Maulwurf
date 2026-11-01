"""I-S3-JF-07 (J3.8): conflicts (overlap ≠ clustering) and manual Calendar refresh.

Gate: M5 / G7-CAL. Runs against PostgreSQL with the real migration chain. Google is
replaced at the service boundary by a deterministic source, so no real Google call,
credential or secret is involved.

The `tasks` table is A3.1's; it is not in `develop` yet. This module creates the frozen
projection declared by the conflicts service for its own cases and removes it afterwards,
so `deadline_clashes` is exercised against a real SQL read. The projection is never left
behind for the migration-catalog tests.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Integer,
    MetaData,
    Table,
    Text,
    event,
    func,
    inspect,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core import crypto
from app.core import db as core_db
from app.core.config import settings
from app.core.db import get_session
from app.main import app
from app.models import CalendarEvent, CalendarSettings, GoogleCredential, User
from app.services import calendar_sync as calendar_sync_service
from app.services import sessions
from app.services.calendar_sync import (
    MANUAL_SYNC_COOLDOWN_SECONDS,
    CalendarEventInvalid,
    CalendarRead,
    CalendarWindow,
    RemoteCalendarEvent,
    refresh_calendar_mirror,
)
from app.services.google_calendar import (
    GoogleCalendarInvalidGrant,
    GoogleCalendarTransientError,
    GoogleCalendarUnauthorized,
)
from app.services.oauth_google import CALENDAR_SCOPES
from tests.integration.actors import Actor, make_actor

pytestmark = pytest.mark.integration

CALENDAR = "primary"
ARCHIVE = "archive"
NOW = datetime.now(UTC)

# Frozen `tasks` read/write projection (DESIGN §2.8). Only the columns this suite needs;
# the real migration owns the FKs, checks and the rest of the table.
TASKS = Table(
    "tasks",
    MetaData(),
    Column("id", PgUUID(as_uuid=True), primary_key=True),
    Column("user_id", PgUUID(as_uuid=True), nullable=False),
    Column("source", Text, nullable=False, server_default="extraction"),
    Column("tipo", Text, nullable=False),
    Column("titulo", Text, nullable=False),
    Column("detalle", Text),
    Column("due_date", Date),
    Column("due_at", DateTime(timezone=True)),
    Column("timezone", Text, nullable=False, server_default="UTC"),
    Column("all_day", Boolean, nullable=False, server_default=text("true")),
    Column("date_status", Text, nullable=False, server_default="resolved"),
    Column("status", Text, nullable=False, server_default="needs_review"),
    Column("calendar_sync_status", Text, nullable=False, server_default="not_requested"),
    Column("version", Integer, nullable=False, server_default="1"),
    Column("due_version", Integer, nullable=False, server_default="1"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)


@pytest.fixture(scope="module", autouse=True)
def _tasks_projection(migrated_database_url: str) -> Iterator[None]:
    """Create A3.1's frozen `tasks` shape for this module, then remove it."""

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


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    async def override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = override
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=settings.allowed_origin
    ) as http:
        yield http
    app.dependency_overrides.clear()


@asynccontextmanager
async def _production_client(migrated_database_url: str) -> AsyncIterator[AsyncClient]:
    """HTTP client backed by a role-bound factory with real commits.

    The standard `db_session` fixture joins an outer transaction through savepoints, so a
    service `commit()` only releases a savepoint and row locks survive until teardown. Some
    lock/concurrency cases must observe the production semantics (a commit really releases
    the lock), so this client opens one session per request like `get_session` does.
    """
    engine = create_async_engine(migrated_database_url, poolclass=NullPool)
    event.listen(engine.sync_engine, "begin", core_db._apply_tenant_settings)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = override
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=settings.allowed_origin
        ) as http:
            yield http
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


class FakeSource:
    """Deterministic reader; records the window and can fail on demand."""

    def __init__(
        self,
        events: list[RemoteCalendarEvent] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.events = events or []
        self.error = error
        self.calls: list[tuple[str, datetime, datetime]] = []

    async def list_events(
        self, calendar_id: str, *, time_min: datetime, time_max: datetime
    ) -> CalendarRead:
        self.calls.append((calendar_id, time_min, time_max))
        if self.error is not None:
            raise self.error
        return CalendarRead(
            events=tuple(
                event for event in self.events if event.calendar_id == calendar_id
            )
        )


def _timed(
    event_id: str,
    start: datetime,
    *,
    hours: float = 1,
    calendar_id: str = CALENDAR,
    remote_status: str = "confirmed",
) -> RemoteCalendarEvent:
    return RemoteCalendarEvent(
        calendar_id=calendar_id,
        event_id=event_id,
        title=event_id,
        start_at=start,
        end_at=start + timedelta(hours=hours),
        timezone="America/Bogota",
        remote_status=remote_status,
    )


async def _settings(
    db: AsyncSession,
    actor: Actor,
    *,
    read_calendars: list[str] | None = None,
    coverage: bool = True,
) -> CalendarSettings:
    window = CalendarWindow.default(now=NOW, timezone="UTC")
    row = CalendarSettings(
        user_id=actor.user_id,
        calendar_write_calendar_id="maulwurf@group.calendar.example",
        calendar_read_calendar_ids=read_calendars if read_calendars is not None else [CALENDAR],
        calendar_last_synced_at=NOW if coverage else None,
        calendar_coverage_from=window.start if coverage else None,
        calendar_coverage_to=window.end if coverage else None,
        calendar_coverage_calendar_ids=[CALENDAR] if coverage else [],
    )
    db.add(row)
    await db.flush()
    return row


async def _credential(
    db: AsyncSession,
    actor: Actor,
    *,
    scopes: list[str] | None = None,
    status: str = "connected",
) -> GoogleCredential:
    access, version = crypto.encrypt(
        "ya29.integration-access",
        user_id=actor.user_id,
        field="google_credentials.access_token",
    )
    refresh, _ = crypto.encrypt(
        "1//integration-refresh",
        user_id=actor.user_id,
        field="google_credentials.refresh_token",
    )
    row = GoogleCredential(
        user_id=actor.user_id,
        access_token_enc=access,
        refresh_token_enc=refresh,
        key_version=version,
        scopes=scopes if scopes is not None else list(CALENDAR_SCOPES),
        token_expires_at=NOW + timedelta(days=30),
        status=status,
    )
    db.add(row)
    await db.flush()
    return row


async def _seed_committed_actor(migrated_database_url: str) -> Actor:
    """Create an actor whose rows are committed on their own connection.

    A second connection can only see (and lock) the credential row once it is committed,
    which is what the row-lock probes need. The standard `db_session` fixture keeps its
    writes inside an open transaction, so it cannot be used for those cases.
    """
    engine = create_async_engine(migrated_database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            user = User(
                google_sub=f"sub-{uuid.uuid4()}",
                email=f"{uuid.uuid4().hex[:8]}@example.test",
            )
            db.add(user)
            await db.flush()
            access, version = crypto.encrypt(
                "ya29.committed-access",
                user_id=user.id,
                field="google_credentials.access_token",
            )
            db.add(
                CalendarSettings(
                    user_id=user.id,
                    calendar_read_calendar_ids=[CALENDAR],
                )
            )
            db.add(
                GoogleCredential(
                    user_id=user.id,
                    access_token_enc=access,
                    key_version=version,
                    scopes=list(CALENDAR_SCOPES),
                    token_expires_at=datetime.now(UTC) + timedelta(hours=1),
                    status="connected",
                )
            )
            issued = await sessions.create_session(db, user.id)
            await db.commit()
        core_db.clear_user()
        return Actor(user, issued)
    finally:
        await engine.dispose()


async def _delete_committed_user(
    migrated_database_url: str, user_id: uuid.UUID
) -> None:
    engine = create_async_engine(migrated_database_url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM users WHERE id = :user_id"), {"user_id": user_id}
            )
    finally:
        await engine.dispose()


async def _insert_event(
    db: AsyncSession,
    actor: Actor,
    *,
    event_id: str,
    calendar_id: str = CALENDAR,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    start_date: date | None = None,
    end_date_exclusive: date | None = None,
    remote_status: str = "confirmed",
) -> CalendarEvent:
    row = CalendarEvent(
        user_id=actor.user_id,
        calendar_id=calendar_id,
        event_id=event_id,
        source="google",
        title=event_id,
        start_at=start_at,
        end_at=end_at,
        start_date=start_date,
        end_date_exclusive=end_date_exclusive,
        timezone="America/Bogota" if start_at is not None else None,
        remote_status=remote_status,
        last_synced_at=NOW,
    )
    db.add(row)
    await db.flush()
    return row


async def _insert_task(
    db: AsyncSession,
    actor: Actor,
    *,
    tipo: str = "examen",
    due_date: date | None = None,
    due_at: datetime | None = None,
    status: str = "needs_review",
    timezone: str = "America/Bogota",
) -> uuid.UUID:
    task_id = uuid.uuid4()
    await db.execute(
        TASKS.insert().values(
            id=task_id,
            user_id=actor.user_id,
            tipo=tipo,
            titulo=f"{tipo} de prueba",
            due_date=due_date,
            due_at=due_at,
            timezone=timezone,
            all_day=due_date is not None,
            date_status="resolved",
            status=status,
        )
    )
    await db.flush()
    return task_id


async def _set_timezone(db: AsyncSession, actor: Actor, timezone: str) -> None:
    user = await db.get(User, actor.user_id)
    assert user is not None
    user.timezone = timezone
    await db.flush()


async def _conflicts(
    client: AsyncClient,
    actor: Actor,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Any:
    params: dict[str, str] = {}
    if date_from is not None:
        params["from"] = date_from.isoformat()
    if date_to is not None:
        params["to"] = date_to.isoformat()
    return await client.get("/calendar/conflicts", params=params, cookies=actor.cookies)


async def _post_sync(client: AsyncClient, actor: Actor) -> Any:
    return await client.post(
        "/calendar/sync", cookies=actor.cookies, headers=actor.headers
    )


def _patch_source(monkeypatch: pytest.MonkeyPatch, source: FakeSource) -> None:
    monkeypatch.setattr(
        calendar_sync_service, "google_event_source", lambda _token: source
    )


# --- GET /calendar/conflicts -----------------------------------------------------------


async def test_openapi_exposes_the_conflicts_and_sync_contract(
    client: AsyncClient,
) -> None:
    paths = (await client.get("/openapi.json")).json()["paths"]

    conflicts = paths["/calendar/conflicts"]["get"]
    assert {"200", "401", "422"} <= set(conflicts["responses"])
    sync = paths["/calendar/sync"]["post"]
    assert {"202", "401", "403", "409", "429", "503"} <= set(sync["responses"])


async def test_overlap_is_a_time_conflict_and_same_day_events_are_not(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await make_actor(db_session)
    await _settings(db_session, actor)
    start = (NOW + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    await _insert_event(
        db_session, actor, event_id="a", start_at=start, end_at=start + timedelta(hours=1)
    )
    await _insert_event(
        db_session,
        actor,
        event_id="b",
        start_at=start + timedelta(minutes=30),
        end_at=start + timedelta(minutes=90),
    )
    # Same day, but these do not share time and one of them is all-day.
    await _insert_event(
        db_session,
        actor,
        event_id="c",
        start_at=start + timedelta(hours=5),
        end_at=start + timedelta(hours=6),
    )
    await _insert_event(
        db_session,
        actor,
        event_id="all-day",
        start_date=start.date(),
        end_date_exclusive=start.date() + timedelta(days=1),
    )

    response = await _conflicts(client, actor)

    assert response.status_code == 200
    body = response.json()
    assert [(pair["a"]["event_id"], pair["b"]["event_id"]) for pair in body["time_conflicts"]] == [
        ("a", "b")
    ]
    assert body["deadline_clashes"] == []
    assert body["coverage_ok"] is True
    reference = datetime.now(UTC)
    assert body["from"] == (reference.date() - timedelta(days=7)).isoformat()
    assert body["to"] == (reference.date() + timedelta(days=60)).isoformat()


async def test_two_deadlines_on_the_same_day_cluster_without_a_time_conflict(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await make_actor(db_session)
    await _settings(db_session, actor)
    day = (NOW + timedelta(days=2)).date()
    first = await _insert_task(db_session, actor, due_date=day)
    second = await _insert_task(db_session, actor, tipo="entrega", due_date=day)

    body = (await _conflicts(client, actor)).json()

    assert body["time_conflicts"] == []
    assert len(body["deadline_clashes"]) == 1
    clash = body["deadline_clashes"][0]
    assert clash["date"] == day.isoformat()
    assert {item["task_id"] for item in clash["items"]} == {str(first), str(second)}
    assert {item["tipo"] for item in clash["items"]} == {"examen", "entrega"}


async def test_a_single_deadline_and_different_days_do_not_cluster(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await make_actor(db_session)
    await _settings(db_session, actor)
    day = (NOW + timedelta(days=2)).date()
    await _insert_task(db_session, actor, due_date=day)
    await _insert_task(db_session, actor, due_date=day + timedelta(days=1))

    body = (await _conflicts(client, actor)).json()

    assert body["deadline_clashes"] == []


async def test_due_at_clusters_on_the_user_local_day(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await make_actor(db_session)
    await _set_timezone(db_session, actor, "America/Bogota")
    await _settings(db_session, actor)
    # 02:00Z is still the previous day in Bogota (UTC-5).
    instant = (NOW + timedelta(days=2)).replace(
        hour=2, minute=0, second=0, microsecond=0
    )
    await _insert_task(db_session, actor, due_at=instant)
    await _insert_task(db_session, actor, due_at=instant + timedelta(hours=1))
    expected = instant.astimezone(ZoneInfo("America/Bogota")).date()

    body = (await _conflicts(client, actor)).json()

    assert [clash["date"] for clash in body["deadline_clashes"]] == [expected.isoformat()]
    assert body["time_conflicts"] == []


async def test_only_active_deadline_types_cluster(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await make_actor(db_session)
    await _settings(db_session, actor)
    day = (NOW + timedelta(days=2)).date()
    await _insert_task(db_session, actor, tipo="recordatorio", due_date=day)
    await _insert_task(db_session, actor, tipo="recomendacion", due_date=day)
    await _insert_task(db_session, actor, due_date=day, status="dismissed")
    await _insert_task(db_session, actor, due_date=day, status="completed")

    body = (await _conflicts(client, actor)).json()

    assert body["deadline_clashes"] == []


async def test_cancelled_and_missing_events_are_not_current_conflicts(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await make_actor(db_session)
    await _settings(db_session, actor)
    start = (NOW + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    await _insert_event(
        db_session,
        actor,
        event_id="live",
        start_at=start,
        end_at=start + timedelta(hours=1),
    )
    await _insert_event(
        db_session,
        actor,
        event_id="cancelled",
        start_at=start,
        end_at=start + timedelta(hours=1),
        remote_status="cancelled",
    )
    await _insert_event(
        db_session,
        actor,
        event_id="missing",
        start_at=start,
        end_at=start + timedelta(hours=1),
        remote_status="missing",
    )

    body = (await _conflicts(client, actor)).json()

    assert body["time_conflicts"] == []


async def test_a_deselected_calendar_is_not_presented(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await make_actor(db_session)
    await _settings(db_session, actor, read_calendars=[CALENDAR])
    start = (NOW + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    await _insert_event(
        db_session,
        actor,
        event_id="hidden",
        calendar_id=ARCHIVE,
        start_at=start,
        end_at=start + timedelta(hours=1),
    )
    await _insert_event(
        db_session,
        actor,
        event_id="visible",
        start_at=start,
        end_at=start + timedelta(hours=1),
    )

    body = (await _conflicts(client, actor)).json()

    assert [(pair["a"]["event_id"], pair["b"]["event_id"]) for pair in body["time_conflicts"]] == []


async def test_conflicts_are_tenant_scoped(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await make_actor(db_session)
    other = await make_actor(db_session)
    await _settings(db_session, actor)
    await _settings(db_session, other)
    start = (NOW + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    await _insert_event(
        db_session,
        actor,
        event_id="mine",
        start_at=start,
        end_at=start + timedelta(hours=1),
    )
    await _insert_event(
        db_session,
        other,
        event_id="theirs",
        start_at=start,
        end_at=start + timedelta(hours=1),
    )
    day = (NOW + timedelta(days=2)).date()
    await _insert_task(db_session, other, due_date=day)
    await _insert_task(db_session, other, due_date=day)

    body = (await _conflicts(client, actor)).json()

    assert body["time_conflicts"] == []
    assert body["deadline_clashes"] == []


async def test_conflicts_reject_an_inverted_or_oversized_range(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await make_actor(db_session)

    inverted = await _conflicts(
        client, actor, date_from=date(2026, 11, 3), date_to=date(2026, 11, 2)
    )
    oversized = await _conflicts(
        client, actor, date_from=date(2026, 1, 1), date_to=date(2027, 12, 31)
    )

    assert inverted.status_code == 422
    assert inverted.json()["error"]["code"] == "validation_failed"
    assert oversized.status_code == 422
    assert oversized.json()["error"]["code"] == "validation_failed"


async def test_conflicts_require_a_session(client: AsyncClient) -> None:
    response = await client.get("/calendar/conflicts")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "auth_required"


# --- POST /calendar/sync ---------------------------------------------------------------


async def test_sync_requires_a_session_and_csrf(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await make_actor(db_session)

    anonymous = await client.post("/calendar/sync")
    no_csrf = await client.post(
        "/calendar/sync", cookies=actor.cookies, headers={"Origin": settings.allowed_origin}
    )

    assert anonymous.status_code == 401
    assert no_csrf.status_code == 403
    assert no_csrf.json()["error"]["code"] == "csrf_invalid"


async def test_sync_rejects_a_selection_without_read_calendars(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await make_actor(db_session)
    await _settings(db_session, actor, read_calendars=[])

    response = await _post_sync(client, actor)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "calendar_sync_unavailable"
    assert response.json()["error"]["details"]["reason"] == "no_read_calendars"


async def test_sync_rejects_a_missing_grant(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await make_actor(db_session)
    await _settings(db_session, actor)

    response = await _post_sync(client, actor)

    assert response.status_code == 409
    assert response.json()["error"]["details"]["reason"] == "google_not_connected"


async def test_sync_rejects_a_disconnected_grant(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await make_actor(db_session)
    await _settings(db_session, actor)
    await _credential(db_session, actor, status="disconnected")

    response = await _post_sync(client, actor)

    assert response.status_code == 409
    assert response.json()["error"]["details"]["reason"] == "google_not_connected"


async def test_sync_rejects_a_grant_without_calendar_scopes(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await make_actor(db_session)
    await _settings(db_session, actor)
    await _credential(db_session, actor, scopes=["openid", "email", "profile"])

    response = await _post_sync(client, actor)

    assert response.status_code == 409
    assert response.json()["error"]["details"]["reason"] == "missing_calendar_scopes"


async def test_sync_forces_a_full_refresh_and_returns_202(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = await make_actor(db_session)
    settings_row = await _settings(db_session, actor)
    await _credential(db_session, actor)
    start = (datetime.now(UTC) + timedelta(days=1)).replace(
        hour=14, minute=0, second=0, microsecond=0
    )
    source = FakeSource([_timed("synced", start)])
    _patch_source(monkeypatch, source)

    response = await _post_sync(client, actor)

    assert response.status_code == 202
    body = response.json()
    window = CalendarWindow.default(now=datetime.now(UTC), timezone="UTC")
    assert body["from"] == window.start_date.isoformat()
    assert body["to"] == window.end_date.isoformat()
    assert body["calendars"] == [CALENDAR]
    assert body["inserted"] == 1
    assert body["removed"] == 0
    assert source.calls and source.calls[0][0] == CALENDAR

    await db_session.refresh(settings_row)
    assert settings_row.calendar_last_synced_at is not None
    rows = (
        await db_session.execute(
            select(CalendarEvent).where(CalendarEvent.user_id == actor.user_id)
        )
    ).scalars().all()
    assert {row.event_id for row in rows} == {"synced"}

    mirror = await client.get("/calendar/events", cookies=actor.cookies)
    assert mirror.status_code == 200
    assert {event["event_id"] for event in mirror.json()["events"]} == {"synced"}


async def test_sync_does_not_touch_the_mirror_when_google_fails(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = await make_actor(db_session)
    settings_row = await _settings(db_session, actor)
    await _credential(db_session, actor)
    start = (datetime.now(UTC) + timedelta(days=1)).replace(
        hour=14, minute=0, second=0, microsecond=0
    )
    _patch_source(monkeypatch, FakeSource([_timed("kept", start)]))
    first = await _post_sync(client, actor)
    assert first.status_code == 202
    await db_session.refresh(settings_row)
    synced_at = settings_row.calendar_last_synced_at
    # This case exercises the provider failure, not the cooldown: let the next manual
    # attempt through without waiting for the real window.
    settings_row.calendar_last_manual_sync_at = None
    await db_session.flush()

    _patch_source(
        monkeypatch,
        FakeSource(error=GoogleCalendarTransientError("provider down")),
    )
    failed = await _post_sync(client, actor)

    assert failed.status_code == 503
    assert failed.json()["error"]["code"] == "calendar_sync_temporarily_unavailable"
    await db_session.refresh(settings_row)
    assert settings_row.calendar_last_synced_at == synced_at
    rows = (
        await db_session.execute(
            select(CalendarEvent.event_id).where(CalendarEvent.user_id == actor.user_id)
        )
    ).scalars().all()
    assert rows == ["kept"]


async def test_sync_with_a_revoked_grant_disconnects_and_answers_409(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = await make_actor(db_session)
    await _settings(db_session, actor)
    credential = await _credential(db_session, actor)
    _patch_source(
        monkeypatch, FakeSource(error=GoogleCalendarInvalidGrant("grant revoked"))
    )

    response = await _post_sync(client, actor)

    assert response.status_code == 409
    assert response.json()["error"]["details"]["reason"] == "google_invalid_grant"
    await db_session.refresh(credential)
    assert credential.status == "disconnected"


async def test_sync_with_a_malformed_provider_read_answers_409(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = await make_actor(db_session)
    await _settings(db_session, actor)
    await _credential(db_session, actor)
    _patch_source(monkeypatch, FakeSource(error=CalendarEventInvalid("event_block_missing")))

    response = await _post_sync(client, actor)

    assert response.status_code == 409
    assert response.json()["error"]["details"]["reason"] == "calendar_read_failed"


async def test_sync_rejects_a_second_call_inside_the_per_user_cooldown(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = await make_actor(db_session)
    settings_row = await _settings(db_session, actor)
    await _credential(db_session, actor)
    source = FakeSource()
    _patch_source(monkeypatch, source)

    first = await _post_sync(client, actor)
    second = await _post_sync(client, actor)

    assert first.status_code == 202
    assert second.status_code == 429
    error = second.json()["error"]
    assert error["code"] == "rate_limited"
    assert error["details"]["reason"] == "calendar_sync_cooldown"
    retry_after = int(second.headers["Retry-After"])
    assert 1 <= retry_after <= MANUAL_SYNC_COOLDOWN_SECONDS
    # The cooldown is checked before the provider: one manual call means one Google read.
    assert len(source.calls) == 1
    await db_session.refresh(settings_row)
    assert settings_row.calendar_last_manual_sync_at is not None


async def test_sync_is_allowed_again_after_the_cooldown_expires(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = await make_actor(db_session)
    settings_row = await _settings(db_session, actor)
    await _credential(db_session, actor)
    settings_row.calendar_last_manual_sync_at = datetime.now(UTC) - timedelta(
        seconds=MANUAL_SYNC_COOLDOWN_SECONDS + 1
    )
    await db_session.flush()
    source = FakeSource()
    _patch_source(monkeypatch, source)

    response = await _post_sync(client, actor)

    assert response.status_code == 202
    assert len(source.calls) == 1


async def test_sync_reports_a_busy_credential_lock_as_transient(
    migrated_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A concurrent holder of the credential row must not make the route wait forever."""
    actor = await _seed_committed_actor(migrated_database_url)
    monkeypatch.setattr(calendar_sync_service, "LOCK_TIMEOUT_MS", 250)
    holder = create_async_engine(migrated_database_url, poolclass=NullPool)
    try:
        async with holder.connect() as conn:
            transaction = await conn.begin()
            try:
                await conn.execute(
                    text(
                        "SELECT user_id FROM google_credentials "
                        "WHERE user_id = :user_id FOR UPDATE"
                    ),
                    {"user_id": actor.user_id},
                )
                # Real commits: the cooldown stamp transaction must release its settings
                # lock so this case can finish and clean up its committed rows.
                async with _production_client(migrated_database_url) as http:
                    response = await _post_sync(http, actor)
            finally:
                await transaction.rollback()
    finally:
        await holder.dispose()
        await _delete_committed_user(migrated_database_url, actor.user_id)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "calendar_sync_temporarily_unavailable"
    assert int(response.headers["Retry-After"]) >= 1


async def test_sync_reports_a_busy_publication_lock_as_transient(
    migrated_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The per-user advisory lock is bounded too; a held one answers 503, not a hang."""
    actor = await _seed_committed_actor(migrated_database_url)
    monkeypatch.setattr(calendar_sync_service, "LOCK_TIMEOUT_MS", 250)
    _patch_source(monkeypatch, FakeSource())
    lock = {
        "namespace": calendar_sync_service._ADVISORY_LOCK_NAMESPACE,
        "user_key": calendar_sync_service._user_lock_key(actor.user_id),
    }
    holder = create_async_engine(migrated_database_url, poolclass=NullPool)
    try:
        async with holder.connect() as conn:
            await conn.execute(
                text("SELECT pg_advisory_lock(:namespace, :user_key)"), lock
            )
            try:
                async with _production_client(migrated_database_url) as http:
                    response = await _post_sync(http, actor)
            finally:
                await conn.execute(
                    text("SELECT pg_advisory_unlock(:namespace, :user_key)"), lock
                )
    finally:
        await holder.dispose()
        await _delete_committed_user(migrated_database_url, actor.user_id)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "calendar_sync_temporarily_unavailable"
    assert int(response.headers["Retry-After"]) >= 1


async def test_a_second_401_after_the_forced_renewal_is_an_invalid_grant(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = await make_actor(db_session)
    await _settings(db_session, actor)
    credential = await _credential(db_session, actor)

    async def _fake_fresh(
        db: AsyncSession,
        stored: GoogleCredential,
        *,
        now: datetime | None = None,
        transport: Any = None,
        force: bool = False,
    ) -> str:
        del db, stored, now, transport
        return "forced-token" if force else "initial-token"

    sources = iter(
        [
            FakeSource(error=GoogleCalendarUnauthorized("expired")),
            FakeSource(error=GoogleCalendarUnauthorized("still rejected")),
        ]
    )
    monkeypatch.setattr(calendar_sync_service, "fresh_access_token", _fake_fresh)
    monkeypatch.setattr(
        calendar_sync_service, "google_event_source", lambda _token: next(sources)
    )

    response = await _post_sync(client, actor)

    assert response.status_code == 409
    assert response.json()["error"]["details"]["reason"] == "google_invalid_grant"
    await db_session.refresh(credential)
    assert credential.status == "disconnected"


async def test_the_credential_lock_is_released_before_google_reads(
    migrated_database_url: str,
) -> None:
    """The Google read must not run while the credential row is still locked.

    The source probes the row from an independent connection with a short `lock_timeout`.
    While the old code held `FOR UPDATE` through every page, the probe timed out; after
    splitting the lock transaction, the probe locks the row and releases it at once.
    """
    actor = await _seed_committed_actor(migrated_database_url)
    probe = create_async_engine(migrated_database_url, poolclass=NullPool)
    lock_states: list[str] = []

    class _LockProbeSource:
        async def list_events(
            self, calendar_id: str, *, time_min: datetime, time_max: datetime
        ) -> CalendarRead:
            del time_min, time_max
            async with probe.connect() as conn:
                await conn.execute(text('SET LOCAL ROLE "maulwurf_app"'))
                await conn.execute(
                    text("SELECT set_config('app.user_id', :user_id, true)"),
                    {"user_id": str(actor.user_id)},
                )
                await conn.execute(text("SET LOCAL lock_timeout = '250ms'"))
                try:
                    await conn.execute(
                        text(
                            "SELECT user_id FROM google_credentials "
                            "WHERE user_id = :user_id FOR UPDATE"
                        ),
                        {"user_id": actor.user_id},
                    )
                except DBAPIError:
                    lock_states.append("locked")
                    raise
                lock_states.append("free")
            return CalendarRead(
                events=(
                    _timed(
                        "during-read",
                        datetime.now(UTC) + timedelta(days=1),
                        calendar_id=calendar_id,
                    ),
                )
            )

    session_engine = create_async_engine(migrated_database_url, poolclass=NullPool)
    event.listen(session_engine.sync_engine, "begin", core_db._apply_tenant_settings)
    factory = async_sessionmaker(session_engine, expire_on_commit=False)
    token = core_db.bind_user(actor.user_id)
    try:
        async with factory() as db:
            result = await refresh_calendar_mirror(
                db, user_id=actor.user_id, source=_LockProbeSource()
            )
            await db.commit()
    finally:
        core_db.reset_user(token)
        core_db.clear_user()
        await session_engine.dispose()
        await probe.dispose()
        await _delete_committed_user(migrated_database_url, actor.user_id)

    assert lock_states == ["free"]
    assert result.calendars == (CALENDAR,)
    assert result.inserted == 1

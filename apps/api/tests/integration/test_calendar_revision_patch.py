"""I-S3-JF-04 (J3.6): ETag patches, external conflicts and the complete/dismiss lifecycle.

Gate: G7-CAL / M5. The ARQ entry point and the lifecycle adapter run against PostgreSQL and
a deterministic Google Calendar double at the HTTP boundary; no real Google call, credential
or secret is involved.

A3.1 (`tasks`), A3.7 (task endpoints) and the S4 `notifications` table are not in `develop`.
This suite creates the frozen §2.8/§2.9 projections in the isolated test database and drops
them afterwards, exactly like I-S3-JF-03 does for `tasks`.
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

from app.core.errors import ApiError
from app.services.calendar_gateway import GoogleCalendarGateway
from app.services.google_calendar import GoogleCalendarService
from app.services.task_event_store import TASKS, SqlTaskEventStore
from app.services.task_event_sync import (
    CalendarGateway,
    TaskSnapshot,
    build_event_body,
    deterministic_event_id,
)
from app.services.task_lifecycle import (
    CALENDAR_EVENT_KEPT_NOTICE,
    TaskLifecycle,
)
from app.services.task_lifecycle_store import NOTIFICATIONS, SqlTaskLifecycleStore
from app.workers.jobs.calendar_sync import GATEWAYS_KEY, SESSIONS_KEY, sync_task_event
from tests.integration.actors import Actor, make_actor

pytestmark = pytest.mark.integration

CALENDAR_ID = "maulwurf@group.calendar.example"
START = datetime(2026, 11, 2, 14, 0, tzinfo=UTC)


@pytest.fixture(scope="module", autouse=True)
def _frozen_tables(migrated_database_url: str) -> Iterator[None]:
    """Create the frozen `tasks`/`notifications` projections, then remove what we created."""

    async def exists(table: str) -> bool:
        engine = create_async_engine(migrated_database_url)
        async with engine.connect() as conn:
            found = await conn.run_sync(lambda sync: inspect(sync).has_table(table))
        await engine.dispose()
        return bool(found)

    async def create(table: str) -> None:
        engine = create_async_engine(migrated_database_url)
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda sync: (TASKS if table == "tasks" else NOTIFICATIONS).create(
                    sync, checkfirst=True
                )
            )
        await engine.dispose()

    async def drop(table: str) -> None:
        engine = create_async_engine(migrated_database_url)
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda sync: (TASKS if table == "tasks" else NOTIFICATIONS).drop(
                    sync, checkfirst=True
                )
            )
        await engine.dispose()

    existed = {table: asyncio.run(exists(table)) for table in ("tasks", "notifications")}
    for table in existed:
        asyncio.run(create(table))
    try:
        yield
    finally:
        for table, was_present in existed.items():
            if not was_present:
                asyncio.run(drop(table))


class FakeGoogle:
    """Calendar events endpoint with ETags and a hook for the external-edit race."""

    def __init__(self) -> None:
        self.events: dict[tuple[str, str], dict[str, Any]] = {}
        self.requests: list[httpx.Request] = []
        self.insert_requests = 0
        self.get_requests = 0
        self.patch_requests = 0
        self.patch_if_match: list[str | None] = []
        self.external_edit_on_patch: dict[str, Any] | None = None
        self._etag_sequence = 0

    def _next_etag(self) -> str:
        self._etag_sequence += 1
        return f'"etag-{self._etag_sequence}"'

    def _store(self, key: tuple[str, str], body: dict[str, Any]) -> dict[str, Any]:
        stored = dict(body)
        stored["etag"] = self._next_etag()
        self.events[key] = stored
        return stored

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        parts = [unquote(part) for part in request.url.path.strip("/").split("/")]
        start = parts.index("calendars")
        parts = parts[start:]
        assert parts[0] == "calendars" and parts[2] == "events", request.url
        calendar_id = parts[1]
        if request.method == "POST":
            self.insert_requests += 1
            body = json.loads(request.content)
            key = (calendar_id, body["id"])
            if key in self.events:
                return httpx.Response(409, json={"error": {"code": 409}})
            return httpx.Response(200, json=self._store(key, body))
        key = (calendar_id, parts[3])
        if request.method == "PATCH":
            self.patch_requests += 1
            if_match = request.headers.get("If-Match")
            self.patch_if_match.append(if_match)
            stored = self.events.get(key)
            if stored is None:
                return httpx.Response(404, json={"error": {"code": 404}})
            if self.external_edit_on_patch is not None:
                # The user edited the event after our GET and before this PATCH: the edit
                # lands first, the ETag moves and the conditional write must fail.
                stored.update(self.external_edit_on_patch)
                stored["etag"] = self._next_etag()
                self.external_edit_on_patch = None
                return httpx.Response(412, json={"error": {"code": 412}})
            if if_match is not None and if_match != stored.get("etag"):
                return httpx.Response(412, json={"error": {"code": 412}})
            stored.update(json.loads(request.content))
            stored["etag"] = self._next_etag()
            return httpx.Response(200, json=stored)
        self.get_requests += 1
        event = self.events.get(key)
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
    """Real commit/rollback sessions on a savepoint, as the API and jobs use."""

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


async def _insert_task(
    db: AsyncSession, actor: Actor, **overrides: Any
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "user_id": actor.user_id,
        "version": 2,
        "status": "confirmed",
        "calendar_sync_status": "pending",
        "calendar_id": CALENDAR_ID,
        "calendar_event_id": None,
        "titulo": "Entregar informe corregido",
        "detalle": "Informe revisado y firmado",
        "timezone": "America/Bogota",
        "all_day": False,
        "event_start_at": START + timedelta(hours=2),
        "event_end_at": START + timedelta(hours=4),
        "event_start_date": None,
        "event_end_date_exclusive": None,
        "completed_at": None,
        "dismissed_at": None,
        "updated_at": START,
    }
    values.update(overrides)
    await db.execute(TASKS.insert().values(**values))
    await db.flush()
    return values


async def _snapshot(db: AsyncSession, task_id: uuid.UUID) -> TaskSnapshot:
    snapshot = await SqlTaskEventStore(_session_factory(db)).load(task_id)
    assert snapshot is not None
    return snapshot


def _synced_event(task: TaskSnapshot, **changes: Any) -> dict[str, Any]:
    body = {"id": deterministic_event_id(task.task_id), **build_event_body(task)}
    body.update(changes)
    return body


async def _advance_revision(db: AsyncSession, task_id: uuid.UUID) -> None:
    """Leave the row as A3.4's confirm would: the revision activated, version bumped."""
    await db.execute(
        TASKS.update()
        .where(TASKS.c.id == task_id)
        .values(
            version=2,
            titulo="Entregar informe corregido",
            detalle="Informe revisado y firmado",
            event_start_at=START + timedelta(hours=2),
            event_end_at=START + timedelta(hours=4),
        )
    )
    await db.flush()


async def _task_status(db: AsyncSession, task_id: uuid.UUID) -> tuple[str, str, str | None]:
    row = (
        await db.execute(
            select(
                TASKS.c.status,
                TASKS.c.calendar_sync_status,
                TASKS.c.calendar_event_id,
            ).where(TASKS.c.id == task_id)
        )
    ).one()
    return row.status, row.calendar_sync_status, row.calendar_event_id


def _ctx(db: AsyncSession, fake: FakeGoogle) -> dict[str, Any]:
    return {
        SESSIONS_KEY: _session_factory(db),
        GATEWAYS_KEY: _gateway_factory(fake),
        "job_try": 1,
    }


async def test_confirming_a_revision_patches_the_event_with_its_etag(
    db_session: AsyncSession,
) -> None:
    """I-S3-JF-04: the revision updates the linked event under `If-Match`, never by insert."""
    fake = FakeGoogle()
    actor = await make_actor(db_session)
    row = await _insert_task(
        db_session,
        actor,
        version=1,
        titulo="Entregar informe",
        detalle="Informe anterior",
        event_start_at=START,
        event_end_at=START + timedelta(hours=1),
    )
    previous = await _snapshot(db_session, row["id"])
    key = (CALENDAR_ID, deterministic_event_id(row["id"]))
    stored = fake._store(key, _synced_event(previous))
    previous_etag = stored["etag"]
    await _advance_revision(db_session, row["id"])

    await sync_task_event(_ctx(db_session, fake), str(row["id"]), 2)

    assert fake.insert_requests == 1  # the deterministic insert collides with the event
    assert fake.patch_requests == 1
    assert fake.patch_if_match == [previous_etag]
    assert fake.events[key]["summary"] == "Entregar informe corregido"
    assert fake.events[key]["description"] == "Informe revisado y firmado"
    assert await _task_status(db_session, row["id"]) == (
        "confirmed",
        "synced",
        deterministic_event_id(row["id"]),
    )


async def test_an_external_edit_is_not_overwritten_and_needs_review(
    db_session: AsyncSession,
) -> None:
    """I-S3-JF-04: a 412 leaves Google untouched and flags the task for explicit review."""
    fake = FakeGoogle()
    fake.external_edit_on_patch = {"summary": "Editado directamente en Google"}
    actor = await make_actor(db_session)
    row = await _insert_task(
        db_session,
        actor,
        version=1,
        titulo="Entregar informe",
        detalle="Informe anterior",
        event_start_at=START,
        event_end_at=START + timedelta(hours=1),
    )
    previous = await _snapshot(db_session, row["id"])
    key = (CALENDAR_ID, deterministic_event_id(row["id"]))
    fake._store(key, _synced_event(previous))
    await _advance_revision(db_session, row["id"])

    await sync_task_event(_ctx(db_session, fake), str(row["id"]), 2)

    assert fake.patch_requests == 1  # no second write after the 412
    assert fake.events[key]["summary"] == "Editado directamente en Google"
    assert fake.events[key]["description"] == "Informe anterior"
    status, sync_status, _ = await _task_status(db_session, row["id"])
    assert status == "confirmed"  # the task lifecycle is untouched
    assert sync_status == "failed"  # D3.7 surfaces it for review and explicit retry


async def _insert_notification(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    task_id: uuid.UUID | None,
    status: str,
    scheduled_at: datetime,
) -> uuid.UUID:
    notification_id = uuid.uuid4()
    await db.execute(
        NOTIFICATIONS.insert().values(
            id=notification_id,
            user_id=user_id,
            task_id=task_id,
            type="reminder_t24",
            scheduled_at=scheduled_at,
            status=status,
            updated_at=START,
        )
    )
    await db.flush()
    return notification_id


async def _notification_status(db: AsyncSession, notification_id: uuid.UUID) -> str:
    value = (
        await db.execute(
            select(NOTIFICATIONS.c.status).where(NOTIFICATIONS.c.id == notification_id)
        )
    ).scalar_one()
    return str(value)


async def test_completing_cancels_pending_reminders_and_keeps_the_event(
    db_session: AsyncSession,
) -> None:
    """I-S3-JF-04: complete closes the task, cancels unsent reminders and calls no Google."""
    fake = FakeGoogle()
    actor = await make_actor(db_session)
    other = await make_actor(db_session)
    row = await _insert_task(
        db_session,
        actor,
        calendar_sync_status="synced",
    )
    linked_event = deterministic_event_id(row["id"])
    await db_session.execute(
        TASKS.update()
        .where(TASKS.c.id == row["id"])
        .values(calendar_event_id=linked_event)
    )
    await db_session.flush()
    key = (CALENDAR_ID, linked_event)
    fake._store(key, {"summary": "Evento que se conserva", "id": linked_event})
    future = await _insert_notification(
        db_session,
        user_id=actor.user_id,
        task_id=row["id"],
        status="pending",
        scheduled_at=START + timedelta(hours=24),
    )
    overdue = await _insert_notification(
        db_session,
        user_id=actor.user_id,
        task_id=row["id"],
        status="pending",
        scheduled_at=START - timedelta(hours=1),
    )
    sent = await _insert_notification(
        db_session,
        user_id=actor.user_id,
        task_id=row["id"],
        status="sent",
        scheduled_at=START - timedelta(hours=48),
    )
    other_row = await _insert_task(db_session, other)
    foreign = await _insert_notification(
        db_session,
        user_id=other.user_id,
        task_id=other_row["id"],
        status="pending",
        scheduled_at=START + timedelta(hours=24),
    )
    lifecycle = TaskLifecycle(SqlTaskLifecycleStore(_scoped_session_factory(db_session)))

    result = await lifecycle.complete(row["id"], expected_version=row["version"])

    assert result.status == "completed"
    assert result.cancelled_reminders == 2
    assert result.calendar_event_kept is True
    assert result.notice == CALENDAR_EVENT_KEPT_NOTICE
    assert await _notification_status(db_session, future) == "cancelled"
    assert await _notification_status(db_session, overdue) == "cancelled"
    assert await _notification_status(db_session, sent) == "sent"
    assert await _notification_status(db_session, foreign) == "pending"
    stored = (
        await db_session.execute(
            select(TASKS.c.status, TASKS.c.completed_at, TASKS.c.dismissed_at).where(
                TASKS.c.id == row["id"]
            )
        )
    ).one()
    assert stored.status == "completed"
    assert stored.completed_at is not None
    assert stored.dismissed_at is None
    status, sync_status, calendar_event_id = await _task_status(db_session, row["id"])
    assert (status, sync_status, calendar_event_id) == ("completed", "synced", linked_event)
    assert fake.requests == []  # the event is kept: no insert, PATCH or delete
    assert fake.events[key]["summary"] == "Evento que se conserva"


async def test_dismissing_cancels_reminders_and_keeps_the_event(
    db_session: AsyncSession,
) -> None:
    fake = FakeGoogle()
    actor = await make_actor(db_session)
    row = await _insert_task(
        db_session,
        actor,
        status="needs_review",
        calendar_sync_status="not_requested",
        calendar_event_id=None,
    )
    pending = await _insert_notification(
        db_session,
        user_id=actor.user_id,
        task_id=row["id"],
        status="pending",
        scheduled_at=START + timedelta(hours=24),
    )
    lifecycle = TaskLifecycle(SqlTaskLifecycleStore(_scoped_session_factory(db_session)))

    result = await lifecycle.dismiss(row["id"], expected_version=row["version"])

    assert result.status == "dismissed"
    assert result.cancelled_reminders == 1
    assert result.calendar_event_kept is False
    assert result.notice is None
    assert await _notification_status(db_session, pending) == "cancelled"
    stored = (
        await db_session.execute(
            select(TASKS.c.status, TASKS.c.completed_at, TASKS.c.dismissed_at).where(
                TASKS.c.id == row["id"]
            )
        )
    ).one()
    assert stored.status == "dismissed"
    assert stored.dismissed_at is not None
    assert stored.completed_at is None
    assert fake.requests == []


async def test_a_stale_version_changes_nothing(db_session: AsyncSession) -> None:
    """The CAS/row lock: a stale command is a 409 and does not cancel any reminder."""
    actor = await make_actor(db_session)
    row = await _insert_task(db_session, actor, version=3)
    pending = await _insert_notification(
        db_session,
        user_id=actor.user_id,
        task_id=row["id"],
        status="pending",
        scheduled_at=START + timedelta(hours=24),
    )
    lifecycle = TaskLifecycle(SqlTaskLifecycleStore(_scoped_session_factory(db_session)))

    with pytest.raises(ApiError) as failure:
        await lifecycle.complete(row["id"], expected_version=2)

    assert failure.value.status_code == 409
    assert failure.value.code == "version_conflict"
    assert await _notification_status(db_session, pending) == "pending"
    status, sync_status, _ = await _task_status(db_session, row["id"])
    assert (status, sync_status) == ("confirmed", "pending")

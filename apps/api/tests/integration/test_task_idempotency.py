"""I-S3-AN-06: double analysis, concurrent edits and double confirmation stay idempotent.

The suite drives the real analyze worker and the task routes against PostgreSQL. The model
is scripted and the Calendar integration is seeded, so no provider, Google call, audio or
secret is involved. Concurrent cases use one request session per call, not a shared session,
and hold the task row lock while both requests queue, so the row locks and the optimistic
version token are exercised under real contention. Analysis edits a `needs_review` task in
place; only a confirmed task's edit becomes a pending revision (M4), so those cases expect
no revision row.
"""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, date, datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.db import get_session
from app.main import app
from app.models import (
    Audio,
    CalendarSettings,
    OutboxEvent,
    ProcessingRun,
    Segment,
    Task,
    TaskEvidence,
    TaskRevision,
    TaskSource,
    Transcript,
)
from app.services.analysis import ExtractionModel
from app.services.oauth_google import CALENDAR_SCOPES
from app.services.outbox import SYNC_TASK_EVENT
from app.workers.jobs.analyze import run_analyze
from prompts.registry import load_summary_prompts
from tests.integration import actors

pytestmark = pytest.mark.integration

QUOTE = "the midterm exam will be on Monday, September 21"
NOW = datetime(2026, 9, 16, 15, tzinfo=UTC)
DUE_DAY = date(2026, 9, 28)
DUE_END = date(2026, 9, 29)
CALENDAR_ID = "maulwurf@group.calendar.example"
SUMMARY = (
    "One exam was announced for the following week. "
    "Students should review the chapter. No other work was assigned."
)


def _item(title: str, segment_id: uuid.UUID) -> dict[str, object]:
    return {
        "tipo": "examen",
        "titulo": title,
        "detalle": None,
        "due_date": None,
        "due_at": None,
        "timezone": "America/Bogota",
        "all_day": True,
        "date_status": "resolved",
        "confidence_score": 0.5,
        "mencion": {"kind": "all_day", "due_date": DUE_DAY.isoformat()},
        "fuente": {
            "spans": [
                {
                    "segment_id": str(segment_id),
                    "inicio_char": 0,
                    "fin_char": len(QUOTE),
                    "cita_textual": QUOTE,
                }
            ]
        },
    }


def _document(titles: tuple[str, ...], segment_id: uuid.UUID) -> str:
    return json.dumps(
        {
            "items": [_item(title, segment_id) for title in titles],
            "resumen_clase": SUMMARY,
            "temas": ["midterm"],
        }
    )


def _summary_document() -> str:
    return json.dumps({"resumen_clase": SUMMARY, "temas": ["midterm"]})


class _Model:
    """Deterministic extraction model; the summary call returns one fixed document."""

    def __init__(self, segment_id: uuid.UUID, titles: tuple[str, ...]) -> None:
        self._segment_id = segment_id
        self._titles = titles

    async def complete(self, *, system: str, user: str) -> str:
        if system == load_summary_prompts().system:
            return _summary_document()
        return _document(self._titles, self._segment_id)


def _models(model: ExtractionModel) -> Callable[[], ExtractionModel]:
    return lambda: model


def _session_factory(
    db: AsyncSession,
) -> Callable[[], AbstractAsyncContextManager[AsyncSession]]:
    @asynccontextmanager
    async def sessions() -> AsyncIterator[AsyncSession]:
        yield db

    return sessions


async def _class(db: AsyncSession) -> tuple[actors.Actor, Audio, Segment]:
    actor = await actors.make_actor(db)
    audio = await actors.make_audio(db, actor)
    attempt = await actors.make_attempt(db, actor, audio=audio)
    attempt.status = "succeeded"
    attempt.cleanup_status = "verified"
    transcript = Transcript(
        user_id=actor.user_id,
        audio_id=audio.id,
        version=1,
        language_requested="es",
        model="test-model",
        model_config={},
        timestamp_precision="none",
        text=QUOTE,
        char_count=len(QUOTE),
        fts_config="spanish",
    )
    db.add(transcript)
    await db.flush()
    segment = Segment(
        user_id=actor.user_id,
        transcript_id=transcript.id,
        ordinal=0,
        text=QUOTE,
        char_start=0,
        char_end=len(QUOTE),
    )
    db.add(segment)
    audio.active_transcript_version = 1
    await db.flush()
    return actor, audio, segment


async def _tasks_for(db: AsyncSession, user_id: uuid.UUID) -> list[Task]:
    return list(
        (
            await db.scalars(
                select(Task)
                .where(Task.user_id == user_id)
                .order_by(Task.created_at, Task.id)
            )
        ).all()
    )


async def _count(db: AsyncSession, model: Any, **where: Any) -> int:
    stmt = select(func.count()).select_from(model)
    for name, value in where.items():
        stmt = stmt.where(getattr(model, name) == value)
    return int(await db.scalar(stmt) or 0)


async def _run(db: AsyncSession, audio_id: uuid.UUID) -> ProcessingRun:
    return (
        await db.execute(select(ProcessingRun).where(ProcessingRun.audio_id == audio_id))
    ).scalar_one()


async def _force_new_run(db: AsyncSession, audio_id: uuid.UUID) -> None:
    run = await _run(db, audio_id)
    run.status = "failed"
    run.finished_at = None
    await db.flush()


async def _manual_task(db: AsyncSession, actor: actors.Actor) -> Task:
    task = Task(
        user_id=actor.user_id,
        source="manual",
        tipo="recordatorio",
        titulo=f"Tarea {uuid.uuid4().hex[:8]}",
        timezone="America/Bogota",
        all_day=False,
        date_status="missing",
        status="needs_review",
    )
    db.add(task)
    await db.flush()
    return task


async def _confirmable_task(db: AsyncSession, actor: actors.Actor) -> Task:
    task = Task(
        user_id=actor.user_id,
        source="manual",
        tipo="recordatorio",
        titulo=f"Tarea {uuid.uuid4().hex[:8]}",
        timezone="America/Bogota",
        all_day=True,
        date_status="resolved",
        due_date=DUE_DAY,
        event_start_date=DUE_DAY,
        event_end_date_exclusive=DUE_END,
        status="needs_review",
    )
    db.add(task)
    await db.flush()
    return task


async def _connect_calendar(db: AsyncSession, actor: actors.Actor) -> None:
    """Seed a ready integration without a Google call or a real credential."""
    db.add(CalendarSettings(user_id=actor.user_id, calendar_write_calendar_id=CALENDAR_ID))
    credential = await actors.make_google_credential(db, actor)
    credential.status = "connected"
    credential.scopes = list(CALENDAR_SCOPES)
    await db.flush()


@pytest.fixture
async def committed_client(
    migrated_database_url: str,
) -> AsyncIterator[tuple[AsyncClient, async_sessionmaker[AsyncSession]]]:
    """HTTP client whose requests each get their own committed session."""
    engine = create_async_engine(migrated_database_url, poolclass=NullPool)
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
        ) as client:
            yield client, factory
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def _seed_manual_task(
    factory: async_sessionmaker[AsyncSession],
) -> tuple[actors.Actor, uuid.UUID, uuid.UUID]:
    async with factory() as db:
        actor = await actors.make_actor(db)
        task = await _manual_task(db, actor)
        task_id = task.id
        user_id = actor.user_id
        await db.commit()
    return actor, user_id, task_id


async def _seed_confirmable_task(
    factory: async_sessionmaker[AsyncSession],
) -> tuple[actors.Actor, uuid.UUID, uuid.UUID]:
    async with factory() as db:
        actor = await actors.make_actor(db)
        await _connect_calendar(db, actor)
        task = await _confirmable_task(db, actor)
        task_id = task.id
        user_id = actor.user_id
        await db.commit()
    return actor, user_id, task_id


async def _delete_user(
    factory: async_sessionmaker[AsyncSession], user_id: uuid.UUID
) -> None:
    async with factory() as db:
        await db.execute(text("DELETE FROM users WHERE id = :user_id"), {"user_id": user_id})
        await db.commit()


def _authenticate(client: AsyncClient, actor: actors.Actor) -> None:
    """Persist the session cookie once; httpx deprecates per-request cookies."""
    client.cookies.update(actor.cookies)


async def _patch(
    client: AsyncClient,
    actor: actors.Actor,
    task_id: uuid.UUID,
    *,
    version: int,
    title: str,
) -> Response:
    return await client.patch(
        f"/tasks/{task_id}",
        json={"version": version, "titulo": title},
        headers=actor.headers,
    )


async def _confirm(
    client: AsyncClient,
    actor: actors.Actor,
    task_id: uuid.UUID,
    *,
    expected_version: int,
) -> Response:
    return await client.post(
        f"/tasks/{task_id}/confirm",
        json={"expected_version": expected_version},
        headers=actor.headers,
    )


async def _sync_events(
    db: AsyncSession, user_id: uuid.UUID, task_id: uuid.UUID
) -> list[OutboxEvent]:
    return list(
        (
            await db.scalars(
                select(OutboxEvent)
                .where(
                    OutboxEvent.user_id == user_id,
                    OutboxEvent.type == SYNC_TASK_EVENT,
                    OutboxEvent.resource_id == task_id,
                )
                .order_by(OutboxEvent.created_at, OutboxEvent.id)
            )
        ).all()
    )


# --- Analysis -------------------------------------------------------------------------


async def test_duplicate_evidence_in_one_analysis_persists_one_task(
    db_session: AsyncSession,
) -> None:
    actor, audio, segment = await _class(db_session)
    audio_id = audio.id
    model = _Model(segment.id, ("First title", "Second title"))

    await run_analyze(
        _session_factory(db_session), _models(model), audio_id, job_try=1, now=NOW
    )

    tasks = await _tasks_for(db_session, actor.user_id)
    assert len(tasks) == 1
    task = tasks[0]
    assert task.titulo == "First title"
    assert task.status == "needs_review"
    assert await _count(db_session, TaskSource, task_id=task.id) == 1
    assert await _count(db_session, TaskEvidence, task_id=task.id) == 1
    assert await _count(db_session, TaskRevision, task_id=task.id) == 0
    assert (await _run(db_session, audio_id)).status == "succeeded"


async def test_second_delivery_keeps_one_task_and_one_run(
    db_session: AsyncSession,
) -> None:
    actor, audio, segment = await _class(db_session)
    audio_id = audio.id
    model = _Model(segment.id, ("Only title",))

    await run_analyze(
        _session_factory(db_session), _models(model), audio_id, job_try=1, now=NOW
    )
    await run_analyze(
        _session_factory(db_session), _models(model), audio_id, job_try=1, now=NOW
    )

    db_session.expire_all()
    tasks = await _tasks_for(db_session, actor.user_id)
    assert len(tasks) == 1
    task = tasks[0]
    assert task.titulo == "Only title"
    assert await _count(db_session, TaskSource, task_id=task.id) == 1
    assert await _count(db_session, TaskEvidence, task_id=task.id) == 1
    assert await _count(db_session, TaskRevision, task_id=task.id) == 0
    assert await _count(db_session, ProcessingRun, audio_id=audio_id) == 1
    assert (await _run(db_session, audio_id)).status == "succeeded"


async def test_a_second_analysis_with_the_same_evidence_updates_the_same_task(
    db_session: AsyncSession,
) -> None:
    actor, audio, segment = await _class(db_session)
    audio_id = audio.id
    first = _Model(segment.id, ("First title",))

    await run_analyze(
        _session_factory(db_session), _models(first), audio_id, job_try=1, now=NOW
    )
    task = (await _tasks_for(db_session, actor.user_id))[0]
    task_id = task.id
    assert task.titulo == "First title"

    await _force_new_run(db_session, audio_id)
    second = _Model(segment.id, ("Second title",))
    await run_analyze(
        _session_factory(db_session), _models(second), audio_id, job_try=1, now=NOW
    )

    db_session.expire_all()
    tasks = await _tasks_for(db_session, actor.user_id)
    assert len(tasks) == 1
    assert tasks[0].id == task_id
    assert tasks[0].titulo == "Second title"
    assert await _count(db_session, TaskSource, task_id=task_id) == 1
    assert await _count(db_session, TaskEvidence, task_id=task_id) == 1
    assert await _count(db_session, TaskRevision, task_id=task_id) == 0
    assert (await _run(db_session, audio_id)).status == "succeeded"


# --- Concurrent edits and confirmation -------------------------------------------------


@asynccontextmanager
async def _task_row_lock(
    factory: async_sessionmaker[AsyncSession], task_id: uuid.UUID
) -> AsyncIterator[None]:
    """Hold the task row so both HTTP requests queue before the race starts."""
    async with factory() as db:
        await db.execute(select(Task.id).where(Task.id == task_id).with_for_update())
        try:
            yield
        finally:
            await db.rollback()


# `pg_locks` is cluster-wide: count only this test database and only sessions whose
# current statement is a real wait. `\m`/`\M` are PostgreSQL word boundaries, so a query
# that merely mentions "tasks" in a comment cannot satisfy the probe.
_TASK_LOCK_WAITERS = text(
    r"SELECT count(DISTINCT a.pid) FROM pg_locks l "
    r"JOIN pg_stat_activity a ON a.pid = l.pid "
    r"WHERE NOT l.granted "
    r"AND a.datname = current_database() "
    r"AND a.pid <> pg_backend_pid() "
    r"AND a.query ~* '\mFROM\s+tasks\M'"
)


async def _wait_for_task_lock_waiters(
    factory: async_sessionmaker[AsyncSession], expected: int
) -> None:
    """Wait until the expected request transactions are queued on the task row lock."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 10
    while loop.time() < deadline:
        async with factory() as db:
            waiting = await db.scalar(_TASK_LOCK_WAITERS)
        if int(waiting or 0) >= expected:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"expected {expected} requests waiting on the tasks row lock")


async def test_concurrent_patch_returns_409_and_refresh_applies_the_loser(
    committed_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = committed_client
    user_id: uuid.UUID | None = None
    try:
        actor, user_id, task_id = await _seed_manual_task(factory)
        _authenticate(client, actor)
        edits = [("First edit", 1), ("Second edit", 1)]
        requests = [
            asyncio.create_task(
                _patch(client, actor, task_id, version=version, title=title)
            )
            for title, version in edits
        ]
        try:
            async with _task_row_lock(factory, task_id):
                await _wait_for_task_lock_waiters(factory, 2)
            responses = await asyncio.gather(*requests)
        except BaseException:
            for request in requests:
                request.cancel()
            await asyncio.gather(*requests, return_exceptions=True)
            raise

        assert sorted(response.status_code for response in responses) == [200, 409]
        winner = next(
            index for index, response in enumerate(responses) if response.status_code == 200
        )
        loser = 1 - winner
        assert responses[winner].json()["titulo"] == edits[winner][0]
        assert responses[winner].json()["version"] == 2

        conflict = responses[loser].json()["error"]
        assert conflict["code"] == "version_conflict"
        assert conflict["details"] == {
            "current_version": 2,
            "current_status": "needs_review",
        }

        refreshed = await client.get(f"/tasks/{task_id}")
        assert refreshed.status_code == 200
        assert refreshed.json()["version"] == 2
        assert refreshed.json()["titulo"] == edits[winner][0]

        loser_title = edits[loser][0]
        retried = await _patch(
            client, actor, task_id, version=refreshed.json()["version"], title=loser_title
        )
        assert retried.status_code == 200, retried.text
        assert retried.json()["version"] == 3
        assert retried.json()["titulo"] == loser_title
        assert retried.json()["pending_revision"] is None

        async with factory() as db:
            stored = await db.get(Task, task_id)
            assert stored is not None
            assert stored.titulo == loser_title
            assert stored.version == 3
    finally:
        if user_id is not None:
            await _delete_user(factory, user_id)


async def test_concurrent_double_confirmation_creates_one_outbox(
    committed_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = committed_client
    user_id: uuid.UUID | None = None
    try:
        actor, user_id, task_id = await _seed_confirmable_task(factory)
        _authenticate(client, actor)
        requests = [
            asyncio.create_task(
                _confirm(client, actor, task_id, expected_version=1)
            )
            for _ in range(2)
        ]
        try:
            async with _task_row_lock(factory, task_id):
                await _wait_for_task_lock_waiters(factory, 2)
            responses = await asyncio.gather(*requests)
        except BaseException:
            for request in requests:
                request.cancel()
            await asyncio.gather(*requests, return_exceptions=True)
            raise

        assert sorted(response.status_code for response in responses) == [202, 409]
        confirmed = next(
            response for response in responses if response.status_code == 202
        )
        conflict = next(response for response in responses if response.status_code == 409)
        body = confirmed.json()
        assert body["notice"] is None
        assert body["task"]["status"] == "confirmed"
        assert body["task"]["calendar_sync_status"] == "pending"
        assert body["task"]["version"] == 2
        assert conflict.json()["error"]["code"] == "version_conflict"
        assert conflict.json()["error"]["details"] == {
            "current_version": 2,
            "current_status": "confirmed",
        }

        async with factory() as db:
            events = await _sync_events(db, user_id, task_id)
            assert len(events) == 1
            assert events[0].resource_version == 2
            assert events[0].dedupe_key == "confirm:2:1:0"
            assert events[0].status == "pending"
            assert events[0].enabled is True
            stored = await db.get(Task, task_id)
            assert stored is not None
            assert stored.version == 2
            assert stored.status == "confirmed"
            assert stored.calendar_sync_status == "pending"

        refreshed = await client.get(f"/tasks/{task_id}")
        assert refreshed.status_code == 200
        assert refreshed.json()["version"] == 2
        assert refreshed.json()["status"] == "confirmed"

        repeated = await _confirm(client, actor, task_id, expected_version=2)
        assert repeated.status_code == 202, repeated.text
        assert repeated.json()["task"]["version"] == 2
        assert repeated.json()["task"]["status"] == "confirmed"
        assert repeated.json()["task"]["calendar_sync_status"] == "pending"
        assert repeated.json()["notice"] is None

        async with factory() as db:
            assert len(await _sync_events(db, user_id, task_id)) == 1
    finally:
        if user_id is not None:
            await _delete_user(factory, user_id)

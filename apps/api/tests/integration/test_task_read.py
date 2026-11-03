"""A3.3 read slice: tenant-scoped task inbox and detail with reconstructed evidence."""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session
from app.main import app
from app.models import Segment, Task, TaskRevision, TaskSource
from tests.integration import actors

pytestmark = pytest.mark.integration


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    async def override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = override
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=settings.allowed_origin
        ) as http:
            yield http
    finally:
        app.dependency_overrides.clear()


def _task(actor: actors.Actor, *, audio_id: uuid.UUID, subject_id: uuid.UUID, title: str) -> Task:
    return Task(
        user_id=actor.user_id,
        audio_id=audio_id,
        subject_id=subject_id,
        source="extraction",
        tipo="tarea",
        titulo=title,
        timezone="America/Bogota",
        all_day=True,
        date_status="missing",
        status="needs_review",
    )


async def test_task_inbox_returns_the_quote_and_keeps_statuses_separate(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    audio, _attempt, transcript = await actors.make_class_with_transcript(db_session, actor)
    text = "texto de prueba"
    segment = Segment(
        user_id=actor.user_id,
        transcript_id=transcript.id,
        ordinal=1,
        text=text,
        char_start=0,
        char_end=len(text),
    )
    db_session.add(segment)
    await db_session.flush()
    task = _task(actor, audio_id=audio.id, subject_id=audio.subject_id, title="Tarea")
    task.date_status = "resolved"
    task.due_date = date(2026, 11, 9)
    db_session.add(task)
    await db_session.flush()
    db_session.add(
        TaskSource(
            user_id=actor.user_id,
            task_id=task.id,
            audio_id=audio.id,
            transcript_id=transcript.id,
            transcript_version=transcript.version,
            segment_id=segment.id,
            char_start=0,
            char_end=7,
        )
    )
    db_session.add(
        TaskRevision(
            user_id=actor.user_id,
            task_id=task.id,
            base_task_version=1,
            revision_type="edit_of_confirmed",
            proposed_fields={"titulo": "Otra"},
        )
    )
    await db_session.flush()

    response = await client.get("/tasks", cookies=actor.cookies)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["id"] == str(task.id)
    assert item["status"] == "needs_review"
    assert item["calendar_sync_status"] == "not_requested"
    assert item["date_status"] == "resolved"
    assert item["due_date"] == "2026-11-09"
    assert item["evidence"] == [
        {
            "segment_id": str(segment.id),
            "char_start": 0,
            "char_end": 7,
            "quote": "texto d",
            "t_start": None,
            "t_end": None,
        }
    ]
    assert item["sources"][0]["audio_id"] == str(audio.id)
    assert item["pending_revision"]["base_task_version"] == 1


async def test_task_listing_is_tenant_scoped_and_keyset_paginated(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    alice, bob = await actors.make_actor(db_session), await actors.make_actor(db_session)
    alice_audio, _, _ = await actors.make_class_with_transcript(db_session, alice)
    bob_audio, _, _ = await actors.make_class_with_transcript(db_session, bob)
    base = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
    for index in range(3):
        task = _task(
            alice,
            audio_id=alice_audio.id,
            subject_id=alice_audio.subject_id,
            title=f"Tarea {index}",
        )
        task.created_at = base.replace(minute=index)
        db_session.add(task)
    foreign = _task(
        bob,
        audio_id=bob_audio.id,
        subject_id=bob_audio.subject_id,
        title="Ajena",
    )
    db_session.add(foreign)
    await db_session.flush()

    first = await client.get("/tasks", params={"limit": 2}, cookies=alice.cookies)
    assert first.status_code == 200
    first_body = first.json()
    assert len(first_body["items"]) == 2
    assert first_body["next_cursor"] is not None
    assert all(item["id"] != str(foreign.id) for item in first_body["items"])

    second = await client.get(
        "/tasks",
        params={"limit": 2, "cursor": first_body["next_cursor"]},
        cookies=alice.cookies,
    )
    assert second.status_code == 200
    second_body = second.json()
    assert len(second_body["items"]) == 1
    assert second_body["next_cursor"] is None
    assert {item["id"] for item in first_body["items"] + second_body["items"]} == {
        str(task.id) for task in await _alice_tasks(db_session, alice)
    }

    foreign_detail = await client.get(f"/tasks/{foreign.id}", cookies=alice.cookies)
    assert foreign_detail.status_code == 404


async def _alice_tasks(db: AsyncSession, actor: actors.Actor) -> list[Task]:
    from sqlalchemy import select

    return list((await db.scalars(select(Task).where(Task.user_id == actor.user_id))).all())


async def test_invalid_status_and_cursor_are_typed_422(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)

    bad_status = await client.get("/tasks", params={"status": "scheduled"}, cookies=actor.cookies)
    bad_cursor = await client.get("/tasks", params={"cursor": "***"}, cookies=actor.cookies)

    assert bad_status.status_code == 422
    assert bad_status.json()["error"]["code"] == "validation_failed"
    assert bad_cursor.status_code == 422
    assert bad_cursor.json()["error"]["details"]["fields"]["cursor"]

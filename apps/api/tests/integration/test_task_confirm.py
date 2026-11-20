"""A3.4: atomic confirmation, revision promotion and Calendar outbox."""

import uuid
from collections.abc import AsyncIterator
from datetime import date

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.config import settings
from app.core.db import get_session
from app.main import app
from app.models import (
    CalendarSettings,
    GoogleCredential,
    OutboxEvent,
    Segment,
    Task,
    TaskRevision,
    TaskSource,
)
from app.services.oauth_google import CALENDAR_SCOPES
from tests.integration import actors

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skip(
        reason="Superseded by develop's test_task_endpoints.py; kept as historical A3.4 evidence."
    ),
]


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


def _task(actor: actors.Actor, **overrides: object) -> Task:
    values: dict[str, object] = {
        "user_id": actor.user_id,
        "source": "manual",
        "tipo": "tarea",
        "titulo": f"Tarea {uuid.uuid4().hex[:6]}",
        "timezone": "America/Bogota",
        "all_day": True,
        "date_status": "resolved",
        "due_date": date(2026, 11, 9),
        "event_start_date": date(2026, 11, 10),
        "event_end_date_exclusive": date(2026, 11, 11),
        "status": "needs_review",
    }
    values.update(overrides)
    return Task(**values)


async def _confirm(
    client: AsyncClient,
    actor: actors.Actor,
    task_id: uuid.UUID,
    *,
    expected_version: int,
    revision_id: uuid.UUID | None = None,
) -> Response:
    return await client.post(
        f"/tasks/{task_id}/confirm",
        json={
            "expected_version": expected_version,
            "revision_id": None if revision_id is None else str(revision_id),
        },
        cookies=actor.cookies,
        headers=actor.headers,
    )


async def _outbox(db_session: AsyncSession, task_id: uuid.UUID) -> list[OutboxEvent]:
    return list(
        (
            await db_session.scalars(
                select(OutboxEvent).where(
                    OutboxEvent.type == "sync_task_event",
                    OutboxEvent.resource_id == task_id,
                )
            )
        ).all()
    )


async def _calendar_ready(db_session: AsyncSession, actor: actors.Actor) -> None:
    blob, version = crypto.encrypt(
        "ya29.test",
        user_id=actor.user_id,
        field="google_credentials.access_token",
    )
    db_session.add(
        CalendarSettings(
            user_id=actor.user_id,
            calendar_write_calendar_id="mw@group.calendar.example",
        )
    )
    db_session.add(
        GoogleCredential(
            user_id=actor.user_id,
            access_token_enc=blob,
            key_version=version,
            scopes=list(CALENDAR_SCOPES),
            status="connected",
        )
    )
    await db_session.flush()


async def test_confirm_without_google_is_confirmed_and_blocked(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    task = _task(actor)
    db_session.add(task)
    await db_session.flush()
    task_id = task.id
    version = task.version

    response = await _confirm(
        client, actor, task_id, expected_version=version
    )

    assert response.status_code == 202
    body = response.json()
    assert body["task"]["status"] == "confirmed"
    assert body["task"]["calendar_sync_status"] == "blocked"
    assert body["task"]["version"] == version + 1
    assert body["revision_id"] is None
    assert await _outbox(db_session, task_id) == []


async def test_confirm_with_google_creates_one_outbox_and_target_calendar(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    await _calendar_ready(db_session, actor)
    task = _task(actor)
    db_session.add(task)
    await db_session.flush()
    task_id = task.id
    version = task.version

    first = await _confirm(client, actor, task_id, expected_version=version)
    assert first.status_code == 202
    body = first.json()
    assert body["task"]["calendar_sync_status"] == "pending"
    assert body["task"]["version"] == version + 1

    await db_session.commit()
    events = await _outbox(db_session, task_id)
    assert len(events) == 1
    assert events[0].resource_version == version + 1
    assert events[0].dedupe_key == f"confirm:{body['task']['due_version']}"
    assert events[0].enabled is True

    # Repeating with the old version loses the CAS and never creates a second outbox.
    stale = await _confirm(client, actor, task_id, expected_version=version)
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "version_conflict"
    assert len(await _outbox(db_session, task_id)) == 1

    # Repeating with the current version is an accepted no-op.
    current = await _confirm(
        client, actor, task_id, expected_version=version + 1
    )
    assert current.status_code == 202
    assert current.json()["task"]["status"] == "confirmed"
    assert len(await _outbox(db_session, task_id)) == 1


async def test_confirm_promotes_exactly_one_revision(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    task = _task(actor, status="confirmed")
    db_session.add(task)
    await db_session.flush()
    revision = TaskRevision(
        user_id=actor.user_id,
        task_id=task.id,
        base_task_version=task.version,
        revision_type="edit_of_confirmed",
        proposed_fields={
            "titulo": "TÃ­tulo revisado",
            "due_date": "2026-11-12",
            "date_status": "resolved",
            "all_day": True,
        },
    )
    db_session.add(revision)
    await db_session.flush()
    task_id = task.id
    revision_id = revision.id
    version = task.version
    old_due_version = task.due_version

    response = await _confirm(
        client,
        actor,
        task_id,
        expected_version=version,
        revision_id=revision_id,
    )

    assert response.status_code == 202
    body = response.json()
    assert body["revision_id"] == str(revision_id)
    assert body["task"]["titulo"] == "TÃ­tulo revisado"
    assert body["task"]["due_date"] == "2026-11-12"
    assert body["task"]["version"] == version + 1
    assert body["task"]["due_version"] == old_due_version + 1
    assert body["task"]["pending_revision"] is None
    stored = await db_session.get(TaskRevision, revision_id)
    assert stored is not None
    assert stored.status == "activated"
    assert stored.activated_at is not None


async def test_confirm_requires_resolved_date_and_evidence(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    missing = _task(
        actor,
        date_status="missing",
        due_date=None,
        event_start_date=None,
        event_end_date_exclusive=None,
    )
    db_session.add(missing)
    await db_session.flush()

    unresolved = await _confirm(
        client, actor, missing.id, expected_version=missing.version
    )
    assert unresolved.status_code == 422
    assert unresolved.json()["error"]["code"] == "date_unresolved"

    audio, _attempt, transcript = await actors.make_class_with_transcript(
        db_session, actor
    )
    segment = Segment(
        user_id=actor.user_id,
        transcript_id=transcript.id,
        ordinal=1,
        text=transcript.text,
        char_start=0,
        char_end=transcript.char_count,
    )
    db_session.add(segment)
    extraction = _task(
        actor,
        source="extraction",
        audio_id=audio.id,
        subject_id=audio.subject_id,
    )
    db_session.add(extraction)
    await db_session.flush()

    no_evidence = await _confirm(
        client, actor, extraction.id, expected_version=extraction.version
    )
    assert no_evidence.status_code == 422
    assert no_evidence.json()["error"]["code"] == "evidence_invalid"

    db_session.add(
        TaskSource(
            user_id=actor.user_id,
            task_id=extraction.id,
            audio_id=audio.id,
            transcript_id=transcript.id,
            transcript_version=transcript.version,
            segment_id=segment.id,
            char_start=0,
            char_end=7,
        )
    )
    await db_session.flush()

    confirmed = await _confirm(
        client, actor, extraction.id, expected_version=extraction.version
    )
    assert confirmed.status_code == 202
    assert confirmed.json()["task"]["status"] == "confirmed"


async def test_confirm_rejects_a_stale_revision(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    task = _task(actor, status="confirmed")
    db_session.add(task)
    await db_session.flush()
    revision = TaskRevision(
        user_id=actor.user_id,
        task_id=task.id,
        base_task_version=task.version + 1,
        revision_type="edit_of_confirmed",
        proposed_fields={"titulo": "Vieja"},
    )
    db_session.add(revision)
    await db_session.flush()
    task_id = task.id
    version = task.version
    revision_id = revision.id

    response = await _confirm(
        client,
        actor,
        task_id,
        expected_version=version,
        revision_id=revision_id,
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "version_conflict"


"""A3.3/A3.4 against PostgreSQL: task reads, proposals, edits, confirmation and lifecycle.

The suite exercises the real routes and tables, including A3.2's persisted evidence. No
provider, Google call, audio or secret is involved: the Calendar integration is seeded
directly, and I-S3-AN-03 proves that the task and its single `sync_task_event` row are
written in the same transaction (or that a blocked confirmation writes no event).
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session
from app.main import app
from app.models import (
    Audio,
    CalendarSettings,
    Notification,
    OutboxEvent,
    Segment,
    Task,
    TaskEvidence,
    TaskRevision,
    TaskSource,
    Transcript,
)
from app.services import tasks as task_service
from app.services.notifications import dedupe_key
from app.services.oauth_google import CALENDAR_SCOPES
from app.services.outbox import SYNC_TASK_EVENT, decide
from tests.integration import actors

pytestmark = pytest.mark.integration

FIRST = "the midterm exam will be on Monday, September 21"
SECOND = "bring the lab report on Friday"
TRANSCRIPT = f"{FIRST} {SECOND}"
DUE_DAY = date(2026, 11, 9)
DUE_AT = datetime(2026, 11, 9, 15, 0, tzinfo=UTC)
CALENDAR_ID = "maulwurf@group.calendar.example"


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


def _error(response: Any) -> str:
    return str(response.json()["error"]["code"])


async def _class(
    db: AsyncSession, actor: actors.Actor
) -> tuple[Audio, Transcript, Segment]:
    audio = await actors.make_audio(db, actor)
    transcript = Transcript(
        user_id=actor.user_id,
        audio_id=audio.id,
        version=1,
        language_requested="es",
        model="test-model",
        model_config={},
        timestamp_precision="none",
        text=TRANSCRIPT,
        char_count=len(TRANSCRIPT),
        fts_config="spanish",
    )
    db.add(transcript)
    await db.flush()
    segment = Segment(
        user_id=actor.user_id,
        transcript_id=transcript.id,
        ordinal=1,
        text=TRANSCRIPT,
        char_start=0,
        char_end=len(TRANSCRIPT),
    )
    db.add(segment)
    await db.flush()
    return audio, transcript, segment


async def _task(db: AsyncSession, actor: actors.Actor, **overrides: Any) -> Task:
    values: dict[str, Any] = {
        "user_id": actor.user_id,
        "source": "manual",
        "tipo": "recordatorio",
        "titulo": f"Tarea {uuid.uuid4().hex[:8]}",
        "timezone": "America/Bogota",
        "all_day": False,
        "date_status": "missing",
        "status": "needs_review",
    }
    values.update(overrides)
    task = Task(**values)
    db.add(task)
    await db.flush()
    return task


def _extraction_body(segment: Segment, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "source": "extraction",
        "tipo": "examen",
        "titulo": "Midterm exam",
        "timezone": "America/Bogota",
        "due_date": "2026-09-21",
        "sources": [
            {
                "segment_id": str(segment.id),
                "char_start": 0,
                "char_end": len(FIRST),
                "quote": FIRST,
            }
        ],
    }
    body.update(overrides)
    return body


async def _create_extraction(
    client: AsyncClient, actor: actors.Actor, segment: Segment, **overrides: Any
) -> dict[str, Any]:
    response = await client.post(
        "/tasks",
        json=_extraction_body(segment, **overrides),
        cookies=actor.cookies,
        headers=actor.headers,
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def _confirmable_task(
    db: AsyncSession, actor: actors.Actor, **overrides: Any
) -> Task:
    values: dict[str, Any] = {
        "date_status": "resolved",
        "all_day": True,
        "due_date": DUE_DAY,
        "event_start_date": DUE_DAY,
        "event_end_date_exclusive": DUE_DAY + timedelta(days=1),
    }
    values.update(overrides)
    return await _task(db, actor, **values)


async def _connect_calendar(
    db: AsyncSession,
    actor: actors.Actor,
    *,
    status: str = "connected",
    scopes: list[str] | None = None,
    target: str | None = CALENDAR_ID,
) -> None:
    """Seed the stored grant and target without any Google call or real credential."""
    # A previous request committed through the shared session and expired the actor's
    # attributes; refresh inside the async context before the helper reads `actor.user.id`.
    await db.refresh(actor.user)
    if target is not None:
        db.add(
            CalendarSettings(user_id=actor.user_id, calendar_write_calendar_id=target)
        )
    credential = await actors.make_google_credential(db, actor)
    credential.status = status
    credential.scopes = list(CALENDAR_SCOPES) if scopes is None else scopes
    await db.flush()


async def _sync_events(
    db: AsyncSession, actor: actors.Actor, task_id: uuid.UUID
) -> list[OutboxEvent]:
    return list(
        (
            await db.scalars(
                select(OutboxEvent)
                .where(
                    OutboxEvent.user_id == actor.user_id,
                    OutboxEvent.type == SYNC_TASK_EVENT,
                    OutboxEvent.resource_id == task_id,
                )
                .order_by(OutboxEvent.created_at, OutboxEvent.id)
            )
        ).all()
    )


async def _confirm(
    client: AsyncClient,
    actor: actors.Actor,
    task_id: uuid.UUID | str,
    *,
    expected_version: int,
    revision_id: uuid.UUID | str | None = None,
) -> Any:
    body: dict[str, Any] = {"expected_version": expected_version}
    if revision_id is not None:
        body["revision_id"] = str(revision_id)
    return await client.post(
        f"/tasks/{task_id}/confirm",
        json=body,
        cookies=actor.cookies,
        headers=actor.headers,
    )


async def _notification(
    db: AsyncSession, actor: actors.Actor, task: Task, *, status: str = "pending"
) -> Notification:
    occurrence = date(2026, 11, 9)
    notification = Notification(
        user_id=actor.user_id,
        task_id=task.id,
        type="reminder_t24",
        occurrence=occurrence,
        due_version=task.due_version,
        scheduled_at=datetime(2026, 11, 8, 10, 0, tzinfo=UTC),
        dedupe_key=dedupe_key(
            user_id=actor.user_id,
            type="reminder_t24",
            task_id=task.id,
            due_version=task.due_version,
            occurrence=occurrence,
        ),
        status=status,
    )
    db.add(notification)
    await db.flush()
    return notification


# --- Reads -----------------------------------------------------------------------------


async def test_list_is_tenant_scoped_and_filters_by_status_and_subject(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    other = await actors.make_actor(db_session)
    subject = await actors.make_subject(db_session, actor)
    subject_id = subject.id
    mine = await _task(db_session, actor, subject_id=subject_id, titulo="Mía")
    confirmed = await _task(db_session, actor, status="confirmed", titulo="Confirmada")
    mine_id = mine.id
    confirmed_id = confirmed.id
    await _task(db_session, other, titulo="Ajena")

    response = await client.get("/tasks", cookies=actor.cookies)
    assert response.status_code == 200
    data = response.json()
    assert {item["id"] for item in data["items"]} == {str(mine_id), str(confirmed_id)}

    filtered = await client.get(
        "/tasks",
        params={"status": "confirmed", "subject_id": str(subject_id)},
        cookies=actor.cookies,
    )
    assert filtered.status_code == 200
    assert filtered.json()["items"] == []


async def test_list_rejects_an_unknown_status_and_an_inverted_range(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)

    unknown = await client.get(
        "/tasks", params={"status": "scheduled"}, cookies=actor.cookies
    )
    assert unknown.status_code == 422
    assert _error(unknown) == "validation_failed"

    inverted = await client.get(
        "/tasks",
        params={"due_from": "2026-11-10", "due_to": "2026-11-09"},
        cookies=actor.cookies,
    )
    assert inverted.status_code == 422
    assert _error(inverted) == "validation_failed"


async def test_due_filters_use_the_task_local_day(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    # 02:00 UTC on Nov 10 is still Nov 9 in Bogotá (UTC-5).
    local_ninth = await _task(
        db_session,
        actor,
        timezone="America/Bogota",
        date_status="resolved",
        all_day=False,
        due_at=datetime(2026, 11, 10, 2, 0, tzinfo=UTC),
    )

    same_day = await client.get(
        "/tasks",
        params={"due_from": "2026-11-09", "due_to": "2026-11-09"},
        cookies=actor.cookies,
    )
    assert same_day.status_code == 200
    assert [item["id"] for item in same_day.json()["items"]] == [str(local_ninth.id)]

    next_day = await client.get(
        "/tasks",
        params={"due_from": "2026-11-10", "due_to": "2026-11-10"},
        cookies=actor.cookies,
    )
    assert next_day.status_code == 200
    assert next_day.json()["items"] == []


async def test_list_uses_a_stable_keyset_cursor(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    base = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    first = await _task(db_session, actor, titulo="A", created_at=base)
    second = await _task(
        db_session, actor, titulo="B", created_at=base + timedelta(minutes=1)
    )
    third = await _task(
        db_session, actor, titulo="C", created_at=base + timedelta(minutes=2)
    )

    page = await client.get("/tasks", params={"limit": 2}, cookies=actor.cookies)
    assert page.status_code == 200
    body = page.json()
    assert [item["id"] for item in body["items"]] == [str(third.id), str(second.id)]
    assert body["next_cursor"]

    rest = await client.get(
        "/tasks",
        params={"limit": 2, "cursor": body["next_cursor"]},
        cookies=actor.cookies,
    )
    assert rest.status_code == 200
    assert [item["id"] for item in rest.json()["items"]] == [str(first.id)]
    assert rest.json()["next_cursor"] is None

    bad = await client.get("/tasks", params={"cursor": "garbage"}, cookies=actor.cookies)
    assert bad.status_code == 422
    assert _error(bad) == "validation_failed"


async def test_detail_returns_the_persisted_quote_and_pending_revision(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    _, _, segment = await _class(db_session, actor)
    task = await _create_extraction(client, actor, segment)
    revision = TaskRevision(
        user_id=actor.user_id,
        task_id=uuid.UUID(task["id"]),
        base_task_version=1,
        revision_type="edit_of_confirmed",
        proposed_fields={"titulo": "Revisada"},
    )
    db_session.add(revision)
    await db_session.flush()

    response = await client.get(f"/tasks/{task['id']}", cookies=actor.cookies)
    assert response.status_code == 200
    data = response.json()
    assert data["evidence"][0]["quote"] == FIRST
    assert data["evidence"][0]["segment_id"] == str(segment.id)
    assert data["sources"][0]["segment_id"] == str(segment.id)
    assert data["pending_revision"]["proposed_fields"] == {"titulo": "Revisada"}

    foreign = await actors.make_actor(db_session)
    hidden = await client.get(f"/tasks/{task['id']}", cookies=foreign.cookies)
    assert hidden.status_code == 404
    assert _error(hidden) == "not_found"


# --- Creation and provenance -----------------------------------------------------------


async def test_manual_and_general_chat_tasks_have_no_sources(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    subject = await actors.make_subject(db_session, actor)
    subject_id = subject.id
    subject_name = subject.name

    manual = await client.post(
        "/tasks",
        json={
            "source": "manual",
            "tipo": "recordatorio",
            "titulo": "Repasar",
            "timezone": "America/Bogota",
            "subject_id": str(subject_id),
        },
        cookies=actor.cookies,
        headers=actor.headers,
    )
    assert manual.status_code == 201
    manual_data = manual.json()
    assert manual_data["source"] == "manual"
    assert manual_data["audio_id"] is None
    assert manual_data["sources"] == []
    assert manual_data["subject"] == {"id": str(subject_id), "name": subject_name}

    chat = await client.post(
        "/tasks",
        json={
            "source": "chat",
            "tipo": "recomendacion",
            "titulo": "Lee el capítulo 4",
            "timezone": "America/Bogota",
        },
        cookies=actor.cookies,
        headers=actor.headers,
    )
    assert chat.status_code == 201
    assert chat.json()["sources"] == []
    assert chat.json()["audio_id"] is None


async def test_an_extraction_derives_the_class_and_persists_evidence(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    audio, transcript, segment = await _class(db_session, actor)
    audio_id = audio.id
    transcript_version = transcript.version
    segment_id = segment.id

    data = await _create_extraction(client, actor, segment)

    assert data["audio_id"] == str(audio_id)
    assert data["source"] == "extraction"
    assert data["date_status"] == "resolved"
    assert data["date_text"] == "Monday, September 21"
    assert data["sources"][0]["segment_id"] == str(segment_id)
    assert data["sources"][0]["transcript_version"] == transcript_version
    assert data["evidence"][0]["quote"] == FIRST
    assert data["evidence"][0]["t_start"] is None

    evidence = (
        await db_session.scalars(
            select(TaskEvidence).where(TaskEvidence.task_id == uuid.UUID(data["id"]))
        )
    ).all()
    assert len(evidence) == 1


async def test_a_forged_quote_is_evidence_invalid_and_writes_nothing(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    _, _, segment = await _class(db_session, actor)
    body = _extraction_body(segment)
    body["sources"][0]["quote"] = "a quote that is not in the segment"
    body["sources"][0]["char_end"] = len(body["sources"][0]["quote"])

    response = await client.post(
        "/tasks", json=body, cookies=actor.cookies, headers=actor.headers
    )
    assert response.status_code == 422
    assert _error(response) == "evidence_invalid"
    assert (
        await db_session.scalar(
            select(Task).where(Task.user_id == actor.user_id, Task.source == "extraction")
        )
    ) is None


async def test_sources_from_two_classes_and_conflicting_ids_are_rejected(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    audio, _, segment = await _class(db_session, actor)
    _, _, other_segment = await _class(db_session, actor)

    mixed = _extraction_body(segment)
    mixed["sources"].append(
        {
            "segment_id": str(other_segment.id),
            "char_start": 0,
            "char_end": len(FIRST),
            "quote": FIRST,
        }
    )
    response = await client.post(
        "/tasks", json=mixed, cookies=actor.cookies, headers=actor.headers
    )
    assert response.status_code == 422
    assert _error(response) == "evidence_invalid"

    conflict = _extraction_body(segment, audio_id=str(uuid.uuid4()))
    response = await client.post(
        "/tasks", json=conflict, cookies=actor.cookies, headers=actor.headers
    )
    assert response.status_code == 422
    assert _error(response) == "validation_failed"

    subject = await actors.make_subject(db_session, actor)
    conflict = _extraction_body(segment, subject_id=str(subject.id))
    response = await client.post(
        "/tasks", json=conflict, cookies=actor.cookies, headers=actor.headers
    )
    assert response.status_code == 422
    assert _error(response) == "validation_failed"


async def test_mutations_require_csrf(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    response = await client.post(
        "/tasks",
        json={
            "source": "manual",
            "tipo": "recordatorio",
            "titulo": "Sin CSRF",
            "timezone": "America/Bogota",
        },
        cookies=actor.cookies,
        headers={"Origin": settings.allowed_origin},
    )
    assert response.status_code == 403
    assert _error(response) == "csrf_invalid"


# --- PATCH and revisions ---------------------------------------------------------------


async def test_patch_with_a_stale_version_is_a_409(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    task = await _task(db_session, actor)

    response = await client.patch(
        f"/tasks/{task.id}",
        json={"version": 99, "titulo": "No debe aplicarse"},
        cookies=actor.cookies,
        headers=actor.headers,
    )
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "version_conflict"
    assert error["details"]["current_version"] == 1
    assert error["details"]["current_status"] == "needs_review"


async def test_patch_in_place_bumps_version_and_copies_evidence(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    _, _, segment = await _class(db_session, actor)
    created = await _create_extraction(client, actor, segment)

    response = await client.patch(
        f"/tasks/{created['id']}",
        json={"version": 1, "titulo": "Midterm corregido"},
        cookies=actor.cookies,
        headers=actor.headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["titulo"] == "Midterm corregido"
    assert data["version"] == 2
    assert data["pending_revision"] is None
    assert data["evidence"][0]["quote"] == FIRST

    copied = (
        await db_session.scalars(
            select(TaskEvidence).where(
                TaskEvidence.task_id == uuid.UUID(created["id"]),
                TaskEvidence.task_version == 2,
            )
        )
    ).all()
    assert len(copied) == 1


async def test_patch_cannot_change_the_subject_of_a_task_with_evidence(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    _, _, segment = await _class(db_session, actor)
    # The handler commits and expires ORM attributes; create the subject and copy its id.
    other_subject = await actors.make_subject(db_session, actor)
    other_subject_id = other_subject.id
    created = await _create_extraction(client, actor, segment)

    response = await client.patch(
        f"/tasks/{created['id']}",
        json={"version": 1, "subject_id": str(other_subject_id)},
        cookies=actor.cookies,
        headers=actor.headers,
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_failed"
    assert error["details"]["fields"]["subject_id"] == (
        "La materia de una tarea con evidencia no puede modificarse de forma independiente"
    )


async def test_patch_can_change_the_subject_of_a_manual_task(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    first_subject = await actors.make_subject(db_session, actor)
    second_subject = await actors.make_subject(db_session, actor)
    task = await _task(db_session, actor, subject_id=first_subject.id)
    second_subject_id = second_subject.id

    response = await client.patch(
        f"/tasks/{task.id}",
        json={"version": 1, "subject_id": str(second_subject_id)},
        cookies=actor.cookies,
        headers=actor.headers,
    )
    assert response.status_code == 200
    assert response.json()["subject"]["id"] == str(second_subject_id)


async def test_a_noop_patch_does_not_bump_or_create_a_revision(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    task = await _task(db_session, actor)
    task_id = task.id

    response = await client.patch(
        f"/tasks/{task_id}",
        json={"version": 1},
        cookies=actor.cookies,
        headers=actor.headers,
    )
    assert response.status_code == 200
    assert response.json()["version"] == 1
    assert response.json()["pending_revision"] is None
    assert (
        await db_session.scalar(
            select(TaskRevision).where(TaskRevision.task_id == task_id)
        )
    ) is None


async def test_editing_a_confirmed_task_creates_a_revision_without_touching_it(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    _, _, segment = await _class(db_session, actor)
    created = await _create_extraction(client, actor, segment)
    patched = await client.patch(
        f"/tasks/{created['id']}",
        json={
            "version": 1,
            "event_block": {
                "start_date": "2026-09-21",
                "end_date_exclusive": "2026-09-22",
            },
        },
        cookies=actor.cookies,
        headers=actor.headers,
    )
    assert patched.status_code == 200
    confirmed = await client.post(
        f"/tasks/{created['id']}/confirm",
        json={"expected_version": patched.json()["version"]},
        cookies=actor.cookies,
        headers=actor.headers,
    )
    assert confirmed.status_code == 202
    active = confirmed.json()["task"]

    revision = await client.patch(
        f"/tasks/{created['id']}",
        json={"version": active["version"], "titulo": "Título propuesto"},
        cookies=actor.cookies,
        headers=actor.headers,
    )
    assert revision.status_code == 200
    data = revision.json()
    assert data["titulo"] == "Midterm exam"
    assert data["version"] == active["version"]
    assert data["pending_revision"]["base_task_version"] == active["version"]
    assert data["pending_revision"]["proposed_fields"]["titulo"] == "Título propuesto"

    promoted = await client.post(
        f"/tasks/{created['id']}/confirm",
        json={
            "expected_version": active["version"],
            "revision_id": data["pending_revision"]["id"],
        },
        cookies=actor.cookies,
        headers=actor.headers,
    )
    assert promoted.status_code == 202
    assert promoted.json()["task"]["titulo"] == "Título propuesto"
    assert promoted.json()["task"]["version"] == active["version"] + 1
    stored = await db_session.get(
        TaskRevision, uuid.UUID(data["pending_revision"]["id"])
    )
    assert stored is not None
    assert stored.status == "activated"
    assert stored.activated_at is not None


async def test_two_patches_keep_only_the_latest_pending_revision(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    task = await _task(
        db_session,
        actor,
        status="confirmed",
        date_status="resolved",
        all_day=True,
        due_date=DUE_DAY,
        event_start_date=DUE_DAY,
        event_end_date_exclusive=DUE_DAY + timedelta(days=1),
    )
    task_id = task.id

    first = await client.patch(
        f"/tasks/{task_id}",
        json={"version": 1, "titulo": "Primera propuesta"},
        cookies=actor.cookies,
        headers=actor.headers,
    )
    assert first.status_code == 200
    first_revision_id = first.json()["pending_revision"]["id"]

    second = await client.patch(
        f"/tasks/{task_id}",
        json={"version": 1, "titulo": "Segunda propuesta"},
        cookies=actor.cookies,
        headers=actor.headers,
    )
    assert second.status_code == 200
    second_revision_id = second.json()["pending_revision"]["id"]
    assert second_revision_id != first_revision_id

    db_session.expire_all()
    first_stored = await db_session.get(TaskRevision, uuid.UUID(first_revision_id))
    second_stored = await db_session.get(TaskRevision, uuid.UUID(second_revision_id))
    assert first_stored is not None
    assert first_stored.status == "discarded"
    assert second_stored is not None
    assert second_stored.status == "pending_confirmation"

    promoted = await client.post(
        f"/tasks/{task_id}/confirm",
        json={"expected_version": 1, "revision_id": second_revision_id},
        cookies=actor.cookies,
        headers=actor.headers,
    )
    assert promoted.status_code == 202
    assert promoted.json()["task"]["version"] == 2
    assert promoted.json()["task"]["titulo"] == "Segunda propuesta"

    detail = await client.get(f"/tasks/{task_id}", cookies=actor.cookies)
    assert detail.status_code == 200
    body = detail.json()
    assert body["version"] == 2
    assert body["pending_revision"] is None

    db_session.expire_all()
    first_stored = await db_session.get(TaskRevision, uuid.UUID(first_revision_id))
    second_stored = await db_session.get(TaskRevision, uuid.UUID(second_revision_id))
    assert first_stored is not None
    assert first_stored.status == "discarded"
    assert second_stored is not None
    assert second_stored.status == "activated"
    pending = (
        await db_session.scalars(
            select(TaskRevision).where(
                TaskRevision.task_id == task_id,
                TaskRevision.status == "pending_confirmation",
            )
        )
    ).all()
    assert pending == []


async def test_a_stale_revision_is_a_409(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    task = await _task(
        db_session,
        actor,
        status="confirmed",
        date_status="resolved",
        all_day=True,
        due_date=DUE_DAY,
        event_start_date=DUE_DAY,
        event_end_date_exclusive=DUE_DAY + timedelta(days=1),
    )
    first = TaskRevision(
        user_id=actor.user_id,
        task_id=task.id,
        base_task_version=1,
        revision_type="edit_of_confirmed",
        proposed_fields={"titulo": "Primera"},
    )
    db_session.add(first)
    await db_session.flush()
    task_id = task.id
    first_id = first.id

    promoted = await client.post(
        f"/tasks/{task_id}/confirm",
        json={"expected_version": 1, "revision_id": str(first_id)},
        cookies=actor.cookies,
        headers=actor.headers,
    )
    assert promoted.status_code == 202
    assert promoted.json()["task"]["version"] == 2

    # A revision computed before the promotion is still pending, but its base version is
    # stale. It must fail even when the client sends the current task version.
    stale_revision = TaskRevision(
        user_id=actor.user_id,
        task_id=task_id,
        base_task_version=1,
        revision_type="edit_of_confirmed",
        proposed_fields={"titulo": "Obsoleta"},
    )
    db_session.add(stale_revision)
    await db_session.flush()
    stale_id = stale_revision.id

    stale = await client.post(
        f"/tasks/{task_id}/confirm",
        json={"expected_version": 2, "revision_id": str(stale_id)},
        cookies=actor.cookies,
        headers=actor.headers,
    )
    assert stale.status_code == 409
    assert _error(stale) == "version_conflict"


async def test_terminal_tasks_cannot_be_edited(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    task = await _task(db_session, actor, status="dismissed")

    response = await client.patch(
        f"/tasks/{task.id}",
        json={"version": 1, "titulo": "Tarde"},
        cookies=actor.cookies,
        headers=actor.headers,
    )
    assert response.status_code == 409
    assert _error(response) == "invalid_transition"


# --- Confirmation ----------------------------------------------------------------------


async def test_confirm_requires_a_resolved_date_and_a_calendar_block(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    unresolved = await _task(db_session, actor)
    response = await client.post(
        f"/tasks/{unresolved.id}/confirm",
        json={"expected_version": 1},
        cookies=actor.cookies,
        headers=actor.headers,
    )
    assert response.status_code == 422
    assert _error(response) == "date_unresolved"

    no_block = await _task(
        db_session,
        actor,
        date_status="resolved",
        all_day=True,
        due_date=DUE_DAY,
    )
    response = await client.post(
        f"/tasks/{no_block.id}/confirm",
        json={"expected_version": 1},
        cookies=actor.cookies,
        headers=actor.headers,
    )
    assert response.status_code == 422
    assert _error(response) == "date_unresolved"


async def test_confirm_of_an_extraction_without_evidence_is_rejected(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    task = await _task(
        db_session,
        actor,
        source="extraction",
        date_status="resolved",
        all_day=True,
        due_date=DUE_DAY,
        event_start_date=DUE_DAY,
        event_end_date_exclusive=DUE_DAY + timedelta(days=1),
    )
    response = await client.post(
        f"/tasks/{task.id}/confirm",
        json={"expected_version": 1},
        cookies=actor.cookies,
        headers=actor.headers,
    )
    assert response.status_code == 422
    assert _error(response) == "evidence_invalid"


async def test_confirm_marks_the_task_blocked_without_an_outbox(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    _, _, segment = await _class(db_session, actor)
    created = await _create_extraction(client, actor, segment)
    patched = await client.patch(
        f"/tasks/{created['id']}",
        json={
            "version": 1,
            "event_block": {
                "start_date": "2026-09-21",
                "end_date_exclusive": "2026-09-22",
            },
        },
        cookies=actor.cookies,
        headers=actor.headers,
    )
    response = await client.post(
        f"/tasks/{created['id']}/confirm",
        json={"expected_version": patched.json()["version"]},
        cookies=actor.cookies,
        headers=actor.headers,
    )
    assert response.status_code == 202
    body = response.json()
    assert body["task"]["status"] == "confirmed"
    assert body["task"]["calendar_sync_status"] == "blocked"
    assert body["notice"] == "calendar_sync_blocked"
    assert body["task"]["version"] == patched.json()["version"] + 1
    assert body["task"]["evidence"][0]["quote"] == FIRST
    stored = await db_session.get(Task, uuid.UUID(created["id"]))
    assert stored is not None
    assert stored.confirmed_at is not None
    assert stored.calendar_id is None
    assert await _sync_events(db_session, actor, uuid.UUID(created["id"])) == []


async def test_confirm_with_a_ready_calendar_creates_pending_and_one_outbox(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    await _connect_calendar(db_session, actor)
    _, _, segment = await _class(db_session, actor)
    created = await _create_extraction(client, actor, segment)
    patched = await client.patch(
        f"/tasks/{created['id']}",
        json={
            "version": 1,
            "event_block": {
                "start_date": "2026-09-21",
                "end_date_exclusive": "2026-09-22",
            },
        },
        cookies=actor.cookies,
        headers=actor.headers,
    )
    assert patched.status_code == 200

    response = await _confirm(
        client, actor, created["id"], expected_version=patched.json()["version"]
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["notice"] is None
    assert body["task"]["status"] == "confirmed"
    assert body["task"]["calendar_sync_status"] == "pending"
    assert body["task"]["version"] == patched.json()["version"] + 1
    assert body["task"]["evidence"][0]["quote"] == FIRST

    db_session.expire_all()
    stored = await db_session.get(Task, uuid.UUID(created["id"]))
    assert stored is not None
    assert stored.calendar_id == CALENDAR_ID

    events = await _sync_events(db_session, actor, uuid.UUID(created["id"]))
    assert len(events) == 1
    event = events[0]
    assert event.resource_type == "task"
    assert event.resource_version == 3
    assert event.enabled is True
    assert event.blocked_reason is None
    assert event.status == "pending"
    assert event.payload == {}
    assert event.dedupe_key == "confirm:3:1:0"
    publication = decide(
        event.type,
        enabled=event.enabled,
        blocked_reason=event.blocked_reason,
        cleanup_status=None,
    )
    assert publication.job == "sync_task_event"


async def test_repeating_confirm_with_the_current_version_reuses_the_single_outbox(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    await _connect_calendar(db_session, actor)
    _, _, segment = await _class(db_session, actor)
    created = await _create_extraction(client, actor, segment)
    patched = await client.patch(
        f"/tasks/{created['id']}",
        json={
            "version": 1,
            "event_block": {
                "start_date": "2026-09-21",
                "end_date_exclusive": "2026-09-22",
            },
        },
        cookies=actor.cookies,
        headers=actor.headers,
    )
    assert patched.status_code == 200

    first = await _confirm(
        client, actor, created["id"], expected_version=patched.json()["version"]
    )
    assert first.status_code == 202
    assert first.json()["task"]["version"] == 3
    events = await _sync_events(db_session, actor, uuid.UUID(created["id"]))
    assert len(events) == 1
    event_id = events[0].id

    repeat = await _confirm(client, actor, created["id"], expected_version=3)
    assert repeat.status_code == 202
    assert repeat.json()["task"]["version"] == 3
    assert repeat.json()["task"]["calendar_sync_status"] == "pending"
    assert repeat.json()["notice"] is None
    events = await _sync_events(db_session, actor, uuid.UUID(created["id"]))
    assert [event.id for event in events] == [event_id]

    stale = await _confirm(client, actor, created["id"], expected_version=2)
    assert stale.status_code == 409
    error = stale.json()["error"]
    assert error["code"] == "version_conflict"
    assert error["details"] == {"current_version": 3, "current_status": "confirmed"}
    events = await _sync_events(db_session, actor, uuid.UUID(created["id"]))
    assert [event.id for event in events] == [event_id]


async def test_confirm_promotes_a_revision_and_keeps_the_calendar_link(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    await _connect_calendar(db_session, actor)
    task = await _confirmable_task(
        db_session,
        actor,
        status="confirmed",
        calendar_sync_status="synced",
        calendar_id=CALENDAR_ID,
        calendar_event_id="evt-existing",
        calendar_etag="etag-1",
    )
    task_id = task.id

    revision = await client.patch(
        f"/tasks/{task_id}",
        json={"version": 1, "titulo": "Título propuesto"},
        cookies=actor.cookies,
        headers=actor.headers,
    )
    assert revision.status_code == 200
    revision_id = revision.json()["pending_revision"]["id"]

    promoted = await _confirm(
        client,
        actor,
        task_id,
        expected_version=1,
        revision_id=revision_id,
    )
    assert promoted.status_code == 202, promoted.text
    body = promoted.json()
    assert body["notice"] is None
    assert body["task"]["version"] == 2
    assert body["task"]["titulo"] == "Título propuesto"
    assert body["task"]["calendar_sync_status"] == "pending"
    assert body["task"]["pending_revision"] is None

    db_session.expire_all()
    stored = await db_session.get(Task, task_id)
    assert stored is not None
    assert stored.calendar_id == CALENDAR_ID
    assert stored.calendar_event_id == "evt-existing"
    assert stored.calendar_etag == "etag-1"
    activated = await db_session.get(TaskRevision, uuid.UUID(revision_id))
    assert activated is not None
    assert activated.status == "activated"
    events = await _sync_events(db_session, actor, task_id)
    assert len(events) == 1
    assert events[0].resource_version == 2
    event_id = events[0].id

    repeat = await _confirm(client, actor, task_id, expected_version=2)
    assert repeat.status_code == 202
    assert repeat.json()["task"]["version"] == 2
    events = await _sync_events(db_session, actor, task_id)
    assert [event.id for event in events] == [event_id]


async def test_confirm_without_a_write_calendar_stays_blocked(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    await _connect_calendar(db_session, actor, target=None)
    task = await _confirmable_task(db_session, actor)
    task_id = task.id

    response = await _confirm(client, actor, task_id, expected_version=1)
    assert response.status_code == 202
    assert response.json()["task"]["status"] == "confirmed"
    assert response.json()["task"]["calendar_sync_status"] == "blocked"
    assert response.json()["notice"] == "calendar_sync_blocked"
    assert await _sync_events(db_session, actor, task_id) == []
    db_session.expire_all()
    stored = await db_session.get(Task, task_id)
    assert stored is not None
    assert stored.calendar_id is None


async def test_confirm_with_a_disconnected_credential_keeps_the_target_calendar(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    await _connect_calendar(db_session, actor, status="disconnected")
    task = await _confirmable_task(db_session, actor)
    task_id = task.id

    response = await _confirm(client, actor, task_id, expected_version=1)
    assert response.status_code == 202
    assert response.json()["task"]["calendar_sync_status"] == "blocked"
    assert response.json()["notice"] == "calendar_sync_blocked"
    assert await _sync_events(db_session, actor, task_id) == []
    db_session.expire_all()
    stored = await db_session.get(Task, task_id)
    assert stored is not None
    assert stored.calendar_id == CALENDAR_ID


async def test_confirm_without_calendar_scopes_stays_blocked(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    await _connect_calendar(db_session, actor, scopes=["openid"])
    task = await _confirmable_task(db_session, actor)
    task_id = task.id

    response = await _confirm(client, actor, task_id, expected_version=1)
    assert response.status_code == 202
    assert response.json()["task"]["calendar_sync_status"] == "blocked"
    assert response.json()["notice"] == "calendar_sync_blocked"
    assert await _sync_events(db_session, actor, task_id) == []


async def test_confirm_with_a_foreign_revision_is_a_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    other_actor = await actors.make_actor(db_session)
    task = await _confirmable_task(db_session, actor, status="confirmed")
    task_id = task.id
    same_user_task = await _confirmable_task(db_session, actor)
    foreign_task = await _confirmable_task(db_session, other_actor)

    same_user_revision = TaskRevision(
        user_id=actor.user_id,
        task_id=same_user_task.id,
        base_task_version=1,
        revision_type="edit_of_confirmed",
        proposed_fields={"titulo": "De otra tarea"},
    )
    foreign_revision = TaskRevision(
        user_id=other_actor.user_id,
        task_id=foreign_task.id,
        base_task_version=1,
        revision_type="edit_of_confirmed",
        proposed_fields={"titulo": "De otro usuario"},
    )
    db_session.add_all([same_user_revision, foreign_revision])
    await db_session.flush()

    for revision in (same_user_revision, foreign_revision):
        response = await _confirm(
            client,
            actor,
            task_id,
            expected_version=1,
            revision_id=revision.id,
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    db_session.expire_all()
    stored = await db_session.get(Task, task_id)
    assert stored is not None
    assert stored.status == "confirmed"
    assert stored.version == 1
    assert await _sync_events(db_session, actor, task_id) == []


async def test_a_stale_revision_creates_no_outbox_and_keeps_the_task(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    await _connect_calendar(db_session, actor)
    task = await _confirmable_task(db_session, actor, status="confirmed")
    task_id = task.id
    first = TaskRevision(
        user_id=actor.user_id,
        task_id=task_id,
        base_task_version=1,
        revision_type="edit_of_confirmed",
        proposed_fields={"titulo": "Primera"},
    )
    db_session.add(first)
    await db_session.flush()

    promoted = await _confirm(
        client, actor, task_id, expected_version=1, revision_id=first.id
    )
    assert promoted.status_code == 202
    assert promoted.json()["task"]["version"] == 2
    events = await _sync_events(db_session, actor, task_id)
    assert len(events) == 1

    stale_revision = TaskRevision(
        user_id=actor.user_id,
        task_id=task_id,
        base_task_version=1,
        revision_type="edit_of_confirmed",
        proposed_fields={"titulo": "Obsoleta"},
    )
    db_session.add(stale_revision)
    await db_session.flush()

    stale = await _confirm(
        client,
        actor,
        task_id,
        expected_version=2,
        revision_id=stale_revision.id,
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "version_conflict"

    db_session.expire_all()
    stored = await db_session.get(Task, task_id)
    assert stored is not None
    assert stored.version == 2
    assert stored.titulo == "Primera"
    events = await _sync_events(db_session, actor, task_id)
    assert len(events) == 1


async def test_confirm_rolls_back_the_task_when_the_outbox_insert_fails(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    actor = await actors.make_actor(db_session)
    await _connect_calendar(db_session, actor)
    task = await _confirmable_task(db_session, actor)
    task_id = task.id

    async def _broken_outbox(
        db: AsyncSession, *, user_id: uuid.UUID, task: Task
    ) -> None:
        # `type` is NOT NULL: the failure happens after the task revision/version flush.
        await db.execute(
            pg_insert(OutboxEvent).values(
                user_id=user_id,
                resource_type="task",
                resource_id=task.id,
                resource_version=task.version,
                payload={},
                enabled=True,
                blocked_reason=None,
                dedupe_key="confirm:broken",
            )
        )
        raise AssertionError("the invalid outbox insert must fail")

    monkeypatch.setattr(task_service, "_ensure_confirmation_outbox", _broken_outbox)
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await task_service.confirm_task(
                db_session, actor.user_id, task_id, expected_version=1
            )

    db_session.expire_all()
    stored = await db_session.get(Task, task_id)
    assert stored is not None
    assert stored.status == "needs_review"
    assert stored.version == 1
    assert stored.calendar_sync_status == "not_requested"
    assert stored.confirmed_at is None
    assert stored.calendar_id is None
    assert await _sync_events(db_session, actor, task_id) == []


async def test_repeating_a_blocked_confirm_after_calendar_becomes_ready_enqueues_it(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    task = await _confirmable_task(db_session, actor)
    task_id = task.id

    blocked = await _confirm(client, actor, task_id, expected_version=1)
    assert blocked.status_code == 202
    assert blocked.json()["task"]["version"] == 2
    assert blocked.json()["task"]["calendar_sync_status"] == "blocked"
    assert blocked.json()["notice"] == "calendar_sync_blocked"
    assert await _sync_events(db_session, actor, task_id) == []

    await _connect_calendar(db_session, actor)
    resumed = await _confirm(client, actor, task_id, expected_version=2)
    assert resumed.status_code == 202, resumed.text
    assert resumed.json()["task"]["version"] == 2
    assert resumed.json()["task"]["calendar_sync_status"] == "pending"
    assert resumed.json()["notice"] is None
    db_session.expire_all()
    stored = await db_session.get(Task, task_id)
    assert stored is not None
    assert stored.calendar_id == CALENDAR_ID
    events = await _sync_events(db_session, actor, task_id)
    assert len(events) == 1
    assert events[0].resource_version == 2

    repeat = await _confirm(client, actor, task_id, expected_version=2)
    assert repeat.status_code == 202
    assert repeat.json()["task"]["version"] == 2
    events = await _sync_events(db_session, actor, task_id)
    assert len(events) == 1


# --- Lifecycle -------------------------------------------------------------------------


async def test_dismiss_cancels_reminders_and_keeps_the_calendar_event(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    task = await _task(
        db_session,
        actor,
        status="confirmed",
        date_status="resolved",
        all_day=True,
        due_date=DUE_DAY,
        event_start_date=DUE_DAY,
        event_end_date_exclusive=DUE_DAY + timedelta(days=1),
        calendar_event_id="evt-kept",
    )
    notification = await _notification(db_session, actor, task)

    response = await client.post(
        f"/tasks/{task.id}/dismiss",
        json={"expected_version": 1},
        cookies=actor.cookies,
        headers=actor.headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["task"]["status"] == "dismissed"
    assert body["task"]["version"] == 2
    assert body["cancelled_reminders"] == 1
    assert body["calendar_event_kept"] is True
    assert body["notice"] == "calendar_event_kept"
    await db_session.refresh(notification)
    assert notification.status == "cancelled"


async def test_complete_is_only_valid_from_confirmed(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    task = await _task(db_session, actor)

    response = await client.post(
        f"/tasks/{task.id}/complete",
        json={"expected_version": 1},
        cookies=actor.cookies,
        headers=actor.headers,
    )
    assert response.status_code == 409
    assert _error(response) == "invalid_transition"


async def test_dismiss_and_complete_are_tenant_scoped(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    other = await actors.make_actor(db_session)
    task = await _task(db_session, actor)

    response = await client.post(
        f"/tasks/{task.id}/dismiss",
        json={"expected_version": 1},
        cookies=other.cookies,
        headers=other.headers,
    )
    assert response.status_code == 404
    assert _error(response) == "not_found"


async def test_evidence_survives_a_lifecycle_version_bump(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    _, _, segment = await _class(db_session, actor)
    segment_id = segment.id
    created = await _create_extraction(client, actor, segment)

    response = await client.post(
        f"/tasks/{created['id']}/dismiss",
        json={"expected_version": 1},
        cookies=actor.cookies,
        headers=actor.headers,
    )
    assert response.status_code == 200
    assert response.json()["task"]["evidence"][0]["quote"] == FIRST
    assert response.json()["task"]["sources"][0]["segment_id"] == str(segment_id)
    assert (
        await db_session.scalar(
            select(TaskSource).where(TaskSource.task_id == uuid.UUID(created["id"]))
        )
    ) is not None

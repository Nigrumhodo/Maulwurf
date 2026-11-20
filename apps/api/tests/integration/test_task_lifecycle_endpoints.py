"""A3.7: complete/dismiss endpoints, version control and revision cleanup."""

import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session
from app.main import app
from app.models import Notification, Task, TaskRevision
from tests.integration import actors

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skip(
        reason="Superseded by develop's test_task_endpoints.py; kept as historical A3.7 evidence."
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


async def _close(
    client: AsyncClient,
    actor: actors.Actor,
    task_id: uuid.UUID,
    action: str,
    *,
    expected_version: int,
) -> Response:
    return await client.post(
        f"/tasks/{task_id}/{action}",
        json={"expected_version": expected_version},
        cookies=actor.cookies,
        headers=actor.headers,
    )


async def test_dismiss_cancels_reminders_keeps_event_and_discards_revision(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    notification = await actors.make_notification(db_session, actor)
    task = await db_session.get(Task, notification.task_id)
    assert task is not None
    task.status = "confirmed"
    task.calendar_event_id = "evt-1"
    revision = TaskRevision(
        user_id=actor.user_id,
        task_id=task.id,
        base_task_version=task.version,
        revision_type="edit_of_confirmed",
        proposed_fields={"titulo": "Cambio pendiente"},
    )
    db_session.add(revision)
    await db_session.flush()
    task_id = task.id
    notification_id = notification.id
    revision_id = revision.id
    version = task.version

    response = await _close(
        client, actor, task_id, "dismiss", expected_version=version
    )

    assert response.status_code == 200
    body = response.json()
    assert body["task"]["status"] == "dismissed"
    assert body["task"]["version"] == version + 1
    assert body["notice"] == "calendar_event_kept"
    assert body["cancelled_reminders"] == 1
    assert body["task"]["pending_revision"] is None

    await db_session.commit()
    stored_notification = await db_session.get(Notification, notification_id)
    assert stored_notification is not None
    assert stored_notification.status == "cancelled"
    stored_revision = await db_session.get(TaskRevision, revision_id)
    assert stored_revision is not None
    assert stored_revision.status == "discarded"


async def test_complete_is_only_valid_from_confirmed(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    task = await actors.make_task(db_session, actor)
    version = task.version

    invalid = await _close(
        client, actor, task.id, "complete", expected_version=version
    )
    assert invalid.status_code == 409
    assert invalid.json()["error"]["code"] == "invalid_transition"

    task.status = "confirmed"
    await db_session.flush()

    completed = await _close(
        client, actor, task.id, "complete", expected_version=version
    )
    assert completed.status_code == 200
    assert completed.json()["task"]["status"] == "completed"
    assert completed.json()["task"]["version"] == version + 1


async def test_lifecycle_rejects_a_stale_version_and_a_foreign_task(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    alice, bob = await actors.make_actor(db_session), await actors.make_actor(db_session)
    task = await actors.make_task(db_session, alice)
    task.status = "confirmed"
    await db_session.flush()

    stale = await _close(
        client, alice, task.id, "dismiss", expected_version=task.version + 1
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "version_conflict"

    foreign = await _close(
        client, bob, task.id, "dismiss", expected_version=task.version
    )
    assert foreign.status_code == 404


async def test_dismissing_an_already_dismissed_task_is_idempotent(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    task = await actors.make_task(db_session, actor)
    task.status = "dismissed"
    await db_session.flush()
    version = task.version

    response = await _close(
        client, actor, task.id, "dismiss", expected_version=version
    )

    assert response.status_code == 200
    assert response.json()["task"]["status"] == "dismissed"
    assert response.json()["task"]["version"] == version

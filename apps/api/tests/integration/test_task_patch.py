"""A3.3 PATCH: optimistic locking, due/agenda separation and pending revisions."""

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session
from app.main import app
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


def _headers(actor: actors.Actor) -> dict[str, str]:
    return actor.headers


async def _patch(
    client: AsyncClient,
    actor: actors.Actor,
    task_id: object,
    **body: object,
) -> object:
    return await client.patch(
        f"/tasks/{task_id}",
        json=body,
        cookies=actor.cookies,
        headers=_headers(actor),
    )


async def test_active_patch_updates_due_and_agenda_without_mixing_them(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    task = await actors.make_task(db_session, actor)
    await db_session.flush()
    initial_version = task.version
    initial_due_version = task.due_version

    response = await _patch(
        client,
        actor,
        task.id,
        version=initial_version,
        titulo="Entrega corregida",
        due_date="2026-11-09",
        due_at=None,
        date_status="resolved",
        all_day=True,
        timezone="America/Bogota",
        event_block={
            "start_at": None,
            "end_at": None,
            "start_date": "2026-11-10",
            "end_date_exclusive": "2026-11-11",
        },
    )

    assert response.status_code == 200
    body = response.json()
    updated = body["task"]
    assert body["revision_id"] is None
    assert updated["titulo"] == "Entrega corregida"
    assert updated["version"] == initial_version + 1
    assert updated["due_version"] == initial_due_version + 1
    assert updated["due_date"] == "2026-11-09"
    assert updated["event_block"]["start_date"] == "2026-11-10"
    assert updated["event_block"]["start_at"] is None
    assert updated["due_date"] != updated["event_block"]["start_date"]


async def test_confirmed_patch_creates_a_revision_and_does_not_mutate_active_fields(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    task = await actors.make_task(db_session, actor)
    task.status = "confirmed"
    await db_session.flush()
    title = task.titulo
    version = task.version

    response = await _patch(
        client,
        actor,
        task.id,
        version=version,
        titulo="Cambio pendiente",
        detalle="No debe activarse todavía",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["revision_id"] is not None
    assert body["task"]["titulo"] == title
    assert body["task"]["version"] == version
    assert body["task"]["status"] == "confirmed"
    assert body["task"]["pending_revision"]["status"] == "pending_confirmation"
    assert body["task"]["pending_revision"]["proposed_fields"]["titulo"] == (
        "Cambio pendiente"
    )


async def test_patch_rejects_a_stale_version_with_the_current_status(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    task = await actors.make_task(db_session, actor)
    await db_session.flush()

    response = await _patch(
        client, actor, task.id, version=task.version + 1, titulo="Tarde"
    )

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "version_conflict"
    assert error["details"]["current_version"] == task.version
    assert error["details"]["current_status"] == task.status


async def test_patch_rejects_an_inverted_agenda_block(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    task = await actors.make_task(db_session, actor)
    await db_session.flush()

    response = await _patch(
        client,
        actor,
        task.id,
        version=task.version,
        event_block={
            "start_date": "2026-11-11",
            "end_date_exclusive": "2026-11-10",
        },
    )

    assert response.status_code == 422
    assert "event_block" in response.json()["error"]["details"]["fields"]


async def test_patch_is_tenant_scoped(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    alice, bob = await actors.make_actor(db_session), await actors.make_actor(db_session)
    task = await actors.make_task(db_session, alice)
    await db_session.flush()

    response = await _patch(client, bob, task.id, version=task.version, titulo="Ajena")

    assert response.status_code == 404

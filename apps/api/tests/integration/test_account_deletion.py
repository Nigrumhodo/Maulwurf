"""I-S4-AN-04 (A4.4): account deletion with an observable, idempotent operation.

The suite runs the real HTTP router against PostgreSQL. Google Calendar is a deterministic
`httpx.MockTransport` double: no request leaves the process, no real credential is used, and
the only token is a synthetic value encrypted by the test fixture.

Covered:
- `DELETE /me` answers `202 {deletion_operation_id}`, applies the tombstone, restricts the
  initiating session, revokes the others and stops jobs.
- The purge removes profile, text, derivatives and tokens; the residual operation keeps only
  status, authorization and a redacted error.
- `GET /me/deletion/{id}` exposes `pending|completed|completed_with_remote_failures`, only to
  the former owner, with `Cache-Control: no-store`, and expires.
- Authorized Google deletion runs before the tokens disappear; a failure is reported as
  `completed_with_remote_failures` instead of pretending success.
- A second execution is a no-op, and a second `DELETE /me` cannot be issued by the restricted
  session.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import unquote

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session
from app.core.deps import COOKIE_NAME
from app.main import app
from app.models import (
    AccountDeletion,
    CalendarSettings,
    GoogleCredential,
    GoogleRemoteOperation,
    Session,
    Subject,
    User,
)
from app.models.calendar_event import CalendarEvent
from app.routers.me import deletion_google_transport
from app.services import deletion, sessions
from tests.integration.actors import (
    Actor,
    make_actor,
    make_attempt,
    make_calendar_event,
    make_class_with_transcript,
    make_conversation,
    make_google_credential,
    make_outbox_event,
    make_subject,
    make_task,
    make_task_revision,
)

pytestmark = pytest.mark.integration

CALENDAR_ID = "maulwurf@group.calendar.example"
EVENT_ID = "maulmaulmaulmaulmaulmaulmaulmaul"
TENANT_TABLES = (
    "subjects",
    "audios",
    "ingestion_attempts",
    "variant_confirmation_tokens",
    "outbox_events",
    "transcripts",
    "segments",
    "chunks",
    "chunk_segments",
    "embeddings",
    "index_generations",
    "processing_runs",
    "conversations",
    "messages",
    "message_sources",
    "tasks",
    "task_sources",
    "task_revisions",
    "calendar_events",
    "settings",
    "google_credentials",
)


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    async def override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = override
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=settings.allowed_origin
    ) as c:
        yield c
    app.dependency_overrides.clear()


def _use_google(transport: httpx.BaseTransport) -> None:
    app.dependency_overrides[deletion_google_transport] = lambda: transport


class FakeGoogle:
    """Calendar double that records every DELETE and can fail event deletion."""

    def __init__(self, *, fail_events: bool = False) -> None:
        self.fail_events = fail_events
        self.calls: list[tuple[str, str, str | None]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = unquote(request.url.path)
        authorization = request.headers.get("Authorization")
        self.calls.append((request.method, path, authorization))
        if request.method != "DELETE":
            return httpx.Response(404, json={"error": {"reason": "notFound"}})
        if "/events/" in path:
            if self.fail_events:
                return httpx.Response(403, json={"error": {"reason": "forbidden"}})
            return httpx.Response(204)
        return httpx.Response(204)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def delete_paths(self) -> list[str]:
        return [path for method, path, _ in self.calls if method == "DELETE"]


async def _delete_me(
    client: AsyncClient,
    actor: Actor,
    *,
    delete_events: bool = False,
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
) -> httpx.Response:
    payload = {"delete_maulwurf_events": delete_events} if body is None else body
    return await client.request(
        "DELETE",
        "/me",
        json=payload,
        cookies=actor.cookies,
        headers=headers if headers is not None else actor.headers,
    )


async def _count(db: AsyncSession, table: str, user_id: uuid.UUID) -> int:
    # The table name is one of the fixed constants in TENANT_TABLES, never request input.
    value = await db.scalar(
        text(f'SELECT count(*) FROM "{table}" WHERE user_id = :user_id'),  # noqa: S608
        {"user_id": user_id},
    )
    return int(value or 0)


async def _seed_full_tenant(db: AsyncSession, actor: Actor) -> dict[str, Any]:
    subject = await make_subject(db, actor)
    audio, attempt, transcript = await make_class_with_transcript(db, actor)
    conversation = await make_conversation(db, actor)
    outbox = await make_outbox_event(db, actor)
    task = await make_task(db, actor)
    revision = await make_task_revision(db, actor)
    mirror = await make_calendar_event(db, actor)
    settings_row = CalendarSettings(user_id=actor.user_id)
    db.add(settings_row)
    credential = await make_google_credential(db, actor)
    await db.flush()
    return {
        "subject": subject,
        "audio": audio,
        "attempt": attempt,
        "transcript": transcript,
        "conversation": conversation,
        "outbox": outbox,
        "task": task,
        "revision": revision,
        "mirror": mirror,
        "settings": settings_row,
        "credential": credential,
    }


async def test_delete_me_tombstones_blocks_and_purges(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await make_actor(db_session)
    other_session = await sessions.create_session(db_session, actor.user_id)
    initiator_id = actor.session.id
    other_session_id = other_session.session.id
    seeded = await _seed_full_tenant(db_session, actor)
    subject_id = seeded["subject"].id
    original_email = actor.email
    original_sub = actor.google_sub

    response = await _delete_me(client, actor)

    assert response.status_code == 202
    deletion_id = uuid.UUID(response.json()["deletion_operation_id"])
    db_session.expire_all()
    operation = await db_session.get(AccountDeletion, deletion_id)
    assert operation is not None
    assert operation.status == "completed"
    assert operation.former_user_id == actor.user_id
    assert operation.delete_events is False
    assert operation.expires_at > datetime.now(UTC)

    # The profile is a tombstone without profile data; identifiers are opaque placeholders.
    user = await db_session.get(User, actor.user_id)
    assert user is not None
    assert user.status == "deleted"
    assert user.deleted_at is not None
    assert user.email != original_email
    assert user.google_sub != original_sub
    assert user.display_name is None

    # Every tenant-owned row is gone; only the residual operation and the initiator survive.
    for table in TENANT_TABLES:
        assert await _count(db_session, table, actor.user_id) == 0, table
    assert await _count(db_session, "sessions", actor.user_id) == 1

    # The initiating session is restricted; the others were revoked.
    initiator = await db_session.get(Session, initiator_id)
    assert initiator is not None
    assert initiator.revoked_at is None
    assert initiator.deletion_operation_id == deletion_id
    # The purge deletes the rest of the session hashes; the cookie can no longer authenticate.
    assert await db_session.get(Session, other_session_id) is None
    blocked = await client.get(
        "/me", cookies={COOKIE_NAME: other_session.cookie_token}
    )
    assert blocked.status_code == 401

    # Blocked channels: profile and library are no longer reachable.
    assert (await client.get("/me", cookies=actor.cookies)).status_code == 401
    assert (await client.get("/subjects", cookies=actor.cookies)).status_code == 401
    second_delete = await _delete_me(client, actor)
    assert second_delete.status_code == 401

    # The operation is observable without profile, text or tokens.
    status = await client.get(
        f"/me/deletion/{deletion_id}", cookies=actor.cookies
    )
    assert status.status_code == 200
    assert status.headers["cache-control"] == "no-store"
    body = status.json()
    assert set(body) == {
        "id",
        "status",
        "delete_maulwurf_events",
        "created_at",
        "completed_at",
        "expires_at",
        "error_summary",
    }
    assert body["id"] == str(deletion_id)
    assert body["status"] == "completed"
    assert body["delete_maulwurf_events"] is False
    assert body["completed_at"] is not None
    assert body["error_summary"] is None
    serialized = status.text
    for forbidden in (
        original_email,
        original_sub,
        "texto de prueba",
        "access_token",
        "refresh_token",
        str(subject_id),
    ):
        assert forbidden not in serialized

    # Logout is the only other allowed mutation and it ends the restricted session.
    logout = await client.post("/auth/logout", cookies=actor.cookies, headers=actor.headers)
    assert logout.status_code == 204
    assert (
        await client.get(f"/me/deletion/{deletion_id}", cookies=actor.cookies)
    ).status_code == 401


async def test_delete_me_runs_authorized_google_deletion_before_tokens_are_removed(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await make_actor(db_session)
    task = await make_task(db_session, actor)
    task.status = "confirmed"
    task.calendar_sync_status = "synced"
    task.calendar_id = CALENDAR_ID
    task.calendar_event_id = EVENT_ID
    mirror = CalendarEvent(
        user_id=actor.user_id,
        calendar_id=CALENDAR_ID,
        event_id=EVENT_ID,
        source="maulwurf",
        title="Entrega",
        start_at=datetime.now(UTC) + timedelta(days=1),
        end_at=datetime.now(UTC) + timedelta(days=1, hours=1),
        last_synced_at=datetime.now(UTC),
    )
    db_session.add(mirror)
    db_session.add(CalendarSettings(user_id=actor.user_id, calendar_write_calendar_id=CALENDAR_ID))
    credential = await make_google_credential(db_session, actor)
    await db_session.flush()
    assert credential.status == "connected"

    google = FakeGoogle()
    _use_google(google.transport)

    response = await _delete_me(client, actor, delete_events=True)

    assert response.status_code == 202
    deletion_id = uuid.UUID(response.json()["deletion_operation_id"])
    paths = google.delete_paths()
    assert f"/calendar/v3/calendars/{CALENDAR_ID}/events/{EVENT_ID}" in paths
    assert f"/calendar/v3/calendars/{CALENDAR_ID}" in paths
    assert all(auth is not None and auth.startswith("Bearer ") for _, _, auth in google.calls)

    # The tokens existed for the remote step and are gone afterwards.
    assert await db_session.get(GoogleCredential, actor.user_id) is None
    operations = (
        await db_session.scalars(
            select(GoogleRemoteOperation).where(
                GoogleRemoteOperation.user_id == actor.user_id
            )
        )
    ).all()
    assert {op.kind for op in operations} == {"delete_event", "delete_calendar"}
    assert all(op.status == "succeeded" for op in operations)
    assert all(op.authorized for op in operations)

    operation = await db_session.get(AccountDeletion, deletion_id)
    assert operation is not None
    assert operation.status == "completed"
    assert operation.delete_events is True


async def test_remote_failure_is_reported_and_still_purges(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await make_actor(db_session)
    task = await make_task(db_session, actor)
    task.calendar_sync_status = "synced"
    task.calendar_id = CALENDAR_ID
    task.calendar_event_id = EVENT_ID
    await make_google_credential(db_session, actor)
    await db_session.flush()

    google = FakeGoogle(fail_events=True)
    _use_google(google.transport)

    response = await _delete_me(client, actor, delete_events=True)

    assert response.status_code == 202
    deletion_id = uuid.UUID(response.json()["deletion_operation_id"])
    operation = await db_session.get(AccountDeletion, deletion_id)
    assert operation is not None
    assert operation.status == "completed_with_remote_failures"
    assert operation.error_summary == "google_calendar_forbidden"
    assert operation.completed_at is not None

    failed = (
        await db_session.scalars(
            select(GoogleRemoteOperation).where(
                GoogleRemoteOperation.user_id == actor.user_id,
                GoogleRemoteOperation.status == "failed",
            )
        )
    ).all()
    assert [op.kind for op in failed] == ["delete_event"]
    assert failed[0].error_code == "google_calendar_forbidden"

    # The purge is not conditional on the remote outcome.
    assert await _count(db_session, "tasks", actor.user_id) == 0
    assert await _count(db_session, "google_credentials", actor.user_id) == 0


async def test_event_deletion_without_credentials_is_a_remote_failure(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await make_actor(db_session)
    task = await make_task(db_session, actor)
    task.calendar_sync_status = "synced"
    task.calendar_id = CALENDAR_ID
    task.calendar_event_id = EVENT_ID
    await db_session.flush()

    response = await _delete_me(client, actor, delete_events=True)

    assert response.status_code == 202
    deletion_id = uuid.UUID(response.json()["deletion_operation_id"])
    operation = await db_session.get(AccountDeletion, deletion_id)
    assert operation is not None
    assert operation.status == "completed_with_remote_failures"
    assert operation.error_summary == "google_not_connected"
    failed = (
        await db_session.scalars(
            select(GoogleRemoteOperation).where(
                GoogleRemoteOperation.user_id == actor.user_id
            )
        )
    ).all()
    assert {op.status for op in failed} == {"failed"}
    assert {op.error_code for op in failed} == {"google_not_connected"}


async def test_status_is_pending_before_the_purge_runs(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await make_actor(db_session)
    await make_attempt(db_session, actor)
    await db_session.flush()

    operation = await deletion.create_account_deletion(
        db_session,
        user_id=actor.user_id,
        session_id=actor.session.id,
        delete_events=False,
    )
    deletion_id = operation.id
    await db_session.commit()

    response = await client.get(f"/me/deletion/{deletion_id}", cookies=actor.cookies)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["status"] == "pending"
    assert response.json()["completed_at"] is None
    # A restricted session cannot reach the profile while the operation is pending.
    assert (await client.get("/me", cookies=actor.cookies)).status_code == 401


async def test_create_and_execute_are_idempotent(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await make_actor(db_session)
    other_session = await sessions.create_session(db_session, actor.user_id)
    other_session_id = other_session.session.id
    await make_attempt(db_session, actor)
    await make_outbox_event(db_session, actor)
    await db_session.flush()

    first = await deletion.create_account_deletion(
        db_session,
        user_id=actor.user_id,
        session_id=actor.session.id,
        delete_events=False,
    )
    second = await deletion.create_account_deletion(
        db_session,
        user_id=actor.user_id,
        session_id=actor.session.id,
        delete_events=False,
    )
    assert first.id == second.id
    deletion_id = first.id
    await db_session.commit()

    # The tombstone already blocks the jobs and the other sessions before the purge runs.
    revoked = await db_session.get(Session, other_session_id)
    assert revoked is not None
    assert revoked.revoked_at is not None
    outbox = await db_session.scalar(
        text("SELECT status FROM outbox_events WHERE user_id = :uid AND type = 'task_cancelled'"),
        {"uid": actor.user_id},
    )
    assert outbox == "skipped"
    attempt_status = await db_session.scalar(
        text("SELECT status FROM ingestion_attempts WHERE user_id = :uid"), {"uid": actor.user_id}
    )
    assert attempt_status == "cancelled"

    await deletion.execute_account_deletion(db_session, deletion_id)
    await db_session.commit()
    status_before = await db_session.scalar(
        select(AccountDeletion.status).where(AccountDeletion.id == deletion_id)
    )
    assert status_before == "completed"

    google = FakeGoogle()
    _use_google(google.transport)
    await deletion.execute_account_deletion(db_session, deletion_id, transport=google.transport)
    await db_session.commit()
    assert google.calls == []
    assert (
        await db_session.scalar(
            select(AccountDeletion.status).where(AccountDeletion.id == deletion_id)
        )
        == "completed"
    )


async def test_deletion_is_scoped_to_the_owner_and_expires(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await make_actor(db_session)
    other = await make_actor(db_session)
    await make_subject(db_session, actor)
    other_subject = await make_subject(db_session, other)
    other_subject_id = other_subject.id
    await db_session.flush()

    response = await _delete_me(client, actor)
    assert response.status_code == 202
    deletion_id = uuid.UUID(response.json()["deletion_operation_id"])

    foreign = await client.get(f"/me/deletion/{deletion_id}", cookies=other.cookies)
    unknown = await client.get(f"/me/deletion/{uuid.uuid4()}", cookies=other.cookies)
    assert foreign.status_code == 404
    assert unknown.status_code == 404

    # The other tenant is untouched.
    assert await _count(db_session, "subjects", other.user_id) == 1
    assert await _count(db_session, "subjects", actor.user_id) == 0
    assert await db_session.get(Subject, other_subject_id) is not None

    await db_session.execute(
        update(AccountDeletion)
        .where(AccountDeletion.id == deletion_id)
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await db_session.commit()
    expired = await client.get(f"/me/deletion/{deletion_id}", cookies=actor.cookies)
    assert expired.status_code == 404


async def test_missing_csrf_does_not_create_a_deletion(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await make_actor(db_session)

    response = await _delete_me(client, actor, headers={})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_invalid"
    count = await db_session.scalar(
        select(func.count())
        .select_from(AccountDeletion)
        .where(AccountDeletion.former_user_id == actor.user_id)
    )
    assert count == 0

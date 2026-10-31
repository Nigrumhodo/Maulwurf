"""I-S3-JF-06 (J3.3): incremental Calendar connection and reconnect resumption.

Runs against the real PostgreSQL schema with deterministic Google doubles: `exchange_code`
and `verify_id_token` are replaced, and the Calendar API is an `httpx.MockTransport`. No
request leaves the process and no secret is involved (J3.1's protected-account verification
and the cloud leg of P-S3-JF-02 stay pending).

Covered:
- `connect|reconnect` require CSRF and a session; `reconnect` requires a previous grant.
- The authorization request asks only for the Calendar capability scopes (plus `openid`
  to bind the grant), keeps the login scopes through `include_granted_scopes` and requests
  offline access.
- No Calendar scopes in the token response → clean rejection, nothing persisted, and the
  library/chat endpoints keep working.
- A grant for another Google account or another session is rejected.
- The single "Maulwurf" calendar is created once (or reused) and its ID is stored in
  `settings`; `GET /integrations/status` exposes it.
- After a reconnect, only current blocked tasks move to `pending` and one
  `sync_task_event` outbox row is created or reused; dismissed/completed tasks and stale
  revisions are never reactivated.
- `invalid_grant` from the Calendar API leaves the integration disconnected.

A3.1's `tasks`/`task_revisions` tables are not in `develop`. The resumption tests create
the documented subset of `tasks` for the duration of the test (the fixture keeps a real
table if one ever exists), so the SQL is exercised against PostgreSQL without pre-empting
A3.1's migration.
"""

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from httpx import ASGITransport, AsyncClient, Response
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.config import settings
from app.core.db import get_session
from app.core.deps import OAUTH_COOKIE_NAME
from app.main import app
from app.models import GoogleCredential
from app.services import calendar_connection, oauth_google
from app.services.google_calendar import GoogleCalendarService
from app.services.oauth_google import CALENDAR_SCOPES
from tests.integration.actors import Actor, make_actor

pytestmark = pytest.mark.integration

CONNECT = "/integrations/google/connect"
RECONNECT = "/integrations/google/reconnect"
CALENDAR_BASE = "https://calendar.example/v3"
MAULWURF_ID = "mw-1@group.calendar.google.com"

# A3.1 shape, restricted to the columns J3.3 reads/writes. If the real table exists, the
# fixture uses it instead of creating this stand-in.
TASKS_DDL = """
CREATE TABLE tasks (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id               uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status                text NOT NULL DEFAULT 'needs_review'
                              CHECK (status IN ('pending','needs_review','confirmed',
                                                'dismissed','completed')),
    calendar_sync_status  text NOT NULL DEFAULT 'not_requested'
                              CHECK (calendar_sync_status IN
                                     ('not_requested','pending','synced','failed','blocked')),
    version               integer NOT NULL DEFAULT 1,
    updated_at            timestamptz NOT NULL DEFAULT now()
)
"""


@pytest.fixture
async def client(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    monkeypatch.setattr(settings, "google_client_id", "test-client-id")
    monkeypatch.setattr(settings, "google_client_secret", SecretStr("test-client-secret"))

    async def override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = override
    async with AsyncClient(transport=ASGITransport(app=app), base_url=settings.allowed_origin) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
async def tasks_table(db_session: AsyncSession) -> AsyncIterator[None]:
    """Expose A3.1's `tasks` subset inside the test transaction.

    The table is created in the same transaction the request-scoped session uses, so
    PostgreSQL's transactional DDL removes it when the fixture rolls back. When A3.1's
    migration eventually creates the real table, this fixture leaves it untouched.
    """
    existing = await db_session.scalar(text("SELECT to_regclass('public.tasks')"))
    if existing is None:
        await db_session.execute(text(TASKS_DDL))
    yield


def _cookie_value(response: Response, name: str) -> str:
    for header in response.headers.get_list("set-cookie"):
        if header.startswith(f"{name}="):
            return header.split(";", 1)[0].split("=", 1)[1]
    raise AssertionError(f"cookie {name} no presente en la respuesta")


def _use_calendar(
    monkeypatch: pytest.MonkeyPatch, handler_holder: dict[str, Any], *, max_retries: int = 1
) -> None:
    def factory(access_token: str, on_invalid_grant: Any) -> GoogleCalendarService:
        return GoogleCalendarService(
            access_token=access_token,
            base_url=CALENDAR_BASE,
            max_retries=max_retries,
            transport=httpx.MockTransport(handler_holder["handler"]),
            sleeper=lambda _delay: None,
            jitter=lambda: 0.0,
            on_invalid_grant=on_invalid_grant,
        )

    monkeypatch.setattr(calendar_connection, "calendar_service", factory)


def _fake_google(
    monkeypatch: pytest.MonkeyPatch,
    *,
    sub: str,
    scope: str,
    access_token: str = "ya29.test-calendar",  # noqa: S107 - fixture, not a credential
    refresh_token: str | None = "1//test-refresh",  # noqa: S107 - fixture, not a credential
) -> None:
    async def fake_exchange(code: str, code_verifier: str) -> dict[str, Any]:
        assert code and code_verifier
        tokens: dict[str, Any] = {
            "access_token": access_token,
            "id_token": "fake-id-token",
            "expires_in": 3600,
            "scope": scope,
        }
        if refresh_token is not None:
            tokens["refresh_token"] = refresh_token
        return tokens

    async def fake_verify_id_token(id_token: str, expected_nonce: str) -> dict[str, Any]:
        assert id_token == "fake-id-token"
        return {
            "sub": sub,
            "email": "student@example.test",
            "email_verified": True,
            "nonce": expected_nonce,
        }

    monkeypatch.setattr(oauth_google, "exchange_code", fake_exchange)
    monkeypatch.setattr(oauth_google, "verify_id_token", fake_verify_id_token)


def _calendar_handler(
    *, existing: list[dict[str, object]] | None = None, create_status: int = 200
) -> dict[str, Any]:
    """Deterministic Calendar double: list, create and get."""
    state: dict[str, Any] = {
        "existing": list(existing or []),
        "create_status": create_status,
        "created": [],
        "calls": [],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"].append((request.method, request.url.path))
        if request.method == "GET" and "/calendarList" in request.url.path:
            return httpx.Response(200, json={"items": state["existing"]})
        if request.method == "POST" and request.url.path.endswith("/calendars"):
            body = json.loads(request.content)
            state["created"].append(body)
            if create_status != 200:
                return httpx.Response(create_status, json={"error": {"reason": "backendError"}})
            return httpx.Response(200, json={**body, "id": MAULWURF_ID})
        if request.method == "GET" and "/calendars/" in request.url.path:
            for entry in state["existing"]:
                if entry.get("id") in request.url.path:
                    return httpx.Response(200, json=entry)
            return httpx.Response(404, json={"error": {"errors": [{"reason": "notFound"}]}})
        return httpx.Response(404, json={"error": {"errors": [{"reason": "notFound"}]}})

    state["handler"] = handler
    return state


async def _start(
    client: AsyncClient, actor: Actor, path: str = CONNECT
) -> tuple[Response, str, str]:
    response = await client.post(
        path, cookies=actor.cookies, headers=actor.headers, follow_redirects=False
    )
    assert response.status_code == 302
    query = parse_qs(urlparse(response.headers["location"]).query)
    return response, query["state"][0], _cookie_value(response, OAUTH_COOKIE_NAME)


async def _callback(
    client: AsyncClient,
    actor: Actor,
    state: str,
    cookie: str,
    *,
    code: str = "code-1",
    cookies: dict[str, str] | None = None,
) -> Response:
    return await client.get(
        "/auth/google/callback",
        params={"code": code, "state": state},
        cookies={OAUTH_COOKIE_NAME: cookie, **(cookies or actor.cookies)},
        follow_redirects=False,
    )


def _scope_of(response: Response) -> list[str]:
    query = parse_qs(urlparse(response.headers["location"]).query)
    return query["scope"][0].split()


async def _credential(db_session: AsyncSession, actor: Actor) -> GoogleCredential | None:
    await db_session.commit()  # rows written by the handler live in its savepoint
    return await db_session.get(GoogleCredential, actor.user_id)


async def _settings_calendar_id(db_session: AsyncSession, actor: Actor) -> str | None:
    await db_session.commit()
    value = await db_session.scalar(
        text("SELECT calendar_write_calendar_id FROM settings WHERE user_id = :user"),
        {"user": actor.user_id},
    )
    return None if value is None else str(value)


async def _seed_credential(
    db_session: AsyncSession,
    actor: Actor,
    *,
    scopes: list[str],
    status: str = "connected",
    refresh_token: bool = True,
) -> GoogleCredential:
    blob, version = crypto.encrypt(
        "ya29.previous", user_id=actor.user_id, field="google_credentials.access_token"
    )
    refresh_blob = None
    if refresh_token:
        refresh_blob, _ = crypto.encrypt(
            "1//previous", user_id=actor.user_id, field="google_credentials.refresh_token"
        )
    credential = GoogleCredential(
        user_id=actor.user_id,
        access_token_enc=blob,
        refresh_token_enc=refresh_blob,
        key_version=version,
        scopes=scopes,
        status=status,
    )
    db_session.add(credential)
    await db_session.flush()
    # Handlers may roll back on a rejected grant; a committed seed is what a real prior
    # request would have left behind (the outer test transaction still rolls it back).
    await db_session.commit()
    return credential


# --- Request phase --------------------------------------------------------------------


async def test_connect_and_reconnect_are_in_the_openapi_contract(client: AsyncClient) -> None:
    paths = (await client.get("/openapi.json")).json()["paths"]

    assert CONNECT in paths and RECONNECT in paths


async def test_connect_requires_a_mutation_session(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await make_actor(db_session)

    response = await client.post(CONNECT, cookies=actor.cookies)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_invalid"


async def test_connect_requests_only_the_new_calendar_scopes(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await make_actor(db_session)
    short_first_scope = CALENDAR_SCOPES[0].rsplit("/", 1)[-1]
    await _seed_credential(
        db_session, actor, scopes=["openid", "email", "profile", short_first_scope]
    )

    response, _, cookie = await _start(client, actor)
    query = parse_qs(urlparse(response.headers["location"]).query)
    requested = _scope_of(response)

    # `openid` only binds the grant; the login scopes are never requested again.
    assert requested == ["openid", CALENDAR_SCOPES[1], CALENDAR_SCOPES[2]]
    assert "email" not in requested and "profile" not in requested
    assert query["include_granted_scopes"] == ["true"]
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]
    assert query["login_hint"] == [actor.email]
    assert query["redirect_uri"] == [f"{settings.allowed_origin}/auth/google/callback"]
    assert response.headers["cache-control"] == "no-store"
    assert cookie


async def test_reconnect_without_a_previous_grant_is_a_typed_conflict(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await make_actor(db_session)

    response = await client.post(
        RECONNECT, cookies=actor.cookies, headers=actor.headers, follow_redirects=False
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "google_not_connected"


async def test_reconnect_asks_for_the_whole_calendar_set(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await make_actor(db_session)
    await _seed_credential(
        db_session,
        actor,
        scopes=[*CALENDAR_SCOPES, "openid", "email", "profile"],
        status="disconnected",
    )

    response, _, _ = await _start(client, actor, RECONNECT)
    requested = _scope_of(response)

    # The previous grant cannot be trusted after invalid_grant.
    assert requested == ["openid", *CALENDAR_SCOPES]


async def test_connect_without_oauth_configuration_fails_cleanly(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    actor = await make_actor(db_session)
    monkeypatch.setattr(settings, "google_client_id", None)
    monkeypatch.setattr(settings, "google_client_secret", None)

    response = await client.post(
        CONNECT, cookies=actor.cookies, headers=actor.headers, follow_redirects=False
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "google_integration_unavailable"


# --- Callback phase -------------------------------------------------------------------


async def test_callback_rejects_a_grant_without_calendar_scopes_and_keeps_the_app_usable(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    actor = await make_actor(db_session)
    await _seed_credential(db_session, actor, scopes=["openid", "email", "profile"])
    # The callback rejects with a rollback; setup must live outside that savepoint so the
    # assertion reads the pre-request state instead of a wiped transaction.
    await db_session.commit()
    _fake_google(monkeypatch, sub=actor.google_sub, scope="openid email profile")
    _use_calendar(monkeypatch, _calendar_handler())

    _, state, cookie = await _start(client, actor)
    response = await _callback(client, actor, state, cookie)

    assert response.status_code == 302
    assert response.headers["location"] == "/settings?calendar=error&reason=scope_required"
    credential = await _credential(db_session, actor)
    assert credential is not None
    assert credential.scopes == ["openid", "email", "profile"]
    assert await _settings_calendar_id(db_session, actor) is None
    # Nothing about the refusal breaks the rest of the product.
    assert (await client.get("/integrations/status", cookies=actor.cookies)).status_code == 200
    assert (await client.get("/subjects", cookies=actor.cookies)).status_code == 200
    assert (await client.get("/audios", cookies=actor.cookies)).status_code == 200


async def test_connect_creates_one_calendar_and_stores_its_id(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    actor = await make_actor(db_session)
    _fake_google(
        monkeypatch, sub=actor.google_sub, scope="openid " + " ".join(CALENDAR_SCOPES)
    )
    calendar = _calendar_handler()
    _use_calendar(monkeypatch, calendar)

    _, state, cookie = await _start(client, actor)
    response = await _callback(client, actor, state, cookie)

    assert response.headers["location"] == "/settings?calendar=connected"
    credential = await _credential(db_session, actor)
    assert credential is not None
    assert credential.status == "connected"
    assert set(CALENDAR_SCOPES) <= set(credential.scopes)
    assert credential.refresh_token_enc is not None
    assert await _settings_calendar_id(db_session, actor) == MAULWURF_ID
    timezone = await db_session.scalar(
        text("SELECT timezone FROM users WHERE id = :id"), {"id": actor.user_id}
    )
    assert len(calendar["created"]) == 1
    assert calendar["created"][0] == {
        "summary": "Maulwurf",
        "description": calendar_connection.MAULWURF_CALENDAR_DESCRIPTION,
        "timeZone": timezone,
    }
    status = (await client.get("/integrations/status", cookies=actor.cookies)).json()["google"]
    assert status["status"] == "connected"
    assert status["calendar_id"] == MAULWURF_ID
    assert "ya29.test-calendar" not in json.dumps(status)


async def test_connect_reuses_an_existing_maulwurf_calendar(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    actor = await make_actor(db_session)
    _fake_google(monkeypatch, sub=actor.google_sub, scope=" ".join(CALENDAR_SCOPES))
    calendar = _calendar_handler(
        existing=[
            {
                "id": MAULWURF_ID,
                "summary": calendar_connection.MAULWURF_CALENDAR_SUMMARY,
                "description": calendar_connection.MAULWURF_CALENDAR_DESCRIPTION,
            }
        ]
    )
    _use_calendar(monkeypatch, calendar)

    _, state, cookie = await _start(client, actor)
    response = await _callback(client, actor, state, cookie)

    assert response.headers["location"] == "/settings?calendar=connected"
    assert calendar["created"] == []
    assert await _settings_calendar_id(db_session, actor) == MAULWURF_ID


async def test_ambiguous_create_is_reconciled_over_http(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    actor = await make_actor(db_session)
    _fake_google(monkeypatch, sub=actor.google_sub, scope=" ".join(CALENDAR_SCOPES))
    calendar = _calendar_handler(create_status=503)

    # The create fails, but the calendar exists: the reconcile step finds it.
    original = calendar["handler"]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            calendar["existing"] = [
                {
                    "id": MAULWURF_ID,
                    "summary": calendar_connection.MAULWURF_CALENDAR_SUMMARY,
                    "description": calendar_connection.MAULWURF_CALENDAR_DESCRIPTION,
                }
            ]
        response: httpx.Response = original(request)
        return response

    calendar["handler"] = handler
    _use_calendar(monkeypatch, calendar)

    _, state, cookie = await _start(client, actor)
    response = await _callback(client, actor, state, cookie)

    assert response.headers["location"] == "/settings?calendar=connected"
    assert await _settings_calendar_id(db_session, actor) == MAULWURF_ID


async def test_callback_for_another_google_account_is_rejected(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    actor = await make_actor(db_session)
    await db_session.commit()  # survives the callback's rollback on rejection
    _fake_google(monkeypatch, sub="another-google-sub", scope=" ".join(CALENDAR_SCOPES))
    _use_calendar(monkeypatch, _calendar_handler())

    _, state, cookie = await _start(client, actor)
    response = await _callback(client, actor, state, cookie)

    assert response.headers["location"] == "/settings?calendar=error&reason=wrong_account"
    assert await _credential(db_session, actor) is None
    assert await _settings_calendar_id(db_session, actor) is None


async def test_callback_for_another_session_cannot_attach_the_grant(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = await make_actor(db_session)
    bob = await make_actor(db_session)
    await db_session.commit()  # survives the callback's rollback on rejection
    _fake_google(monkeypatch, sub=alice.google_sub, scope=" ".join(CALENDAR_SCOPES))
    _use_calendar(monkeypatch, _calendar_handler())

    _, state, cookie = await _start(client, alice)
    # Bob's browser completes Alice's consent flow: the signed user_id does not match.
    response = await _callback(client, bob, state, cookie)

    assert response.headers["location"] == "/settings?calendar=error&reason=state_mismatch"
    assert await _credential(db_session, alice) is None
    assert await _credential(db_session, bob) is None


async def test_invalid_grant_from_the_calendar_api_leaves_the_integration_disconnected(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    actor = await make_actor(db_session)
    await _seed_credential(
        db_session, actor, scopes=["openid", *CALENDAR_SCOPES], status="disconnected"
    )
    await db_session.commit()  # survives the callback's rollback before the disconnect
    _fake_google(monkeypatch, sub=actor.google_sub, scope=" ".join(CALENDAR_SCOPES))

    def invalid_grant(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, json={"error": {"errors": [{"reason": "invalid_grant"}]}}
        )

    holder = {"handler": invalid_grant}
    _use_calendar(monkeypatch, holder)

    _, state, cookie = await _start(client, actor, RECONNECT)
    response = await _callback(client, actor, state, cookie)

    assert response.headers["location"] == "/settings?calendar=error&reason=invalid_grant"
    credential = await _credential(db_session, actor)
    assert credential is not None
    assert credential.status == "disconnected"


# --- Reconnect resumption (A3.1 table shape) ------------------------------------------


async def _insert_task(
    db_session: AsyncSession,
    actor: Actor,
    *,
    status: str,
    sync: str,
    version: int = 1,
) -> uuid.UUID:
    task_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO tasks (id, user_id, status, calendar_sync_status, version) "
            "VALUES (:id, :user, :status, :sync, :version)"
        ),
        {"id": task_id, "user": actor.user_id, "status": status, "sync": sync, "version": version},
    )
    await db_session.flush()
    await db_session.commit()
    return task_id


async def _task_sync(db_session: AsyncSession, task_id: uuid.UUID) -> str:
    await db_session.commit()
    status = await db_session.scalar(
        text("SELECT calendar_sync_status FROM tasks WHERE id = :id"), {"id": task_id}
    )
    assert isinstance(status, str)
    return status


async def _sync_events(db_session: AsyncSession, actor: Actor) -> list[dict[str, Any]]:
    await db_session.commit()
    rows = (
        await db_session.execute(
            text(
                "SELECT id, resource_id, resource_version, dedupe_key, enabled, "
                "blocked_reason, status FROM outbox_events "
                "WHERE user_id = :user AND type = 'sync_task_event' ORDER BY created_at, id"
            ),
            {"user": actor.user_id},
        )
    ).mappings()
    return [dict(row) for row in rows]


async def test_reconnect_resumes_only_current_blocked_tasks(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    tasks_table: None,
) -> None:
    actor = await make_actor(db_session)
    await _seed_credential(
        db_session, actor, scopes=["openid", *CALENDAR_SCOPES], status="disconnected"
    )
    blocked = await _insert_task(db_session, actor, status="confirmed", sync="blocked", version=3)
    dismissed = await _insert_task(db_session, actor, status="dismissed", sync="blocked", version=1)
    completed = await _insert_task(db_session, actor, status="completed", sync="blocked", version=1)
    unconfirmed = await _insert_task(
        db_session, actor, status="needs_review", sync="blocked", version=1
    )
    already_pending = await _insert_task(
        db_session, actor, status="confirmed", sync="pending", version=1
    )
    _fake_google(monkeypatch, sub=actor.google_sub, scope=" ".join(CALENDAR_SCOPES))
    _use_calendar(
        monkeypatch,
        _calendar_handler(
            existing=[
                {
                    "id": MAULWURF_ID,
                    "summary": calendar_connection.MAULWURF_CALENDAR_SUMMARY,
                    "description": calendar_connection.MAULWURF_CALENDAR_DESCRIPTION,
                }
            ]
        ),
    )

    _, state, cookie = await _start(client, actor, RECONNECT)
    response = await _callback(client, actor, state, cookie)

    assert response.headers["location"] == "/settings?calendar=connected"
    assert await _task_sync(db_session, blocked) == "pending"
    assert await _task_sync(db_session, dismissed) == "blocked"
    assert await _task_sync(db_session, completed) == "blocked"
    assert await _task_sync(db_session, unconfirmed) == "blocked"
    assert await _task_sync(db_session, already_pending) == "pending"
    events = await _sync_events(db_session, actor)
    assert len(events) == 1
    assert events[0]["resource_id"] == blocked
    assert events[0]["resource_version"] == 3
    assert events[0]["enabled"] is True
    assert events[0]["blocked_reason"] is None
    assert events[0]["status"] == "pending"
    assert events[0]["dedupe_key"] == "reconnect:3:0"


async def test_second_reconnect_reuses_the_live_outbox_and_starts_a_new_cycle_after_failure(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    tasks_table: None,
) -> None:
    actor = await make_actor(db_session)
    await _seed_credential(
        db_session, actor, scopes=["openid", *CALENDAR_SCOPES], status="disconnected"
    )
    blocked = await _insert_task(db_session, actor, status="confirmed", sync="blocked", version=2)
    _fake_google(monkeypatch, sub=actor.google_sub, scope=" ".join(CALENDAR_SCOPES))
    _use_calendar(monkeypatch, _calendar_handler())

    _, state, cookie = await _start(client, actor, RECONNECT)
    assert (await _callback(client, actor, state, cookie)).headers["location"] == (
        "/settings?calendar=connected"
    )
    events = await _sync_events(db_session, actor)
    assert len(events) == 1
    first_event = events[0]["id"]

    # A second reconnect finds the task already pending: nothing changes, no new event.
    _, state, cookie = await _start(client, actor, RECONNECT)
    assert (await _callback(client, actor, state, cookie)).headers["location"] == (
        "/settings?calendar=connected"
    )
    assert [event["id"] for event in await _sync_events(db_session, actor)] == [first_event]

    # The dispatcher consumed the event and the task became blocked again: a new reconnect
    # starts the next cycle instead of reusing the terminal event.
    await db_session.execute(
        text("UPDATE outbox_events SET status = 'failed' WHERE id = :id"), {"id": first_event}
    )
    await db_session.execute(
        text(
            "UPDATE tasks SET calendar_sync_status = 'blocked' WHERE id = :id"
        ),
        {"id": blocked},
    )
    await db_session.flush()
    _, state, cookie = await _start(client, actor, RECONNECT)
    assert (await _callback(client, actor, state, cookie)).headers["location"] == (
        "/settings?calendar=connected"
    )
    events = await _sync_events(db_session, actor)
    assert len(events) == 2
    # `created_at` defaults to `now()`, identical inside one transaction: compare the set,
    # not a positional order that depends on the UUID tie-break.
    assert {event["dedupe_key"] for event in events} == {"reconnect:2:0", "reconnect:2:1"}
    assert [event["status"] for event in events if event["dedupe_key"] == "reconnect:2:1"] == [
        "pending"
    ]
    assert await _task_sync(db_session, blocked) == "pending"


async def test_a_reconnect_without_the_tasks_table_still_connects(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A3.1 is not in `develop`: the connection must not fail because of it."""
    actor = await make_actor(db_session)
    await _seed_credential(db_session, actor, scopes=["openid"], status="disconnected")
    _fake_google(monkeypatch, sub=actor.google_sub, scope=" ".join(CALENDAR_SCOPES))
    _use_calendar(monkeypatch, _calendar_handler())

    _, state, cookie = await _start(client, actor, RECONNECT)
    response = await _callback(client, actor, state, cookie)

    assert response.headers["location"] == "/settings?calendar=connected"
    assert await _settings_calendar_id(db_session, actor) == MAULWURF_ID

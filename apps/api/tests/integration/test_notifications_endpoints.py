"""I-S4-JF-05 (A4.2): notification preferences, test send, listing and warned resend.

Runs against the real PostgreSQL schema and the real HTTP routes. Gmail is not called:
J4.1 owns the provider adapter, so the delivery pipeline is a deterministic controlled
double built with the real A4.1 state machine (`claim`, `mark_sent`,
`mark_delivery_unknown`, `expire_lease`). The contract under test is the endpoint half of
I-S4-JF-05:

- `PATCH /me/notifications` is a partial, validated update of the single per-user settings
  row, and `GET /me/notifications` reads it without writing.
- `POST /notifications/test` is Gmail opt-in only and records an audited `test` occurrence
  for the user's verified email.
- The listing is tenant-scoped, filterable and keyset-paginated.
- `POST /notifications/{id}/resend` requires `acknowledge_duplicate_risk=true`, creates a
  **new** audited occurrence and never mutates the original `delivery_unknown` row.
- A lost response or an expired `sending` lease ends `delivery_unknown`; there is no
  automatic retry and no row is pushed back to `pending`.

No real email, credential, provider call or class audio is involved.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.main import app
from app.models import CalendarSettings, Notification, Task
from app.services.notifications import (
    RESPONSE_LOST,
    claim,
    dedupe_key,
    expire_lease,
    mark_delivery_unknown,
    mark_sent,
)
from tests.integration.actors import Actor, make_actor, make_task

pytestmark = pytest.mark.integration

PREFERENCES = "/me/notifications"
NOTIFICATIONS = "/notifications"
TEST_SEND = "/notifications/test"
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
OCCURRENCE = date(2026, 11, 9)


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    async def override() -> AsyncIterator[AsyncSession]:
        yield db_session

    # Handlers commit inside the fixture's savepoint; the outer transaction rolls back.
    app.dependency_overrides[get_session] = override
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://app.maulwurf.test"
    ) as c:
        yield c
    app.dependency_overrides.clear()


async def _verified_actor(db: AsyncSession, *, timezone: str = "UTC") -> Actor:
    actor = await make_actor(db)
    actor.user.email_verified = True
    actor.user.timezone = timezone
    await db.flush()
    return actor


async def _opt_in(db: AsyncSession, actor: Actor) -> CalendarSettings:
    row = await db.get(CalendarSettings, actor.user_id)
    if row is None:
        row = CalendarSettings(user_id=actor.user_id)
        db.add(row)
    row.gmail_opt_in = True
    await db.flush()
    return row


def _key(actor: Actor, *, type: str, task_id: uuid.UUID | None, due_version: int | None,
         occurrence: date | None, sequence: int = 0) -> str:
    return dedupe_key(
        user_id=actor.user_id,
        type=type,
        task_id=task_id,
        due_version=due_version,
        occurrence=occurrence,
        occurrence_seq=sequence,
    )


def _notification(
    actor: Actor,
    *,
    status: str = "pending",
    type: str = "test",
    task: Task | None = None,
    occurrence: date | None = OCCURRENCE,
    scheduled_at: datetime = NOW,
    sequence: int = 0,
    due_version: int | None = None,
    last_error_code: str | None = None,
    attempts: int = 0,
    sent_at: datetime | None = None,
    provider_message_id: str | None = None,
    lease_expires_at: datetime | None = None,
) -> Notification:
    task_id = task.id if task is not None else None
    generation = task.due_version if task is not None else due_version
    return Notification(
        user_id=actor.user_id,
        task_id=task_id,
        type=type,
        occurrence=occurrence,
        due_version=generation,
        scheduled_at=scheduled_at,
        dedupe_key=_key(
            actor,
            type=type,
            task_id=task_id,
            due_version=generation,
            occurrence=occurrence,
            sequence=sequence,
        ),
        occurrence_seq=sequence,
        status=status,
        attempts=attempts,
        sent_at=sent_at,
        provider_message_id=provider_message_id,
        last_error_code=last_error_code,
        lease_expires_at=lease_expires_at,
    )


async def _store(db: AsyncSession, row: Notification) -> Notification:
    db.add(row)
    await db.flush()
    return row


async def _count(db: AsyncSession) -> int:
    return int(await db.scalar(select(func.count()).select_from(Notification)) or 0)


class ControlledDelivery:
    """Deterministic stand-in for the J4.1 Gmail adapter / J4.2 scheduler.

    It drives the real A4.1 state machine so the endpoint test observes the exact states the
    production pipeline will produce. `lost` models a response lost after the provider may
    have accepted the message; `expired` models a worker that died holding the lease.
    """

    async def send(self, db: AsyncSession, row: Notification, *, outcome: str) -> None:
        now = datetime.now(UTC)
        claim(row, now=now, lease_expires_at=now + timedelta(minutes=5))
        await db.flush()
        if outcome == "sent":
            mark_sent(row, now=now, provider_message_id=f"gmail-{uuid.uuid4()}")
        elif outcome == "lost":
            mark_delivery_unknown(row, error_code=RESPONSE_LOST)
        elif outcome == "expired":
            row.lease_expires_at = now - timedelta(seconds=1)
            await db.flush()
            assert expire_lease(row, now=now) is True
        else:  # pragma: no cover - test helper guard
            raise AssertionError(f"unknown delivery outcome: {outcome}")
        await db.flush()


# --- PATCH /me/notifications -----------------------------------------------------------


async def test_preferences_default_to_the_m8_values_without_creating_a_row(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await _verified_actor(db_session)

    response = await client.get(PREFERENCES, cookies=actor.cookies)

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    body = response.json()
    assert body == {
        "gmail_opt_in": False,
        "digest_daily_enabled": False,
        "digest_daily_time": "07:00",
        "digest_weekly_enabled": False,
        "digest_weekday": 0,
        "reminder_offsets_hours": [48, 24, 2],
        "reminder_allday_offsets_days": [3, 1],
        "updated_at": None,
    }
    assert await db_session.get(CalendarSettings, actor.user_id) is None


async def test_patch_updates_only_the_sent_fields_and_persists_them(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await _verified_actor(db_session)

    response = await client.patch(
        PREFERENCES,
        json={
            "gmail_opt_in": True,
            "digest_daily_time": "08:15",
            "reminder_offsets_hours": [2, 48, 2, 24],
        },
        cookies=actor.cookies,
        headers=actor.headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["gmail_opt_in"] is True
    assert body["digest_daily_time"] == "08:15"
    assert body["reminder_offsets_hours"] == [48, 24, 2]
    # Omitted fields keep the M8 defaults.
    assert body["digest_daily_enabled"] is False
    assert body["digest_weekly_enabled"] is False
    assert body["digest_weekday"] == 0
    assert body["reminder_allday_offsets_days"] == [3, 1]
    assert body["updated_at"] is not None

    row = await db_session.get(CalendarSettings, actor.user_id)
    assert row is not None
    assert row.gmail_opt_in is True
    assert str(row.digest_daily_time) == "08:15:00"
    assert row.reminder_offsets_hours == [48, 24, 2]


async def test_an_empty_patch_is_a_no_op_and_does_not_create_the_row(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await _verified_actor(db_session)

    response = await client.patch(
        PREFERENCES, json={}, cookies=actor.cookies, headers=actor.headers
    )

    assert response.status_code == 200
    assert response.json()["gmail_opt_in"] is False
    assert await db_session.get(CalendarSettings, actor.user_id) is None


async def test_patch_requires_the_session_origin_and_csrf(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await _verified_actor(db_session)

    no_csrf = await client.patch(
        PREFERENCES, json={"gmail_opt_in": True}, cookies=actor.cookies
    )
    no_session = await client.patch(PREFERENCES, json={"gmail_opt_in": True})

    assert no_csrf.status_code == 403
    assert no_csrf.json()["error"]["code"] == "csrf_invalid"
    assert no_session.status_code == 401
    assert no_session.json()["error"]["code"] == "auth_required"


@pytest.mark.parametrize(
    "payload",
    [
        {"digest_weekday": 7},
        {"digest_daily_time": "07:30:15"},
        {"reminder_offsets_hours": [0]},
        {"reminder_allday_offsets_days": [-1]},
        {"gmail_send": True},
    ],
)
async def test_invalid_preferences_are_a_typed_422(
    client: AsyncClient, db_session: AsyncSession, payload: dict[str, Any]
) -> None:
    actor = await _verified_actor(db_session)

    response = await client.patch(
        PREFERENCES, json=payload, cookies=actor.cookies, headers=actor.headers
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


async def test_preferences_are_tenant_scoped(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    alice = await _verified_actor(db_session)
    bob = await _verified_actor(db_session)
    await client.patch(
        PREFERENCES,
        json={"gmail_opt_in": True, "digest_weekly_enabled": True},
        cookies=alice.cookies,
        headers=alice.headers,
    )

    response = await client.get(PREFERENCES, cookies=bob.cookies)

    assert response.status_code == 200
    assert response.json()["gmail_opt_in"] is False
    assert response.json()["digest_weekly_enabled"] is False


# --- POST /notifications/test ----------------------------------------------------------


async def test_test_send_requires_the_gmail_opt_in(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await _verified_actor(db_session)

    response = await client.post(TEST_SEND, cookies=actor.cookies, headers=actor.headers)

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "notifications_test_unavailable"
    assert error["details"]["reason"] == "gmail_not_opted_in"
    assert await _count(db_session) == 0


async def test_test_send_requires_a_verified_recipient(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await make_actor(db_session)
    actor.user.timezone = "UTC"
    await _opt_in(db_session, actor)

    response = await client.post(TEST_SEND, cookies=actor.cookies, headers=actor.headers)

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "notifications_test_unavailable"
    assert error["details"]["reason"] == "recipient_unverified"
    assert await _count(db_session) == 0


async def test_test_send_records_an_audited_pending_occurrence(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await _verified_actor(db_session)
    await _opt_in(db_session, actor)
    today = datetime.now(UTC).date()

    response = await client.post(TEST_SEND, cookies=actor.cookies, headers=actor.headers)

    assert response.status_code == 202
    body = response.json()
    notification = body
    assert notification["type"] == "test"
    assert notification["status"] == "pending"
    assert notification["occurrence"] == today.isoformat()
    assert notification["occurrence_seq"] == 1
    assert notification["task_id"] is None
    assert notification["last_error_code"] is None
    assert body["warning"]["code"] == "delivery_uncertain"
    # The verified address is used server-side and never echoed in the response.
    assert actor.email not in response.text

    stored = await db_session.get(Notification, uuid.UUID(notification["id"]))
    assert stored is not None
    assert stored.status == "pending"
    assert stored.dedupe_key == dedupe_key(
        user_id=actor.user_id,
        type="test",
        task_id=None,
        due_version=None,
        occurrence=today,
        occurrence_seq=1,
    )


async def test_a_second_test_send_is_a_new_audited_occurrence(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await _verified_actor(db_session)
    await _opt_in(db_session, actor)

    first = await client.post(TEST_SEND, cookies=actor.cookies, headers=actor.headers)
    second = await client.post(TEST_SEND, cookies=actor.cookies, headers=actor.headers)

    assert first.status_code == second.status_code == 202
    one, two = first.json(), second.json()
    assert one["id"] != two["id"]
    assert (one["occurrence_seq"], two["occurrence_seq"]) == (1, 2)
    assert await _count(db_session) == 2


async def test_the_controlled_pipeline_marks_a_test_send_sent(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await _verified_actor(db_session)
    await _opt_in(db_session, actor)
    created = await client.post(TEST_SEND, cookies=actor.cookies, headers=actor.headers)
    notification_id = uuid.UUID(created.json()["id"])
    stored = await db_session.get(Notification, notification_id)
    assert stored is not None

    await ControlledDelivery().send(db_session, stored, outcome="sent")

    page = await client.get(NOTIFICATIONS, cookies=actor.cookies)
    item = page.json()["items"][0]
    assert item["status"] == "sent"
    assert item["sent_at"] is not None
    # A confirmed delivery is terminal: the lease expiry pass cannot retry it.
    assert expire_lease(stored, now=datetime.now(UTC) + timedelta(days=1)) is False
    assert await _count(db_session) == 1


# --- delivery_unknown and no automatic retry ------------------------------------------


async def test_a_lost_response_is_delivery_unknown_and_is_never_retried(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await _verified_actor(db_session)
    await _opt_in(db_session, actor)
    created = await client.post(TEST_SEND, cookies=actor.cookies, headers=actor.headers)
    notification_id = uuid.UUID(created.json()["id"])
    stored = await db_session.get(Notification, notification_id)
    assert stored is not None

    await ControlledDelivery().send(db_session, stored, outcome="lost")

    assert stored.status == "delivery_unknown"
    assert stored.last_error_code == RESPONSE_LOST
    assert stored.attempts == 1
    # No automatic retry: the uncertainty is terminal and no pending row is created.
    assert expire_lease(stored, now=datetime.now(UTC) + timedelta(days=1)) is False
    pending = await db_session.scalar(
        select(func.count()).select_from(Notification).where(
            Notification.user_id == actor.user_id, Notification.status == "pending"
        )
    )
    assert pending == 0
    assert await _count(db_session) == 1

    page = await client.get(NOTIFICATIONS, cookies=actor.cookies)
    item = page.json()["items"][0]
    assert item["status"] == "delivery_unknown"
    assert item["last_error_code"] == RESPONSE_LOST


async def test_an_expired_sending_lease_is_delivery_unknown_not_pending(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await _verified_actor(db_session)
    row = await _store(
        db_session,
        _notification(
            actor,
            status="sending",
            scheduled_at=NOW,
            attempts=1,
            lease_expires_at=NOW - timedelta(seconds=1),
        ),
    )

    assert expire_lease(row, now=NOW) is True
    await db_session.flush()

    assert row.status == "delivery_unknown"
    assert row.last_error_code == "lease_expired"
    assert row.status != "pending"
    assert expire_lease(row, now=NOW + timedelta(days=1)) is False


# --- GET /notifications: cursor and filters -------------------------------------------


async def _seed_many(db: AsyncSession, actor: Actor, count: int = 5) -> list[Notification]:
    rows = []
    for index in range(count):
        rows.append(
            await _store(
                db,
                _notification(
                    actor,
                    status="sent" if index % 2 else "delivery_unknown",
                    scheduled_at=NOW - timedelta(hours=index),
                    sequence=index,
                    sent_at=NOW - timedelta(hours=index) if index % 2 else None,
                    provider_message_id=f"gmail-{index}" if index % 2 else None,
                    last_error_code=None if index % 2 else RESPONSE_LOST,
                    attempts=1,
                ),
            )
        )
    return rows


async def test_listing_pages_through_the_tenant_deliveries_with_an_opaque_cursor(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await _verified_actor(db_session)
    seeded = await _seed_many(db_session, actor, 5)

    first = await client.get(NOTIFICATIONS, params={"limit": 2}, cookies=actor.cookies)
    assert first.status_code == 200
    first_body = first.json()
    assert first.headers["Cache-Control"] == "no-store"
    assert len(first_body["items"]) == 2
    cursor = first_body["next_cursor"]
    assert cursor is not None
    assert "2026-11-02" not in cursor

    second = await client.get(
        NOTIFICATIONS,
        params={"limit": 2, "cursor": cursor},
        cookies=actor.cookies,
    )
    third = await client.get(
        NOTIFICATIONS,
        params={"limit": 2, "cursor": second.json()["next_cursor"]},
        cookies=actor.cookies,
    )

    ids = [item["id"] for item in first_body["items"] + second.json()["items"]
           + third.json()["items"]]
    assert len(ids) == len(set(ids)) == 5
    assert set(ids) == {str(row.id) for row in seeded}
    assert third.json()["next_cursor"] is None


async def test_listing_filters_by_status_and_window(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await _verified_actor(db_session)
    await _seed_many(db_session, actor, 4)

    only_unknown = await client.get(
        NOTIFICATIONS,
        params=[("status", "delivery_unknown")],
        cookies=actor.cookies,
    )
    assert only_unknown.status_code == 200
    assert {item["status"] for item in only_unknown.json()["items"]} == {
        "delivery_unknown"
    }

    both = await client.get(
        NOTIFICATIONS,
        params=[("status", "sent"), ("status", "delivery_unknown")],
        cookies=actor.cookies,
    )
    assert both.status_code == 200
    assert {item["status"] for item in both.json()["items"]} == {
        "sent",
        "delivery_unknown",
    }

    window = await client.get(
        NOTIFICATIONS,
        params={
            "from": (NOW - timedelta(hours=3)).isoformat(),
            "to": (NOW - timedelta(hours=1)).isoformat(),
        },
        cookies=actor.cookies,
    )
    assert window.status_code == 200
    scheduled = [item["scheduled_at"] for item in window.json()["items"]]
    assert scheduled == sorted(scheduled, reverse=True)
    assert all(
        (NOW - timedelta(hours=3)) <= datetime.fromisoformat(item) < (NOW - timedelta(hours=1))
        for item in scheduled
    )


async def test_listing_breaks_equal_scheduled_at_ties_by_id(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await _verified_actor(db_session)
    seeded = []
    for sequence in range(1, 4):
        seeded.append(
            await _store(
                db_session,
                _notification(
                    actor,
                    status="sent",
                    scheduled_at=NOW,
                    sequence=sequence,
                    sent_at=NOW,
                ),
            )
        )
    seeded_ids = {str(row.id) for row in seeded}

    seen: list[str] = []
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {"limit": 1}
        if cursor is not None:
            params["cursor"] = cursor
        page = await client.get(NOTIFICATIONS, params=params, cookies=actor.cookies)
        assert page.status_code == 200
        body = page.json()
        seen.extend(item["id"] for item in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break

    assert len(seen) == len(set(seen)) == 3
    assert set(seen) == seeded_ids


@pytest.mark.parametrize(
    "params",
    [
        {"status": "queued"},
        {"cursor": "not-base64!!"},
        {"from": "2026-11-02T12:00:00"},
        {"from": "2026-11-02T12:00:00+00:00", "to": "2026-11-02T11:00:00+00:00"},
        {"limit": 0},
        {"limit": 101},
    ],
)
async def test_listing_rejects_invalid_filters_and_cursors(
    client: AsyncClient, db_session: AsyncSession, params: dict[str, Any]
) -> None:
    actor = await _verified_actor(db_session)

    response = await client.get(NOTIFICATIONS, params=params, cookies=actor.cookies)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


async def test_listing_is_tenant_scoped(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    alice = await _verified_actor(db_session)
    bob = await _verified_actor(db_session)
    await _seed_many(db_session, alice, 2)

    response = await client.get(NOTIFICATIONS, cookies=bob.cookies)

    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None}


# --- POST /notifications/{id}/resend ---------------------------------------------------


async def _unknown_reminder(db: AsyncSession, actor: Actor) -> Notification:
    task = await make_task(db, actor)
    return await _store(
        db,
        _notification(
            actor,
            type="reminder_t24",
            task=task,
            status="delivery_unknown",
            scheduled_at=NOW,
            attempts=1,
            last_error_code=RESPONSE_LOST,
        ),
    )


async def test_resend_requires_the_duplicate_risk_acknowledgement(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await _verified_actor(db_session)
    row = await _unknown_reminder(db_session, actor)
    row_id = row.id

    missing = await client.post(
        f"{NOTIFICATIONS}/{row_id}/resend",
        json={},
        cookies=actor.cookies,
        headers=actor.headers,
    )
    refused = await client.post(
        f"{NOTIFICATIONS}/{row_id}/resend",
        json={"acknowledge_duplicate_risk": False},
        cookies=actor.cookies,
        headers=actor.headers,
    )

    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "validation_failed"
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "acknowledgement_required"
    assert await _count(db_session) == 1
    await db_session.refresh(row)
    assert row.status == "delivery_unknown"


async def test_resend_creates_a_new_audited_occurrence_and_keeps_the_original_intact(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await _verified_actor(db_session)
    original = await _unknown_reminder(db_session, actor)
    original_id = original.id
    original_key = original.dedupe_key
    original_updated_at = original.updated_at
    original_task_id = original.task_id
    original_occurrence = original.occurrence
    original_due_version = original.due_version

    response = await client.post(
        f"{NOTIFICATIONS}/{original_id}/resend",
        json={"acknowledge_duplicate_risk": True},
        cookies=actor.cookies,
        headers=actor.headers,
    )

    assert response.status_code == 202
    body = response.json()
    created = body
    assert created["id"] != str(original_id)
    assert created["status"] == "pending"
    assert created["occurrence_seq"] == 1
    assert created["type"] == "reminder_t24"
    assert created["task_id"] == str(original_task_id)
    assert original_occurrence is not None
    assert created["occurrence"] == original_occurrence.isoformat()
    assert created["due_version"] == original_due_version
    assert body["warning"]["code"] == "duplicate_risk_acknowledged"

    await db_session.refresh(original)
    assert original.status == "delivery_unknown"
    assert original.last_error_code == RESPONSE_LOST
    assert original.attempts == 1
    assert original.dedupe_key == original_key
    assert original.updated_at == original_updated_at

    new_row = await db_session.get(Notification, uuid.UUID(created["id"]))
    assert new_row is not None
    assert new_row.status == "pending"
    assert new_row.occurrence_seq == 1
    assert new_row.dedupe_key == f"{original_key}|n:1"
    assert new_row.dedupe_key != original_key
    assert await _count(db_session) == 2


async def test_each_resend_gets_the_next_sequence(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await _verified_actor(db_session)
    original = await _unknown_reminder(db_session, actor)
    original_id = original.id

    first = await client.post(
        f"{NOTIFICATIONS}/{original_id}/resend",
        json={"acknowledge_duplicate_risk": True},
        cookies=actor.cookies,
        headers=actor.headers,
    )
    second = await client.post(
        f"{NOTIFICATIONS}/{original_id}/resend",
        json={"acknowledge_duplicate_risk": True},
        cookies=actor.cookies,
        headers=actor.headers,
    )

    assert first.status_code == second.status_code == 202
    assert first.json()["occurrence_seq"] == 1
    assert second.json()["occurrence_seq"] == 2
    assert await _count(db_session) == 3


@pytest.mark.parametrize("status", ["pending", "sending", "cancelled"])
async def test_a_queued_in_flight_or_cancelled_occurrence_is_not_resendable(
    client: AsyncClient, db_session: AsyncSession, status: str
) -> None:
    actor = await _verified_actor(db_session)
    row = await _store(
        db_session,
        _notification(
            actor,
            status=status,
            scheduled_at=NOW,
            lease_expires_at=(
                NOW + timedelta(minutes=5) if status == "sending" else None
            ),
        ),
    )

    response = await client.post(
        f"{NOTIFICATIONS}/{row.id}/resend",
        json={"acknowledge_duplicate_risk": True},
        cookies=actor.cookies,
        headers=actor.headers,
    )

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "notification_not_resendable"
    assert error["details"]["status"] == status
    assert await _count(db_session) == 1


async def test_resending_a_failed_delivery_is_allowed(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await _verified_actor(db_session)
    row = await _store(
        db_session,
        _notification(
            actor,
            status="failed",
            scheduled_at=NOW,
            attempts=1,
            last_error_code="provider_rejected",
        ),
    )
    row_id = row.id

    response = await client.post(
        f"{NOTIFICATIONS}/{row_id}/resend",
        json={"acknowledge_duplicate_risk": True},
        cookies=actor.cookies,
        headers=actor.headers,
    )

    assert response.status_code == 202
    assert response.json()["status"] == "pending"
    await db_session.refresh(row)
    assert row.status == "failed"
    assert row.last_error_code == "provider_rejected"


async def test_resending_a_sent_delivery_is_allowed_with_the_duplicate_warning(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await _verified_actor(db_session)
    row = await _store(
        db_session,
        _notification(
            actor,
            status="sent",
            scheduled_at=NOW,
            attempts=1,
            sent_at=NOW,
            provider_message_id="gmail-original",
        ),
    )
    row_id = row.id

    response = await client.post(
        f"{NOTIFICATIONS}/{row_id}/resend",
        json={"acknowledge_duplicate_risk": True},
        cookies=actor.cookies,
        headers=actor.headers,
    )

    assert response.status_code == 202
    assert response.json()["occurrence_seq"] == 1
    await db_session.refresh(row)
    assert row.status == "sent"
    assert row.provider_message_id == "gmail-original"


async def test_resend_is_tenant_scoped_and_unknown_ids_are_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    alice = await _verified_actor(db_session)
    bob = await _verified_actor(db_session)
    row = await _unknown_reminder(db_session, alice)
    row_id = row.id

    foreign = await client.post(
        f"{NOTIFICATIONS}/{row_id}/resend",
        json={"acknowledge_duplicate_risk": True},
        cookies=bob.cookies,
        headers=bob.headers,
    )
    unknown = await client.post(
        f"{NOTIFICATIONS}/{uuid.uuid4()}/resend",
        json={"acknowledge_duplicate_risk": True},
        cookies=bob.cookies,
        headers=bob.headers,
    )

    assert foreign.status_code == 404
    assert unknown.status_code == 404
    assert foreign.json()["error"]["code"] == "not_found"
    assert await _count(db_session) == 1


async def test_resend_requires_the_session_origin_and_csrf(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await _verified_actor(db_session)
    row = await _unknown_reminder(db_session, actor)

    response = await client.post(
        f"{NOTIFICATIONS}/{row.id}/resend",
        json={"acknowledge_duplicate_risk": True},
        cookies=actor.cookies,
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_invalid"
    assert await _count(db_session) == 1


async def test_notification_payloads_do_not_echo_the_recipient_or_private_content(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await _verified_actor(db_session)
    await _unknown_reminder(db_session, actor)

    listing = await client.get(NOTIFICATIONS, cookies=actor.cookies)
    item = listing.json()["items"][0]

    assert actor.email not in listing.text
    assert "mensaje" not in item
    assert "body" not in item
    assert "recipient" not in item
    assert set(item) == {
        "id",
        "type",
        "status",
        "task_id",
        "occurrence",
        "due_version",
        "occurrence_seq",
        "scheduled_at",
        "attempts",
        "sent_at",
        "last_error_code",
        "created_at",
        "updated_at",
    }

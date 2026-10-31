"""I-S3-JF-05 (J3.7): atomic -7/+60 calendar read, recurrences and mirror publication.

Gate: G7-CAL / M5. Runs against PostgreSQL with the real migration chain. Google is replaced
by a deterministic source at the service boundary, except for the pagination case, which
drives the real `GoogleCalendarEventSource` over an `httpx.MockTransport`. No real Google
call, credential or secret is involved.

Covered: the whole window is replaced in one transaction (previous rows and the recorded
coverage survive a mid-publication failure), deleted events disappear, an event rescheduled
from outside the window updates in place, pre-window rows are pruned for the republished
calendars, recurring instances arrive expanded, other tenants are untouched, the
`GET /calendar/events` route reads only the mirror and reports freshness/coverage (a lone
`from`/`to` derives the other bound from it), and `syncToken` is never mixed with window
filters.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session
from app.main import app
from app.models import CalendarEvent, CalendarSettings
from app.services.calendar_sync import (
    CalendarEventInvalid,
    CalendarWindow,
    GoogleCalendarEventSource,
    RemoteCalendarEvent,
    sync_calendar_window,
)
from app.services.google_calendar import GoogleCalendarService
from tests.integration.actors import Actor, make_actor

pytestmark = pytest.mark.integration

CALENDAR = "primary"


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    async def override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = override
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=settings.allowed_origin
    ) as http:
        yield http
    app.dependency_overrides.clear()


class FakeSource:
    """Deterministic calendar source; records what the service asked for."""

    def __init__(self, events: list[RemoteCalendarEvent] | None = None) -> None:
        self.events = events or []
        self.calls: list[tuple[str, datetime, datetime]] = []
        self.fail_after: int | None = None

    async def list_events(
        self, calendar_id: str, *, time_min: datetime, time_max: datetime
    ) -> list[RemoteCalendarEvent]:
        self.calls.append((calendar_id, time_min, time_max))
        if self.fail_after is not None and len(self.calls) > self.fail_after:
            raise CalendarEventInvalid("read_failed")
        return [event for event in self.events if event.calendar_id == calendar_id]


def _timed(
    event_id: str,
    start: datetime,
    *,
    calendar_id: str = CALENDAR,
    title: str | None = None,
) -> RemoteCalendarEvent:
    return RemoteCalendarEvent(
        calendar_id=calendar_id,
        event_id=event_id,
        title=title or event_id,
        start_at=start,
        end_at=start + timedelta(hours=1),
        timezone="America/Bogota",
    )


def _all_day(
    event_id: str, start: date, end: date, *, calendar_id: str = CALENDAR
) -> RemoteCalendarEvent:
    return RemoteCalendarEvent(
        calendar_id=calendar_id,
        event_id=event_id,
        title=event_id,
        start_date=start,
        end_date_exclusive=end,
    )


async def _settings(
    db: AsyncSession, actor: Actor, *, read_calendars: list[str] | None = None
) -> CalendarSettings:
    row = CalendarSettings(
        user_id=actor.user_id,
        calendar_write_calendar_id="maulwurf@group.calendar.example",
        calendar_read_calendar_ids=read_calendars or [CALENDAR],
    )
    db.add(row)
    await db.flush()
    return row


def _window(now: datetime) -> CalendarWindow:
    return CalendarWindow.default(now=now, timezone="UTC")


async def _sync(
    db: AsyncSession,
    actor: Actor,
    *,
    source: FakeSource,
    calendars: list[str] | None = None,
    now: datetime | None = None,
) -> CalendarWindow:
    reference = now or datetime.now(UTC)
    window = _window(reference)
    await sync_calendar_window(
        db,
        user_id=actor.user_id,
        window=window,
        calendars=calendars or [CALENDAR],
        source=source,
        now=reference,
    )
    return window


async def _get(
    client: AsyncClient,
    actor: Actor,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Any:
    params: dict[str, str] = {}
    if date_from is not None:
        params["from"] = date_from.isoformat()
    if date_to is not None:
        params["to"] = date_to.isoformat()
    return await client.get("/calendar/events", params=params, cookies=actor.cookies)


async def _mirror(db: AsyncSession, actor: Actor) -> list[CalendarEvent]:
    result = await db.execute(
        select(CalendarEvent)
        .where(CalendarEvent.user_id == actor.user_id)
        .order_by(CalendarEvent.event_id)
    )
    return list(result.scalars())


async def test_publication_and_mirror_read_agree_on_a_full_window(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await make_actor(db_session)
    settings_row = await _settings(db_session, actor)
    now = datetime.now(UTC)
    start = (now + timedelta(days=1)).replace(hour=14, minute=0, second=0, microsecond=0)
    day = (now + timedelta(days=3)).date()
    source = FakeSource(
        [
            _timed("timed", start),
            _all_day("all-day", day, day + timedelta(days=1)),
        ]
    )

    window = await _sync(db_session, actor, source=source, now=now)

    rows = await _mirror(db_session, actor)
    assert {row.event_id for row in rows} == {"timed", "all-day"}
    timed_row = next(row for row in rows if row.event_id == "timed")
    assert timed_row.start_at == start
    assert timed_row.end_at == start + timedelta(hours=1)
    assert timed_row.timezone == "America/Bogota"
    assert timed_row.last_synced_at == now
    all_day_row = next(row for row in rows if row.event_id == "all-day")
    assert all_day_row.start_date == day
    assert all_day_row.end_date_exclusive == day + timedelta(days=1)
    assert all_day_row.start_at is None

    await db_session.refresh(settings_row)
    assert settings_row.calendar_last_synced_at == now
    assert settings_row.calendar_coverage_from == window.start
    assert settings_row.calendar_coverage_to == window.end
    assert settings_row.calendar_coverage_calendar_ids == [CALENDAR]

    response = await _get(client, actor, date_from=window.start_date, date_to=window.end_date)
    assert response.status_code == 200
    body = response.json()
    assert [item["event_id"] for item in body["events"]] == ["timed", "all-day"]
    assert body["from"] == window.start_date.isoformat()
    assert body["to"] == window.end_date.isoformat()
    assert body["coverage_ok"] is True
    assert body["coverage_notice"] is None
    assert body["stale"] is False
    assert body["events"][0]["start_at"] is not None
    assert body["events"][1]["start_date"] == day.isoformat()
    assert body["events"][1]["end_date_exclusive"] == (day + timedelta(days=1)).isoformat()


async def test_republishing_replaces_deleted_and_changed_events_but_not_the_outside_window(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await make_actor(db_session)
    await _settings(db_session, actor)
    now = datetime.now(UTC)
    start = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    outside = now + timedelta(days=90)

    await _sync(
        db_session,
        actor,
        source=FakeSource([_timed("keep", start), _timed("deleted", start)]),
        now=now,
    )
    db_session.add(
        CalendarEvent(
            user_id=actor.user_id,
            calendar_id=CALENDAR,
            event_id="outside",
            title="outside",
            start_at=outside,
            end_at=outside + timedelta(hours=1),
            last_synced_at=now,
        )
    )
    await db_session.flush()

    window = await _sync(
        db_session,
        actor,
        source=FakeSource([_timed("keep", start + timedelta(hours=2), title="moved")]),
        now=now,
    )

    rows = await _mirror(db_session, actor)
    assert {row.event_id for row in rows} == {"keep", "outside"}
    moved = next(row for row in rows if row.event_id == "keep")
    assert moved.title == "moved"
    assert moved.start_at == start + timedelta(hours=2)

    response = await _get(client, actor, date_from=window.start_date, date_to=window.end_date)
    body = response.json()
    assert [item["event_id"] for item in body["events"]] == ["keep"]
    assert body["coverage_ok"] is True


async def test_an_event_reprogrammed_from_outside_the_window_updates_in_place(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await make_actor(db_session)
    await _settings(db_session, actor)
    now = datetime.now(UTC)
    today = now.date()
    far = now + timedelta(days=90)

    # A wider previous publication left a row far beyond the default +60 window.
    wide = CalendarWindow.from_dates(
        today, today + timedelta(days=120), timezone="UTC"
    )
    await sync_calendar_window(
        db_session,
        user_id=actor.user_id,
        window=wide,
        calendars=[CALENDAR],
        source=FakeSource([_timed("rescheduled", far)]),
        now=now,
    )

    # The user moves it inside the default window; Google keeps the same event id.
    inside = (now + timedelta(days=2)).replace(hour=10, minute=0, second=0, microsecond=0)
    window = await _sync(
        db_session,
        actor,
        source=FakeSource([_timed("rescheduled", inside, title="moved")]),
        now=now,
    )

    rows = await _mirror(db_session, actor)
    assert [row.event_id for row in rows] == ["rescheduled"]
    assert rows[0].start_at == inside
    assert rows[0].title == "moved"
    assert rows[0].last_synced_at == now

    response = await _get(client, actor, date_from=window.start_date, date_to=window.end_date)
    body = response.json()
    assert [item["event_id"] for item in body["events"]] == ["rescheduled"]
    assert body["events"][0]["title"] == "moved"


async def test_rows_before_the_window_are_pruned_only_for_the_republished_calendars(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    await _settings(db_session, actor)
    now = datetime.now(UTC)
    old = now - timedelta(days=30)

    for calendar_id in (CALENDAR, "archive"):
        db_session.add(
            CalendarEvent(
                user_id=actor.user_id,
                calendar_id=calendar_id,
                event_id="old",
                title="old",
                start_at=old,
                end_at=old + timedelta(hours=1),
                last_synced_at=old,
            )
        )
    # A task-linked row belongs to the write path, not to the refresh.
    db_session.add(
        CalendarEvent(
            user_id=actor.user_id,
            calendar_id=CALENDAR,
            event_id="linked",
            title="linked",
            start_at=old,
            end_at=old + timedelta(hours=1),
            last_synced_at=old,
            task_id=uuid.uuid4(),
        )
    )
    await db_session.flush()

    await _sync(db_session, actor, source=FakeSource([]), now=now)
    await _sync(db_session, actor, source=FakeSource([]), now=now)

    rows = await _mirror(db_session, actor)
    # `primary` was republished twice and its pre-window rows never pile up; the linked row
    # and `archive` (not part of the selection) are not touched.
    assert [(row.calendar_id, row.event_id) for row in rows] == [
        ("primary", "linked"),
        ("archive", "old"),
    ]


async def test_a_task_linked_row_inside_the_window_keeps_its_link_across_refreshes(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    await _settings(db_session, actor)
    now = datetime.now(UTC)
    start = now + timedelta(days=2)
    task_id = uuid.uuid4()
    db_session.add(
        CalendarEvent(
            user_id=actor.user_id,
            calendar_id=CALENDAR,
            event_id="linked",
            title="linked",
            start_at=start,
            end_at=start + timedelta(hours=1),
            last_synced_at=now,
            task_id=task_id,
        )
    )
    await db_session.flush()

    # Google still returns the event inside the window: the refresh updates the mirrored
    # values in place and must leave the task link of the write path untouched.
    source = FakeSource([_timed("linked", start, title="renamed")])
    await _sync(db_session, actor, source=source, now=now)
    await _sync(db_session, actor, source=source, now=now)

    rows = await _mirror(db_session, actor)
    assert [(row.event_id, row.task_id) for row in rows] == [("linked", task_id)]
    assert rows[0].title == "renamed"


async def test_a_task_linked_row_inside_the_window_survives_a_refresh_without_it(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    await _settings(db_session, actor)
    now = datetime.now(UTC)
    start = now + timedelta(days=2)
    task_id = uuid.uuid4()
    db_session.add(
        CalendarEvent(
            user_id=actor.user_id,
            calendar_id=CALENDAR,
            event_id="linked",
            title="linked",
            start_at=start,
            end_at=start + timedelta(hours=1),
            last_synced_at=now,
            task_id=task_id,
        )
    )
    await db_session.flush()

    # Contract (J3.7): the read mirror never deletes or unlinks a task-linked row, even
    # when the remote event vanished or was moved out of the window. Those rows belong to
    # the write path (J3.4/A3.8); a stale row is better than a silently lost link.
    await _sync(db_session, actor, source=FakeSource([]), now=now)
    await _sync(db_session, actor, source=FakeSource([]), now=now)

    rows = await _mirror(db_session, actor)
    assert [(row.event_id, row.task_id) for row in rows] == [("linked", task_id)]


async def test_a_failed_read_publishes_nothing(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    await _settings(db_session, actor, read_calendars=["a", "b"])
    now = datetime.now(UTC)
    start = now + timedelta(days=1)

    await _sync(
        db_session,
        actor,
        source=FakeSource([_timed("kept", start, calendar_id="a")]),
        calendars=["a"],
        now=now,
    )
    before = [(row.event_id, row.last_synced_at) for row in await _mirror(db_session, actor)]

    source = FakeSource(
        [
            _timed("new-a", start, calendar_id="a"),
            _timed("new-b", start, calendar_id="b"),
        ]
    )
    source.fail_after = 1
    with pytest.raises(CalendarEventInvalid):
        await _sync(db_session, actor, source=source, calendars=["a", "b"], now=now)

    after = [(row.event_id, row.last_synced_at) for row in await _mirror(db_session, actor)]
    assert after == before


async def test_a_publication_failure_rolls_back_the_window_and_the_coverage(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    settings_row = await _settings(db_session, actor)
    now = datetime.now(UTC)
    start = now + timedelta(days=1)

    await _sync(
        db_session, actor, source=FakeSource([_timed("old", start)]), now=now
    )
    await db_session.refresh(settings_row)
    old_coverage = (
        settings_row.calendar_last_synced_at,
        settings_row.calendar_coverage_calendar_ids,
    )
    assert old_coverage[0] is not None

    invalid = RemoteCalendarEvent(
        calendar_id=CALENDAR,
        event_id="invalid",
        title="invalid",
        start_at=start,
        end_at=start,  # CHECK (start_at < end_at) rejects this row
    )
    with pytest.raises(IntegrityError):
        await sync_calendar_window(
            db_session,
            user_id=actor.user_id,
            window=_window(now),
            calendars=[CALENDAR],
            source=FakeSource([_timed("new", start), invalid]),
            now=now,
        )

    rows = await _mirror(db_session, actor)
    assert [row.event_id for row in rows] == ["old"]
    refreshed = await db_session.get(CalendarSettings, actor.user_id)
    assert refreshed is not None
    assert refreshed.calendar_coverage_calendar_ids == old_coverage[1]
    assert refreshed.calendar_last_synced_at == old_coverage[0]


async def test_other_tenants_are_never_replaced_or_read(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    alice = await make_actor(db_session)
    bob = await make_actor(db_session)
    await _settings(db_session, alice)
    await _settings(db_session, bob)
    now = datetime.now(UTC)
    start = now + timedelta(days=1)

    await _sync(db_session, alice, source=FakeSource([_timed("alice", start)]), now=now)
    await _sync(db_session, bob, source=FakeSource([_timed("bob", start)]), now=now)

    alice_rows = await _mirror(db_session, alice)
    bob_rows = await _mirror(db_session, bob)
    assert [row.event_id for row in alice_rows] == ["alice"]
    assert [row.event_id for row in bob_rows] == ["bob"]

    window = _window(now)
    alice_body = (
        await _get(client, alice, date_from=window.start_date, date_to=window.end_date)
    ).json()
    bob_body = (
        await _get(client, bob, date_from=window.start_date, date_to=window.end_date)
    ).json()
    assert [item["event_id"] for item in alice_body["events"]] == ["alice"]
    assert [item["event_id"] for item in bob_body["events"]] == ["bob"]


async def test_get_reports_never_synced_and_changed_coverage(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await make_actor(db_session)
    now = datetime.now(UTC)
    window = _window(now)

    empty = await _get(client, actor, date_from=window.start_date, date_to=window.end_date)
    assert empty.status_code == 200
    assert empty.json()["coverage_ok"] is False
    assert empty.json()["coverage_notice"] == "never_synced"
    assert empty.json()["events"] == []
    assert empty.json()["last_synced_at"] is None

    settings_row = await _settings(db_session, actor)
    await _sync(db_session, actor, source=FakeSource([_timed("g1", now + timedelta(days=1))]))
    await db_session.refresh(settings_row)
    settings_row.calendar_read_calendar_ids = ["another"]
    await db_session.flush()

    changed = await _get(client, actor, date_from=window.start_date, date_to=window.end_date)
    assert changed.json()["coverage_ok"] is False
    assert changed.json()["coverage_notice"] == "calendar_set_changed"
    # The mirror is still returned: coverage is a notice, never a hard failure.
    assert [item["event_id"] for item in changed.json()["events"]] == ["g1"]


async def test_get_outside_the_recorded_window_reports_outside_coverage(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await make_actor(db_session)
    settings_row = await _settings(db_session, actor)
    now = datetime.now(UTC)
    await _sync(db_session, actor, source=FakeSource([]), now=now)
    await db_session.refresh(settings_row)
    window = _window(now)

    response = await _get(
        client,
        actor,
        date_from=window.start_date - timedelta(days=30),
        date_to=window.end_date - timedelta(days=1),
    )
    assert response.json()["coverage_ok"] is False
    assert response.json()["coverage_notice"] == "outside_coverage"


async def test_get_flags_a_stale_mirror(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await make_actor(db_session)
    settings_row = await _settings(db_session, actor)
    now = datetime.now(UTC)
    window = _window(now)
    await sync_calendar_window(
        db_session,
        user_id=actor.user_id,
        window=window,
        calendars=[CALENDAR],
        source=FakeSource([_timed("g1", now + timedelta(days=1))]),
        now=now - timedelta(hours=3),
    )
    await db_session.refresh(settings_row)

    response = await _get(
        client, actor, date_from=window.start_date, date_to=window.end_date
    )
    assert response.json()["coverage_ok"] is False
    assert response.json()["coverage_notice"] == "stale"
    assert response.json()["stale"] is True


async def test_get_without_parameters_uses_seven_back_and_sixty_forward_days(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await make_actor(db_session)
    before = CalendarWindow.default(now=datetime.now(UTC), timezone="UTC")
    response = await _get(client, actor)
    after = CalendarWindow.default(now=datetime.now(UTC), timezone="UTC")

    assert response.status_code == 200
    body = response.json()
    assert (body["from"], body["to"]) in {
        (before.start_date.isoformat(), before.end_date.isoformat()),
        (after.start_date.isoformat(), after.end_date.isoformat()),
    }


async def test_get_with_a_lone_bound_derives_the_other_from_it(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await make_actor(db_session)
    future_from = datetime.now(UTC).date() + timedelta(days=90)

    from_only = await _get(client, actor, date_from=future_from)
    assert from_only.status_code == 200
    body = from_only.json()
    assert body["from"] == future_from.isoformat()
    assert body["to"] == (future_from + timedelta(days=60)).isoformat()
    assert body["coverage_notice"] == "never_synced"

    past_to = datetime.now(UTC).date() - timedelta(days=30)
    to_only = await _get(client, actor, date_to=past_to)
    assert to_only.status_code == 200
    body = to_only.json()
    assert body["from"] == (past_to - timedelta(days=7)).isoformat()
    assert body["to"] == past_to.isoformat()


async def test_get_rejects_an_inverted_range_and_requires_a_session(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await make_actor(db_session)
    inverted = await _get(
        client, actor, date_from=date(2026, 11, 10), date_to=date(2026, 11, 1)
    )
    assert inverted.status_code == 422
    assert inverted.json()["error"]["code"] == "validation_failed"
    assert "to" in inverted.json()["error"]["details"]["fields"]

    anonymous = await client.get("/calendar/events")
    assert anonymous.status_code == 401
    assert anonymous.json()["error"]["code"] == "auth_required"


class PagedGoogle:
    """Two-page Google double for the complete read → publication path."""

    def __init__(self, now: datetime) -> None:
        self.requests: list[httpx.Request] = []
        self._now = now

    def _instant(self, offset_days: int) -> str:
        moment = (self._now + timedelta(days=offset_days)).replace(
            hour=14, minute=0, second=0, microsecond=0
        )
        return moment.isoformat().replace("+00:00", "Z")

    def _event(self, offset_days: int, *, status: str = "confirmed") -> dict[str, Any]:
        start = self._instant(offset_days)
        end = (
            self._now + timedelta(days=offset_days)
        ).replace(hour=15, minute=0, second=0, microsecond=0)
        end_value = end.isoformat().replace("+00:00", "Z")
        return {
            "id": f"series_{offset_days}",
            "status": status,
            "summary": "Lecture",
            "start": {"dateTime": start, "timeZone": "America/Bogota"},
            "end": {"dateTime": end_value, "timeZone": "America/Bogota"},
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        token = request.url.params.get("pageToken")
        if token is None:
            return httpx.Response(
                200,
                json={"items": [self._event(5)], "nextPageToken": "page-1"},
            )
        return httpx.Response(
            200,
            json={"items": [self._event(12, status="cancelled"), self._event(19)]},
        )


async def test_google_pagination_is_exhausted_and_sync_token_is_never_sent(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await make_actor(db_session)
    settings_row = await _settings(db_session, actor)
    now = datetime.now(UTC)
    google = PagedGoogle(now)
    service = GoogleCalendarService(
        access_token="test-token",
        base_url="https://calendar.test/v3",
        transport=httpx.MockTransport(google.handler),
        max_retries=0,
    )
    source = GoogleCalendarEventSource(service)
    window = _window(now)

    await sync_calendar_window(
        db_session,
        user_id=actor.user_id,
        window=window,
        calendars=[CALENDAR],
        source=source,
        now=now,
    )

    assert len(google.requests) == 2
    assert all("syncToken" not in request.url.params for request in google.requests)
    assert all(request.url.params["singleEvents"] == "true" for request in google.requests)
    assert all(request.url.params["showDeleted"] == "true" for request in google.requests)

    rows = await _mirror(db_session, actor)
    # Recurring instances arrived expanded; the cancelled instance is a deletion.
    assert {row.event_id for row in rows} == {"series_5", "series_19"}
    await db_session.refresh(settings_row)

    response = await _get(
        client, actor, date_from=window.start_date, date_to=window.end_date
    )
    body = response.json()
    assert [item["event_id"] for item in body["events"]] == ["series_5", "series_19"]
    assert body["coverage_ok"] is True


async def test_unknown_calendar_rows_are_never_reachable_from_another_session(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    owner = await make_actor(db_session)
    other = await make_actor(db_session)
    await _settings(db_session, owner)
    now = datetime.now(UTC)
    window = _window(now)
    await _sync(
        db_session,
        owner,
        source=FakeSource([_timed(str(uuid.uuid4()), now + timedelta(days=1))]),
        now=now,
    )

    response = await _get(client, other, date_from=window.start_date, date_to=window.end_date)
    assert response.status_code == 200
    assert response.json()["events"] == []
    assert response.json()["coverage_notice"] == "never_synced"

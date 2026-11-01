"""I-S3-JF-05 (A3.8): per-user Calendar settings and the `calendar_events` mirror.

Gate: G7-CAL / M5. A3.8 owns the Calendar settings row and the mirror shape. J3.3 and
J3.7 provisioned both tables while A3.1 was absent, so this suite closes the remaining
contract now that `tasks` exists:

- `settings` is one row per user, with a nullable write calendar and an explicit read
  selection; the arrays reject NULL/blank ids and coverage is either empty or a complete
  window (from < to, at least one calendar, `last_synced_at` present).
- `calendar_events` is keyed by `(user_id, calendar_id, event_id)`, keeps timed instants and
  the exclusive all-day pair mutually exclusive, stores a nullable but never blank ETag, and
  links a task only through the tenant-pinned `(user_id, task_id) -> tasks (user_id, id)`
  foreign key. Deleting the task unlinks the event without deleting the mirror row.

The atomic publication itself (one savepoint, coverage and `last_synced_at`) is J3.7's
`tests/integration/test_calendar_read.py`; this module asserts the database shape that
publication and the write path rely on. No Google call, credential, audio or secret is
involved.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CalendarEvent, CalendarSettings, Task
from tests.integration.actors import Actor, make_actor, make_task

pytestmark = pytest.mark.integration

CALENDAR = "primary@group.calendar.example"
ARCHIVE = "archive@group.calendar.example"
NOW = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)
DAY = date(2026, 11, 3)


async def _reject(db: AsyncSession, row: object, *, constraint: str | None = None) -> None:
    """Insert one row inside a savepoint and assert PostgreSQL refuses it.

    `constraint` pins *which* CHECK fired: a rejection caused by an unrelated constraint
    would let a weaker blank rule pass unnoticed.
    """
    async with db.begin_nested():
        db.add(row)
        with pytest.raises(IntegrityError, match=constraint):
            await db.flush()


async def _event(actor: Actor, **overrides: Any) -> CalendarEvent:
    values: dict[str, Any] = {
        "user_id": actor.user_id,
        "calendar_id": CALENDAR,
        "event_id": f"event-{uuid.uuid4().hex}",
        "title": "Evento",
        "start_at": NOW,
        "end_at": NOW + timedelta(hours=1),
        "last_synced_at": NOW,
    }
    values.update(overrides)
    return CalendarEvent(**values)


async def _created_event(db: AsyncSession, actor: Actor, **overrides: Any) -> CalendarEvent:
    event = await _event(actor, **overrides)
    db.add(event)
    await db.flush()
    return event


def _settings(actor: Actor, **overrides: Any) -> CalendarSettings:
    values: dict[str, Any] = {"user_id": actor.user_id}
    values.update(overrides)
    return CalendarSettings(**values)


# --- settings -------------------------------------------------------------------------


async def test_settings_default_to_no_write_calendar_and_no_read_selection(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    row = _settings(actor)
    db_session.add(row)
    await db_session.flush()
    await db_session.refresh(row)

    assert row.calendar_write_calendar_id is None
    assert row.calendar_read_calendar_ids == []
    assert row.calendar_last_synced_at is None
    assert row.calendar_last_manual_sync_at is None
    assert row.calendar_coverage_from is None
    assert row.calendar_coverage_to is None
    assert row.calendar_coverage_calendar_ids == []
    assert row.created_at is not None
    assert row.updated_at is not None


async def test_settings_has_exactly_one_row_per_user(db_session: AsyncSession) -> None:
    actor = await make_actor(db_session)
    db_session.add(_settings(actor))
    await db_session.flush()

    await _reject(db_session, _settings(actor))


async def test_settings_accepts_several_read_calendars(db_session: AsyncSession) -> None:
    actor = await make_actor(db_session)
    row = _settings(
        actor,
        calendar_write_calendar_id="maulwurf@group.calendar.example",
        calendar_read_calendar_ids=[CALENDAR, ARCHIVE],
    )
    db_session.add(row)
    await db_session.flush()
    await db_session.refresh(row)

    assert row.calendar_read_calendar_ids == [CALENDAR, ARCHIVE]
    assert row.calendar_write_calendar_id == "maulwurf@group.calendar.example"


async def test_settings_rejects_blank_write_and_invalid_read_calendars(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)

    # "Blank" is the whitespace-aware `btrim` criterion, not just the empty string: spaces
    # and tabs are unusable calendar ids and must not pass the CHECK. Each case names the
    # expected constraint, so a rejection by an unrelated CHECK cannot hide a weaker blank
    # rule. The coverage cases carry a complete window: only the blank-element rule can fail.
    complete_window = {
        "calendar_coverage_from": NOW,
        "calendar_coverage_to": NOW + timedelta(days=1),
        "calendar_last_synced_at": NOW,
    }
    for value in ("", "   ", "\t", " \t "):
        await _reject(
            db_session,
            _settings(actor, calendar_write_calendar_id=value),
            constraint="ck_settings_write_calendar_not_blank",
        )
        await _reject(
            db_session,
            _settings(actor, calendar_read_calendar_ids=[value]),
            constraint="ck_settings_read_calendars_no_blank",
        )
        await _reject(
            db_session,
            _settings(actor, calendar_read_calendar_ids=[CALENDAR, value]),
            constraint="ck_settings_read_calendars_no_blank",
        )
        await _reject(
            db_session,
            _settings(
                actor, calendar_coverage_calendar_ids=[value], **complete_window
            ),
            constraint="ck_settings_coverage_calendars_no_blank",
        )
        await _reject(
            db_session,
            _settings(
                actor, calendar_coverage_calendar_ids=[ARCHIVE, value], **complete_window
            ),
            constraint="ck_settings_coverage_calendars_no_blank",
        )
    await _reject(
        db_session,
        _settings(actor, calendar_read_calendar_ids=cast("list[str]", [CALENDAR, None])),
        constraint="ck_settings_read_calendars_no_null",
    )
    await _reject(
        db_session,
        _settings(
            actor,
            calendar_coverage_calendar_ids=cast("list[str]", [None]),
            **complete_window,
        ),
        constraint="ck_settings_coverage_calendars_no_null",
    )


async def test_settings_coverage_is_either_empty_or_a_complete_window(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)

    partial: list[dict[str, Any]] = [
        {"calendar_coverage_from": NOW},
        {"calendar_coverage_to": NOW + timedelta(days=1)},
        {"calendar_coverage_from": NOW, "calendar_coverage_to": NOW},
        {
            "calendar_coverage_from": NOW,
            "calendar_coverage_to": NOW - timedelta(days=1),
            "calendar_coverage_calendar_ids": [CALENDAR],
            "calendar_last_synced_at": NOW,
        },
        {
            "calendar_coverage_from": NOW,
            "calendar_coverage_to": NOW + timedelta(days=1),
            "calendar_coverage_calendar_ids": [],
            "calendar_last_synced_at": NOW,
        },
        {
            "calendar_coverage_from": NOW,
            "calendar_coverage_to": NOW + timedelta(days=1),
            "calendar_coverage_calendar_ids": [CALENDAR],
        },
    ]
    for overrides in partial:
        await _reject(db_session, _settings(actor, **overrides))

    db_session.add(
        _settings(
            actor,
            calendar_coverage_from=NOW,
            calendar_coverage_to=NOW + timedelta(days=1),
            calendar_coverage_calendar_ids=[CALENDAR],
            calendar_last_synced_at=NOW,
        )
    )
    await db_session.flush()


# --- calendar_events identity ---------------------------------------------------------


async def test_mirror_natural_key_is_per_user_calendar_and_event(
    db_session: AsyncSession,
) -> None:
    alice = await make_actor(db_session)
    bob = await make_actor(db_session)
    await _created_event(db_session, alice, event_id="shared")

    await _reject(db_session, await _event(alice, event_id="shared"))
    await _created_event(db_session, alice, event_id="shared", calendar_id=ARCHIVE)
    await _created_event(db_session, bob, event_id="shared")


async def test_mirror_rejects_blank_identity_and_remote_status(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)

    # Every identity/status field shares the whitespace-aware `btrim` criterion, including
    # the ETag: a whitespace-only ETag would otherwise become a bogus write baseline. Each
    # case names the expected constraint, proving the blank rule itself fired.
    for value in ("", "   ", "\t", " \t "):
        await _reject(
            db_session,
            await _event(actor, calendar_id=value),
            constraint="ck_calendar_events_calendar_id_not_blank",
        )
        await _reject(
            db_session,
            await _event(actor, event_id=value),
            constraint="ck_calendar_events_event_id_not_blank",
        )
        await _reject(
            db_session,
            await _event(actor, remote_status=value),
            constraint="ck_calendar_events_remote_status_not_blank",
        )
        await _reject(
            db_session,
            await _event(actor, etag=value),
            constraint="ck_calendar_events_etag_not_blank",
        )

    event = await _created_event(db_session, actor, etag='"etag-1"')
    assert event.etag == '"etag-1"'
    unknown = await _created_event(db_session, actor, etag=None)
    assert unknown.etag is None


# --- calendar_events block and ETag ---------------------------------------------------


async def test_mirror_keeps_all_day_and_instants_mutually_exclusive(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)

    await _reject(db_session, await _event(actor, start_at=None, end_at=None))
    await _reject(
        db_session,
        await _event(actor, start_at=None, end_at=None, start_date=DAY),
    )
    await _reject(
        db_session,
        await _event(actor, start_at=None, end_at=None, start_date=DAY, end_date_exclusive=None),
    )
    await _reject(
        db_session,
        await _event(
            actor,
            start_at=None,
            end_at=None,
            start_date=DAY,
            end_date_exclusive=DAY,
        ),
    )
    await _reject(
        db_session,
        await _event(actor, start_at=NOW, end_at=NOW, start_date=DAY, end_date_exclusive=None),
    )
    await _reject(db_session, await _event(actor, end_at=NOW))

    timed = await _created_event(db_session, actor)
    assert timed.start_at == NOW
    assert timed.start_date is None

    all_day = await _created_event(
        db_session,
        actor,
        start_at=None,
        end_at=None,
        start_date=DAY,
        end_date_exclusive=DAY + timedelta(days=1),
    )
    assert all_day.start_at is None
    assert all_day.end_date_exclusive == DAY + timedelta(days=1)


# --- calendar_events <-> tasks link ---------------------------------------------------


async def test_mirror_links_only_a_task_of_the_same_tenant(db_session: AsyncSession) -> None:
    alice = await make_actor(db_session)
    bob = await make_actor(db_session)
    bob_task = await make_task(db_session, bob)

    missing = uuid.uuid4()
    await _reject(db_session, await _event(alice, task_id=missing))
    await _reject(db_session, await _event(alice, task_id=bob_task.id))

    alice_task = await make_task(db_session, alice)
    linked = await _created_event(db_session, alice, task_id=alice_task.id)
    assert linked.task_id == alice_task.id


async def test_a_task_has_at_most_one_linked_event(db_session: AsyncSession) -> None:
    actor = await make_actor(db_session)
    task = await make_task(db_session, actor)
    await _created_event(db_session, actor, task_id=task.id)

    await _reject(db_session, await _event(actor, task_id=task.id))


async def test_deleting_the_task_unlinks_the_event_without_deleting_it(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    task = await make_task(db_session, actor)
    event = await _created_event(db_session, actor, task_id=task.id)

    await db_session.execute(delete(Task).where(Task.id == task.id))
    await db_session.flush()

    stored = (
        await db_session.execute(
            select(CalendarEvent)
            .where(CalendarEvent.id == event.id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert stored.task_id is None
    assert stored.calendar_id == CALENDAR

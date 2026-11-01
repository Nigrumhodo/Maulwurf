"""I-S4-JF-06 (J4.6): daily/weekly digests by local date with the week's conflicts.

The suite runs against PostgreSQL 16 and the real migrations. It proves the M8 digest
contract without Gmail:

- the materializer arms the daily occurrence at 07:00 (configurable) in the user's local
  zone and the weekly occurrence on Sunday (0 = Sunday, configurable), with the canonical
  dedupe key that includes the local period;
- a second tick is idempotent and the J4.2 claim can take each occurrence only once;
- a user without the Gmail opt-in gets no occurrence;
- changing the time recomputes a still-pending occurrence instead of duplicating it, a
  cancelled occurrence is reopened when the preference returns, and a stale pending period
  is cancelled instead of producing a retrospective burst;
- the content builder returns the upcoming tasks and the M5 `time_conflicts` /
  `deadline_clashes` for the daily and weekly windows;
- the materializer works under the cross-tenant `maulwurf_jobs` role used by the cron.

Delivery itself is J4.1: this suite stops at the pending/claimed state the Gmail worker
consumes. No provider call, email, credential or real class audio is involved.
"""

import uuid
from datetime import UTC, date, datetime, time
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CalendarEvent, CalendarSettings, Notification, Task
from app.services.digests import (
    DigestContent,
    build_digest_content,
    materialize_due_digests,
)
from app.services.notification_cancellation import SqlNotificationCancellationStore
from app.services.notifications import dedupe_key, enabled_preference_types
from app.services.scheduler_lease import claim_due_notifications
from tests.integration.actors import Actor, make_actor, make_task

pytestmark = pytest.mark.integration

BOGOTA = "America/Bogota"
TOKYO = "Asia/Tokyo"
NOW = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)  # 05:00 Sunday in Bogota
DUE = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)  # 07:00 Sunday in Bogota
JOBS_ROLE = "maulwurf_jobs"


async def _settings(
    db: AsyncSession, actor: Actor, **overrides: Any
) -> CalendarSettings:
    values: dict[str, Any] = {
        "gmail_opt_in": True,
        "digest_daily_enabled": True,
        "digest_daily_time": time(7, 0),
        "digest_weekly_enabled": True,
        "digest_weekday": 0,
    }
    values.update(overrides)
    row = CalendarSettings(user_id=actor.user_id, **values)
    db.add(row)
    await db.flush()
    return row


async def _digests(db: AsyncSession, actor: Actor) -> list[Notification]:
    return list(
        (
            await db.scalars(
                select(Notification)
                .where(Notification.user_id == actor.user_id)
                .order_by(Notification.type, Notification.occurrence)
                .execution_options(populate_existing=True)
            )
        ).all()
    )


async def test_the_materializer_arms_the_daily_and_weekly_occurrences(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    actor.user.timezone = BOGOTA
    await _settings(db_session, actor)

    report = await materialize_due_digests(db_session, now=NOW)

    assert report.users == 1
    assert report.planned == 2
    assert report.written == 2
    assert report.cancelled == 0
    rows = await _digests(db_session, actor)
    assert [row.type for row in rows] == ["digest_daily", "digest_weekly"]
    assert [row.occurrence for row in rows] == [date(2026, 11, 1), date(2026, 11, 1)]
    assert [row.scheduled_at for row in rows] == [DUE, DUE]
    assert {row.status for row in rows} == {"pending"}
    assert {row.occurrence_seq for row in rows} == {0}
    assert {row.task_id for row in rows} == {None}
    assert {row.due_version for row in rows} == {None}
    assert [row.dedupe_key for row in rows] == [
        f"{actor.user_id}|digest_daily|-|-|2026-11-01",
        f"{actor.user_id}|digest_weekly|-|-|2026-11-01",
    ]


async def test_a_second_tick_is_idempotent_and_the_claim_takes_each_row_once(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    actor.user.timezone = BOGOTA
    await _settings(db_session, actor)

    assert (await materialize_due_digests(db_session, now=NOW)).written == 2
    again = await materialize_due_digests(db_session, now=NOW)

    assert again.planned == 2
    assert len(await _digests(db_session, actor)) == 2

    claimed = await claim_due_notifications(
        db_session,
        fencing_token=1,
        now=datetime(2026, 11, 1, 12, 0, 30, tzinfo=UTC),
    )
    assert {row.type for row in claimed} == {"digest_daily", "digest_weekly"}
    assert len(claimed) == 2
    assert (
        await claim_due_notifications(
            db_session,
            fencing_token=1,
            now=datetime(2026, 11, 1, 12, 1, tzinfo=UTC),
        )
        == []
    )


async def test_the_next_period_is_armed_after_the_local_hour(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    actor.user.timezone = BOGOTA
    await _settings(db_session, actor, digest_weekly_enabled=False)

    assert (await materialize_due_digests(db_session, now=NOW)).written == 1
    assert (await materialize_due_digests(db_session, now=DUE)).written == 1
    rows = await _digests(db_session, actor)

    # The same table now has today's pending row plus tomorrow's next daily occurrence.
    assert [row.occurrence for row in rows] == [date(2026, 11, 1), date(2026, 11, 2)]
    assert [row.scheduled_at for row in rows] == [
        DUE,
        datetime(2026, 11, 2, 12, 0, tzinfo=UTC),
    ]


async def test_the_gmail_opt_in_is_required(db_session: AsyncSession) -> None:
    actor = await make_actor(db_session)
    actor.user.timezone = BOGOTA
    await _settings(db_session, actor, gmail_opt_in=False)

    report = await materialize_due_digests(db_session, now=NOW)

    assert report.users == 0
    assert report.planned == 0
    assert report.written == 0
    assert await _digests(db_session, actor) == []


async def test_a_disabled_digest_is_not_armed(db_session: AsyncSession) -> None:
    actor = await make_actor(db_session)
    actor.user.timezone = BOGOTA
    await _settings(
        db_session,
        actor,
        digest_daily_enabled=False,
        digest_weekly_enabled=False,
    )

    report = await materialize_due_digests(db_session, now=NOW)

    assert report.users == 0
    assert report.written == 0


async def test_the_digest_hour_and_timezone_are_configurable(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    actor.user.timezone = TOKYO
    await _settings(
        db_session,
        actor,
        digest_daily_time=time(9, 30),
        digest_weekly_enabled=False,
    )

    # 20:00 UTC is 05:00 on Nov 2 in Tokyo; 09:30 JST is 00:30 UTC the same local date.
    report = await materialize_due_digests(
        db_session, now=datetime(2026, 11, 1, 20, 0, tzinfo=UTC)
    )

    assert report.written == 1
    (row,) = await _digests(db_session, actor)
    assert row.occurrence == date(2026, 11, 2)
    assert row.scheduled_at == datetime(2026, 11, 2, 0, 30, tzinfo=UTC)
    assert row.dedupe_key.endswith("|2026-11-02")


async def test_a_pending_digest_is_recomputed_when_the_time_changes(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    actor.user.timezone = BOGOTA
    settings = await _settings(
        db_session, actor, digest_weekly_enabled=False
    )

    assert (await materialize_due_digests(db_session, now=NOW)).written == 1
    (before,) = await _digests(db_session, actor)
    assert before.scheduled_at == DUE

    settings.digest_daily_time = time(9, 0)
    await db_session.flush()
    report = await materialize_due_digests(db_session, now=NOW)
    rows = await _digests(db_session, actor)

    assert report.planned == 1
    assert len(rows) == 1
    assert rows[0].id == before.id
    assert rows[0].scheduled_at == datetime(2026, 11, 1, 14, 0, tzinfo=UTC)


async def test_a_time_change_to_an_already_passed_hour_cancels_the_pending_period(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    actor.user.timezone = BOGOTA
    settings = await _settings(db_session, actor, digest_weekly_enabled=False)
    assert (await materialize_due_digests(db_session, now=NOW)).written == 1
    (today,) = await _digests(db_session, actor)
    assert today.scheduled_at == DUE

    # 04:00 in Bogota (09:00 UTC) already passed at NOW, so the next occurrence is tomorrow.
    settings.digest_daily_time = time(4, 0)
    await db_session.flush()
    report = await materialize_due_digests(db_session, now=NOW)

    assert report.cancelled == 1
    assert report.written == 1
    assert today.status == "cancelled"
    pending = [row for row in await _digests(db_session, actor) if row.status == "pending"]
    assert [row.occurrence for row in pending] == [date(2026, 11, 2)]
    assert pending[0].scheduled_at == datetime(2026, 11, 2, 9, 0, tzinfo=UTC)

    # The old 12:00 UTC instant must never deliver the cancelled period.
    claimed = await claim_due_notifications(
        db_session,
        fencing_token=1,
        now=datetime(2026, 11, 1, 11, 30, tzinfo=UTC),
    )
    assert claimed == []


async def test_a_second_tick_without_changes_does_not_rewrite_pending_rows(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    actor.user.timezone = BOGOTA
    await _settings(db_session, actor)
    assert (await materialize_due_digests(db_session, now=NOW)).written == 2
    before = {
        row.id: (row.updated_at, row.attempts, row.scheduled_at)
        for row in await _digests(db_session, actor)
    }

    again = await materialize_due_digests(db_session, now=NOW)

    assert again.planned == 2
    assert again.written == 0
    after = {
        row.id: (row.updated_at, row.attempts, row.scheduled_at)
        for row in await _digests(db_session, actor)
    }
    assert after == before


async def test_a_pending_retry_keeps_its_attempt_counter_when_the_time_changes(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    actor.user.timezone = BOGOTA
    settings = await _settings(db_session, actor, digest_weekly_enabled=False)
    assert (await materialize_due_digests(db_session, now=NOW)).written == 1
    (row,) = await _digests(db_session, actor)

    # J4.2 never returns a claimed row to `pending` (a lease expiry ends in
    # `delivery_unknown`), but a pending row that does carry an attempt must keep the
    # counter: rewriting it to zero on every tick would silently reset retry accounting.
    row.attempts = 1
    await db_session.flush()
    settings.digest_daily_time = time(9, 0)
    await db_session.flush()

    assert (await materialize_due_digests(db_session, now=NOW)).written == 1
    (updated,) = await _digests(db_session, actor)

    assert updated.id == row.id
    assert updated.scheduled_at == datetime(2026, 11, 1, 14, 0, tzinfo=UTC)
    assert updated.attempts == 1


async def test_a_cancelled_digest_is_reopened_when_the_preference_returns(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    actor.user.timezone = BOGOTA
    settings = await _settings(db_session, actor, digest_weekly_enabled=False)
    assert (await materialize_due_digests(db_session, now=NOW)).written == 1
    (original,) = await _digests(db_session, actor)

    settings.digest_daily_enabled = False
    disabled = enabled_preference_types(
        reminder_offsets_hours=(48, 24, 2),
        reminder_allday_offsets_days=(3, 1),
        digest_daily_enabled=False,
        digest_weekly_enabled=False,
    )
    assert (
        await SqlNotificationCancellationStore(db_session).cancel_obsolete(
            actor.user_id, enabled_types=disabled
        )
        == 1
    )
    (cancelled,) = await _digests(db_session, actor)
    assert cancelled.status == "cancelled"

    settings.digest_daily_enabled = True
    await db_session.flush()
    assert (await materialize_due_digests(db_session, now=NOW)).written == 1
    (reopened,) = await _digests(db_session, actor)

    assert reopened.id == original.id
    assert reopened.status == "pending"
    assert reopened.attempts == 0
    assert reopened.lease_expires_at is None
    assert reopened.sent_at is None
    assert reopened.provider_message_id is None
    assert reopened.last_error_code is None


async def test_a_future_weekly_row_follows_a_weekday_change(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    actor.user.timezone = BOGOTA
    settings = await _settings(
        db_session, actor, digest_daily_enabled=False, digest_weekly_enabled=True
    )
    assert (await materialize_due_digests(db_session, now=NOW)).written == 1
    (current,) = await _digests(db_session, actor)
    assert current.occurrence == date(2026, 11, 1)
    current.status = "sent"
    current.sent_at = datetime(2026, 11, 1, 12, 1, tzinfo=UTC)
    current.provider_message_id = "gmail-weekly"
    await db_session.flush()

    # Sunday just after delivery: the next week's row is pre-created under the old weekday.
    assert (
        await materialize_due_digests(
            db_session, now=datetime(2026, 11, 1, 12, 1, tzinfo=UTC)
        )
    ).written == 1
    (next_row,) = [
        row for row in await _digests(db_session, actor) if row.status == "pending"
    ]
    assert next_row.occurrence == date(2026, 11, 8)
    assert next_row.scheduled_at == datetime(2026, 11, 8, 12, 0, tzinfo=UTC)

    # Monday: this week's digest is already sent, so the next occurrence is still next
    # week's period; the pending row is corrected once the new weekday enters that period.
    settings.digest_weekday = 3
    await db_session.flush()
    await materialize_due_digests(
        db_session, now=datetime(2026, 11, 2, 10, 0, tzinfo=UTC)
    )
    await materialize_due_digests(
        db_session, now=datetime(2026, 11, 4, 12, 1, tzinfo=UTC)
    )

    rows = await _digests(db_session, actor)
    assert len(rows) == 2
    pending = [row for row in rows if row.status == "pending"]
    assert len(pending) == 1
    assert pending[0].id == next_row.id
    assert pending[0].occurrence == date(2026, 11, 8)
    assert pending[0].scheduled_at == datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


async def test_a_weekday_change_to_an_already_passed_day_cancels_the_current_week(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    actor.user.timezone = BOGOTA
    settings = await _settings(
        db_session, actor, digest_daily_enabled=False, digest_weekday=3
    )
    wednesday = datetime(2026, 11, 4, 10, 0, tzinfo=UTC)  # 05:00 Wednesday in Bogota
    assert (await materialize_due_digests(db_session, now=wednesday)).written == 1
    (this_week,) = await _digests(db_session, actor)
    assert this_week.occurrence == date(2026, 11, 1)
    assert this_week.scheduled_at == datetime(2026, 11, 4, 12, 0, tzinfo=UTC)

    # Monday already passed, so the next weekly occurrence is next week's Monday.
    settings.digest_weekday = 1
    await db_session.flush()
    report = await materialize_due_digests(db_session, now=wednesday)

    assert report.cancelled == 1
    assert report.written == 1
    assert this_week.status == "cancelled"
    pending = [row for row in await _digests(db_session, actor) if row.status == "pending"]
    assert [row.occurrence for row in pending] == [date(2026, 11, 8)]
    assert pending[0].scheduled_at == datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


async def test_a_stale_pending_digest_is_cancelled_instead_of_sent_late(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    actor.user.timezone = BOGOTA
    await _settings(db_session, actor, digest_weekly_enabled=False)
    assert (await materialize_due_digests(db_session, now=NOW)).written == 1
    (stale,) = await _digests(db_session, actor)

    later = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)  # 05:00 on Nov 3 in Bogota
    report = await materialize_due_digests(db_session, now=later)
    rows = await _digests(db_session, actor)

    assert report.cancelled == 1
    assert stale.status == "cancelled"
    pending = [row for row in rows if row.status == "pending"]
    assert [row.occurrence for row in pending] == [date(2026, 11, 3)]
    assert pending[0].scheduled_at == datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


async def test_a_delivered_stale_digest_is_never_rewritten(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    actor.user.timezone = BOGOTA
    await _settings(db_session, actor, digest_weekly_enabled=False)

    sent = Notification(
        user_id=actor.user_id,
        task_id=None,
        type="digest_daily",
        occurrence=date(2026, 10, 30),
        due_version=None,
        scheduled_at=datetime(2026, 10, 30, 12, 0, tzinfo=UTC),
        dedupe_key=dedupe_key(
            user_id=actor.user_id,
            type="digest_daily",
            task_id=None,
            due_version=None,
            occurrence=date(2026, 10, 30),
        ),
        occurrence_seq=0,
        status="sent",
        sent_at=datetime(2026, 10, 30, 12, 1, tzinfo=UTC),
        provider_message_id="gmail-history",
    )
    db_session.add(sent)
    await db_session.flush()

    report = await materialize_due_digests(db_session, now=NOW)
    stored = await db_session.get(Notification, sent.id)

    assert report.cancelled == 0
    assert stored is not None
    assert stored.status == "sent"
    assert stored.provider_message_id == "gmail-history"


async def test_the_materializer_runs_under_the_cross_tenant_jobs_role(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    actor.user.timezone = BOGOTA
    await _settings(db_session, actor, digest_weekly_enabled=False)

    await db_session.execute(text(f'SET LOCAL ROLE "{JOBS_ROLE}"'))
    await db_session.execute(text("SELECT set_config('app.cross_tenant', 'on', true)"))

    report = await materialize_due_digests(db_session, now=NOW)

    assert report.users == 1
    assert report.written == 1
    rows = (
        await db_session.scalars(
            select(Notification).where(Notification.user_id == actor.user_id)
        )
    ).all()
    assert [row.type for row in rows] == ["digest_daily"]


async def _task(
    db: AsyncSession,
    actor: Actor,
    *,
    title: str,
    tipo: str = "tarea",
    status: str = "confirmed",
    due_date: date | None = None,
    due_at: datetime | None = None,
) -> Task:
    task = await make_task(db, actor)
    task.titulo = title
    task.tipo = tipo
    task.status = status
    task.all_day = due_date is not None
    task.date_status = "resolved"
    task.due_date = due_date
    task.due_at = due_at
    await db.flush()
    return task


async def _event(
    db: AsyncSession,
    actor: Actor,
    *,
    title: str,
    start: datetime,
    end: datetime,
) -> CalendarEvent:
    event = CalendarEvent(
        user_id=actor.user_id,
        calendar_id="primary",
        event_id=f"event-{uuid.uuid4().hex}",
        title=title,
        start_at=start,
        end_at=end,
        last_synced_at=NOW,
    )
    db.add(event)
    await db.flush()
    return event


async def test_the_content_has_upcoming_tasks_and_the_weeks_conflicts(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    actor.user.timezone = BOGOTA
    await _settings(
        db_session,
        actor,
        digest_daily_enabled=False,
        digest_weekly_enabled=False,
        calendar_read_calendar_ids=["primary"],
    )
    today = await _task(
        db_session, actor, title="Hoy", due_date=date(2026, 11, 1)
    )
    tomorrow = await _task(
        db_session,
        actor,
        title="Mañana",
        tipo="entrega",
        status="pending",
        due_at=datetime(2026, 11, 2, 15, 0, tzinfo=UTC),
    )
    week = await _task(
        db_session, actor, title="Jueves", tipo="lectura", due_date=date(2026, 11, 5)
    )
    await _task(
        db_session,
        actor,
        title="Vencida",
        due_date=date(2026, 11, 1),
        status="dismissed",
    )
    await make_task(db_session, actor)  # undated: never enters a digest
    clash_a = await _task(
        db_session, actor, title="Examen martes", tipo="examen",
        due_date=date(2026, 11, 3),
    )
    clash_b = await _task(
        db_session, actor, title="Tarea martes", tipo="tarea",
        due_date=date(2026, 11, 3),
    )
    await _event(
        db_session,
        actor,
        title="Clase A",
        start=datetime(2026, 11, 3, 15, 0, tzinfo=UTC),
        end=datetime(2026, 11, 3, 16, 30, tzinfo=UTC),
    )
    await _event(
        db_session,
        actor,
        title="Clase B",
        start=datetime(2026, 11, 3, 16, 0, tzinfo=UTC),
        end=datetime(2026, 11, 3, 17, 0, tzinfo=UTC),
    )
    await _event(
        db_session,
        actor,
        title="Fuera de la semana",
        start=datetime(2026, 11, 9, 15, 0, tzinfo=UTC),
        end=datetime(2026, 11, 9, 16, 0, tzinfo=UTC),
    )

    weekly: DigestContent = await build_digest_content(
        db_session,
        user_id=actor.user_id,
        user_timezone=BOGOTA,
        type_="digest_weekly",
        local_date=date(2026, 11, 1),
        now=NOW,
    )
    daily: DigestContent = await build_digest_content(
        db_session,
        user_id=actor.user_id,
        user_timezone=BOGOTA,
        type_="digest_daily",
        local_date=date(2026, 11, 1),
        now=NOW,
    )

    assert (weekly.window_start, weekly.window_end) == (
        date(2026, 11, 1),
        date(2026, 11, 7),
    )
    assert [item.task_id for item in weekly.upcoming_tasks] == [
        today.id,
        tomorrow.id,
        clash_a.id,
        clash_b.id,
        week.id,
    ]
    assert [item.titulo for item in weekly.upcoming_tasks] == [
        "Hoy",
        "Mañana",
        "Examen martes",
        "Tarea martes",
        "Jueves",
    ]
    assert len(weekly.conflicts.time_conflicts) == 1
    conflict = weekly.conflicts.time_conflicts[0]
    assert {conflict.a.title, conflict.b.title} == {"Clase A", "Clase B"}
    assert [clash.date for clash in weekly.conflicts.deadline_clashes] == [
        date(2026, 11, 3)
    ]
    assert {item.task_id for item in weekly.conflicts.deadline_clashes[0].items} == {
        clash_a.id,
        clash_b.id,
    }

    assert (daily.window_start, daily.window_end) == (
        date(2026, 11, 1),
        date(2026, 11, 2),
    )
    assert [item.task_id for item in daily.upcoming_tasks] == [today.id, tomorrow.id]
    assert daily.conflicts.time_conflicts == ()
    assert daily.conflicts.deadline_clashes == ()


async def test_the_local_day_filter_uses_the_users_zone(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    actor.user.timezone = BOGOTA
    await _settings(
        db_session,
        actor,
        digest_daily_enabled=False,
        digest_weekly_enabled=False,
        calendar_read_calendar_ids=[],
    )
    # 02:00 UTC on Nov 2 is still Nov 1 at 21:00 in Bogota: it belongs to the daily window
    # starting Nov 1, not to Nov 2.
    late = await _task(
        db_session,
        actor,
        title="Noche",
        due_at=datetime(2026, 11, 2, 2, 0, tzinfo=UTC),
    )

    daily = await build_digest_content(
        db_session,
        user_id=actor.user_id,
        user_timezone=BOGOTA,
        type_="digest_daily",
        local_date=date(2026, 11, 1),
        now=NOW,
    )
    next_day = await build_digest_content(
        db_session,
        user_id=actor.user_id,
        user_timezone=BOGOTA,
        type_="digest_daily",
        local_date=date(2026, 11, 2),
        now=NOW,
    )

    assert [item.task_id for item in daily.upcoming_tasks] == [late.id]
    assert [item.local_day for item in daily.upcoming_tasks] == [date(2026, 11, 1)]
    assert next_day.upcoming_tasks == ()

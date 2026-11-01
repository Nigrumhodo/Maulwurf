"""U-S4-JF-02 (J4.4): persistent dedupe and DST for scheduled reminders (M8 §1.2).

The pure planner is exercised without PostgreSQL or Gmail, with injected clocks that cross
a real time change. The contract under test:

- an all-day reminder keeps the same local wall clock across the DST transition; only the
  UTC instant moves with the offset;
- a nonexistent local hour (spring-forward gap) moves forward to the first valid instant of
  the day, and a repeated local hour (fall-back) is armed exactly once at its first fold;
- both DST edges are deterministic, so re-planning after the transition returns the same
  `scheduled_at` and `dedupe_key`; the canonical identity carries the user-local target day,
  not the instant, so a repeated hour cannot produce two notifications;
- the `due_version`/preference change rules of A4.3 still retire the obsolete occurrences
  when the target falls on a DST day.

No provider, credential, audio or secret is involved.
"""

import uuid
from datetime import UTC, date, datetime, time
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.services.notifications import dedupe_key, obsolete_pending
from app.services.reminders import (
    ReminderOccurrence,
    ReminderTask,
    plan_task_reminders,
)

USER = uuid.UUID("22222222-2222-4222-8222-222222222222")
TASK = uuid.UUID("11111111-1111-4111-8111-111111111111")
NEW_YORK = "America/New_York"
SANTIAGO = "America/Santiago"
LORD_HOWE = "Australia/Lord_Howe"

SPRING_NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
FALL_NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def _all_day(due_date: date, *, due_version: int = 1) -> ReminderTask:
    return ReminderTask(
        id=TASK,
        user_id=USER,
        status="confirmed",
        all_day=True,
        date_status="resolved",
        due_date=due_date,
        due_at=None,
        due_version=due_version,
    )


def _timed(due_at: datetime, *, due_version: int = 1) -> ReminderTask:
    return ReminderTask(
        id=TASK,
        user_id=USER,
        status="confirmed",
        all_day=False,
        date_status="resolved",
        due_date=None,
        due_at=due_at,
        due_version=due_version,
    )


def _rows(plan: tuple[ReminderOccurrence, ...]) -> list[tuple[str, datetime, date]]:
    return [(row.type, row.scheduled_at, row.occurrence) for row in plan]


def _by_type(plan: tuple[ReminderOccurrence, ...], type_: str) -> ReminderOccurrence:
    rows = [row for row in plan if row.type == type_]
    assert len(rows) == 1
    return rows[0]


def _pending_rows(plan: tuple[ReminderOccurrence, ...]) -> list[SimpleNamespace]:
    """A4.3-shaped rows for the pure cancellation rules (no persistence)."""
    return [
        SimpleNamespace(
            status="pending",
            type=row.type,
            task_id=TASK,
            due_version=row.due_version,
        )
        for row in plan
    ]


# --- The wall clock is preserved across the change --------------------------------------


def test_the_all_day_clock_stays_nine_local_across_the_spring_forward() -> None:
    plan = plan_task_reminders(
        _all_day(date(2026, 3, 11)), user_timezone=NEW_YORK, now=SPRING_NOW
    )

    # 2026-03-08 is the transition day: 09:00 already uses EDT (UTC-4).
    assert _rows(plan) == [
        ("reminder_t3d", datetime(2026, 3, 8, 13, 0, tzinfo=UTC), date(2026, 3, 8)),
        ("reminder_t1d", datetime(2026, 3, 10, 13, 0, tzinfo=UTC), date(2026, 3, 10)),
    ]


def test_the_utc_offset_moves_but_the_local_wall_clock_does_not() -> None:
    before = plan_task_reminders(
        _all_day(date(2026, 3, 8)), user_timezone=NEW_YORK, now=SPRING_NOW
    )
    after = plan_task_reminders(
        _all_day(date(2026, 3, 11)), user_timezone=NEW_YORK, now=SPRING_NOW
    )

    # Before the transition 09:00 is EST (14:00 UTC); after it, 09:00 is EDT (13:00 UTC).
    assert [row.scheduled_at for row in before] == [
        datetime(2026, 3, 5, 14, 0, tzinfo=UTC),
        datetime(2026, 3, 7, 14, 0, tzinfo=UTC),
    ]
    assert [row.scheduled_at for row in after] == [
        datetime(2026, 3, 8, 13, 0, tzinfo=UTC),
        datetime(2026, 3, 10, 13, 0, tzinfo=UTC),
    ]
    # Both plans keep the configured 09:00 wall clock in the user's zone.
    zone = ZoneInfo(NEW_YORK)
    for row in (*before, *after):
        assert row.scheduled_at.astimezone(zone).time() == time(9, 0)


def test_the_all_day_clock_stays_nine_local_across_the_fall_back() -> None:
    plan = plan_task_reminders(
        _all_day(date(2026, 11, 3)), user_timezone=NEW_YORK, now=FALL_NOW
    )

    # 2026-10-31 is still EDT (13:00 UTC); 2026-11-02 already uses EST (14:00 UTC).
    assert _rows(plan) == [
        ("reminder_t3d", datetime(2026, 10, 31, 13, 0, tzinfo=UTC), date(2026, 10, 31)),
        ("reminder_t1d", datetime(2026, 11, 2, 14, 0, tzinfo=UTC), date(2026, 11, 2)),
    ]


# --- Nonexistent hour (spring forward) --------------------------------------------------


def test_a_nonexistent_hour_moves_to_the_first_valid_instant() -> None:
    plan = plan_task_reminders(
        _all_day(date(2026, 3, 11)),
        user_timezone=NEW_YORK,
        now=SPRING_NOW,
        local_time=time(2, 30),
    )

    t3d = _by_type(plan, "reminder_t3d")
    # 02:30 does not exist on 2026-03-08; the gap ends at 03:00 EDT (07:00 UTC).
    assert t3d.scheduled_at == datetime(2026, 3, 8, 7, 0, tzinfo=UTC)
    assert t3d.occurrence == date(2026, 3, 8)
    assert _rows(plan) == [
        ("reminder_t3d", datetime(2026, 3, 8, 7, 0, tzinfo=UTC), date(2026, 3, 8)),
        ("reminder_t1d", datetime(2026, 3, 10, 6, 30, tzinfo=UTC), date(2026, 3, 10)),
    ]


def test_the_gap_start_moves_to_the_gap_end() -> None:
    plan = plan_task_reminders(
        _all_day(date(2026, 3, 11)),
        user_timezone=NEW_YORK,
        now=SPRING_NOW,
        local_time=time(2, 0),
    )

    assert _by_type(plan, "reminder_t3d").scheduled_at == datetime(
        2026, 3, 8, 7, 0, tzinfo=UTC
    )


def test_a_valid_minute_before_the_gap_is_not_moved() -> None:
    plan = plan_task_reminders(
        _all_day(date(2026, 3, 11)),
        user_timezone=NEW_YORK,
        now=SPRING_NOW,
        local_time=time(1, 59),
    )

    assert _by_type(plan, "reminder_t3d").scheduled_at == datetime(
        2026, 3, 8, 6, 59, tzinfo=UTC
    )


def test_a_valid_minute_after_the_gap_is_not_moved() -> None:
    plan = plan_task_reminders(
        _all_day(date(2026, 3, 11)),
        user_timezone=NEW_YORK,
        now=SPRING_NOW,
        local_time=time(3, 0),
    )

    assert _by_type(plan, "reminder_t3d").scheduled_at == datetime(
        2026, 3, 8, 7, 0, tzinfo=UTC
    )


def test_a_thirty_minute_gap_uses_its_own_gap_end() -> None:
    # Lord Howe moves 02:00 -> 02:30 (+11:00) on 2026-10-04.
    plan = plan_task_reminders(
        _all_day(date(2026, 10, 7)),
        user_timezone=LORD_HOWE,
        now=datetime(2026, 9, 28, 12, 0, tzinfo=UTC),
        local_time=time(2, 15),
    )

    t3d = _by_type(plan, "reminder_t3d")
    assert t3d.scheduled_at == datetime(2026, 10, 3, 15, 30, tzinfo=UTC)
    assert t3d.scheduled_at.astimezone(ZoneInfo(LORD_HOWE)).time() == time(2, 30)
    assert t3d.occurrence == date(2026, 10, 4)
    assert _by_type(plan, "reminder_t1d").scheduled_at == datetime(
        2026, 10, 5, 15, 15, tzinfo=UTC
    )


def test_a_midnight_gap_keeps_the_target_occurrence_day() -> None:
    # Santiago moves 00:00 -> 01:00 (-04:00 -> -03:00) on 2026-09-06.
    plan = plan_task_reminders(
        _all_day(date(2026, 9, 9)),
        user_timezone=SANTIAGO,
        now=datetime(2026, 8, 30, 12, 0, tzinfo=UTC),
        local_time=time(0, 30),
    )

    assert _rows(plan) == [
        ("reminder_t3d", datetime(2026, 9, 6, 4, 0, tzinfo=UTC), date(2026, 9, 6)),
        ("reminder_t1d", datetime(2026, 9, 8, 3, 30, tzinfo=UTC), date(2026, 9, 8)),
    ]


def test_a_nonexistent_hour_is_neither_dropped_nor_duplicated() -> None:
    plan = plan_task_reminders(
        _all_day(date(2026, 3, 11)),
        user_timezone=NEW_YORK,
        now=SPRING_NOW,
        local_time=time(2, 30),
    )

    assert len(plan) == 2
    assert [row.type for row in plan] == ["reminder_t3d", "reminder_t1d"]
    assert len({row.dedupe_key for row in plan}) == 2


# --- Repeated hour (fall back) ----------------------------------------------------------


def test_a_repeated_hour_arms_one_occurrence_at_the_first_fold() -> None:
    plan = plan_task_reminders(
        _all_day(date(2026, 11, 4)),
        user_timezone=NEW_YORK,
        now=FALL_NOW,
        local_time=time(1, 30),
    )

    t3d = _by_type(plan, "reminder_t3d")
    # 01:30 happens twice on 2026-11-01; the first fold is 01:30 EDT (05:30 UTC).
    assert t3d.scheduled_at == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    assert t3d.occurrence == date(2026, 11, 1)
    assert datetime(2026, 11, 1, 6, 30, tzinfo=UTC) not in [
        row.scheduled_at for row in plan
    ]
    assert len([row for row in plan if row.type == "reminder_t3d"]) == 1


def test_the_repeated_wall_clock_has_a_single_dedupe_identity() -> None:
    plan = plan_task_reminders(
        _all_day(date(2026, 11, 4)),
        user_timezone=NEW_YORK,
        now=FALL_NOW,
        local_time=time(1, 30),
    )

    t3d = _by_type(plan, "reminder_t3d")
    assert t3d.dedupe_key == dedupe_key(
        user_id=USER,
        type="reminder_t3d",
        task_id=TASK,
        due_version=1,
        occurrence=date(2026, 11, 1),
    )
    # The identity carries the local target day, never the instant: a second fold would
    # render the same key and the UNIQUE constraint would reject the duplicate row.
    assert "05:30" not in t3d.dedupe_key
    assert t3d.dedupe_key.endswith("|2026-11-01")


def test_replanning_after_the_fall_back_keeps_the_future_identity() -> None:
    task = _all_day(date(2026, 11, 4))
    before = plan_task_reminders(
        task, user_timezone=NEW_YORK, now=FALL_NOW, local_time=time(1, 30)
    )
    after = plan_task_reminders(
        task,
        user_timezone=NEW_YORK,
        now=datetime(2026, 11, 1, 7, 0, tzinfo=UTC),
        local_time=time(1, 30),
    )

    # The T-3d occurrence belonged to the repeated hour and is already past; the T-1d one
    # keeps its exact instant and key, so re-planning cannot insert a duplicate.
    assert [row.type for row in after] == ["reminder_t1d"]
    assert after[0] == _by_type(before, "reminder_t1d")


def test_the_same_generation_before_and_after_the_spring_forward_is_identical() -> None:
    task = _all_day(date(2026, 3, 11))
    before = plan_task_reminders(task, user_timezone=NEW_YORK, now=SPRING_NOW)
    after = plan_task_reminders(
        task,
        user_timezone=NEW_YORK,
        now=datetime(2026, 3, 8, 8, 0, tzinfo=UTC),
    )

    assert before == after
    assert [row.dedupe_key for row in before] == [row.dedupe_key for row in after]


def test_confirmation_at_the_normalised_instant_keeps_the_threshold() -> None:
    plan = plan_task_reminders(
        _all_day(date(2026, 3, 11)),
        user_timezone=NEW_YORK,
        now=datetime(2026, 3, 8, 7, 0, tzinfo=UTC),
        local_time=time(2, 30),
    )

    assert [row.type for row in plan] == ["reminder_t3d", "reminder_t1d"]


def test_confirmation_one_second_later_omits_the_normalised_threshold() -> None:
    plan = plan_task_reminders(
        _all_day(date(2026, 3, 11)),
        user_timezone=NEW_YORK,
        now=datetime(2026, 3, 8, 7, 0, 1, tzinfo=UTC),
        local_time=time(2, 30),
    )

    assert [row.type for row in plan] == ["reminder_t1d"]


# --- Timed reminders are absolute instants ----------------------------------------------


def test_a_timed_task_crossing_the_transition_keeps_its_absolute_instants() -> None:
    plan = plan_task_reminders(
        _timed(datetime(2026, 3, 8, 12, 0, tzinfo=UTC)),
        user_timezone=NEW_YORK,
        now=SPRING_NOW,
    )

    assert _rows(plan) == [
        ("reminder_t48", datetime(2026, 3, 6, 12, 0, tzinfo=UTC), date(2026, 3, 6)),
        ("reminder_t24", datetime(2026, 3, 7, 12, 0, tzinfo=UTC), date(2026, 3, 7)),
        ("reminder_t2", datetime(2026, 3, 8, 10, 0, tzinfo=UTC), date(2026, 3, 8)),
    ]


def test_a_timed_occurrence_before_the_fall_back_uses_its_local_day() -> None:
    plan = plan_task_reminders(
        _timed(datetime(2026, 11, 1, 5, 30, tzinfo=UTC)),
        user_timezone=NEW_YORK,
        now=FALL_NOW,
    )

    # 2026-11-01 03:30 UTC is still 2026-10-31 23:30 EDT: two thresholds share a local day.
    assert [row.occurrence for row in plan] == [
        date(2026, 10, 30),
        date(2026, 10, 31),
        date(2026, 10, 31),
    ]
    assert len({row.dedupe_key for row in plan}) == 3


def test_a_timed_plan_does_not_depend_on_the_planning_clock() -> None:
    task = _timed(datetime(2026, 3, 20, 12, 0, tzinfo=UTC))

    before = plan_task_reminders(task, user_timezone=NEW_YORK, now=SPRING_NOW)
    after = plan_task_reminders(
        task,
        user_timezone=NEW_YORK,
        now=datetime(2026, 3, 9, 12, 0, tzinfo=UTC),
    )

    assert before == after


# --- Persistent dedupe and A4.3 cancellation --------------------------------------------


@pytest.mark.parametrize("local_time", [time(9, 0), time(2, 30), time(1, 30)])
def test_every_dst_occurrence_matches_the_canonical_builder(local_time: time) -> None:
    plan = plan_task_reminders(
        _all_day(date(2026, 11, 4)),
        user_timezone=NEW_YORK,
        now=FALL_NOW,
        local_time=local_time,
    )

    for row in plan:
        assert row.dedupe_key == dedupe_key(
            user_id=USER,
            type=row.type,
            task_id=TASK,
            due_version=row.due_version,
            occurrence=row.occurrence,
        )
    assert len({row.dedupe_key for row in plan}) == len(plan)


def test_a_due_date_change_retires_the_dst_generation() -> None:
    now = FALL_NOW
    old_plan = plan_task_reminders(
        _all_day(date(2026, 11, 4)), user_timezone=NEW_YORK, now=now, local_time=time(1, 30)
    )
    new_plan = plan_task_reminders(
        _all_day(date(2026, 11, 5), due_version=2),
        user_timezone=NEW_YORK,
        now=now,
        local_time=time(1, 30),
    )

    obsolete = obsolete_pending(
        _pending_rows(old_plan), task_id=TASK, due_version=2
    )

    assert [row.type for row in obsolete] == ["reminder_t3d", "reminder_t1d"]
    assert {row.dedupe_key for row in old_plan}.isdisjoint(
        {row.dedupe_key for row in new_plan}
    )


def test_a_disabled_threshold_retires_its_dst_occurrence() -> None:
    plan = plan_task_reminders(
        _all_day(date(2026, 11, 4)), user_timezone=NEW_YORK, now=FALL_NOW
    )

    obsolete = obsolete_pending(
        _pending_rows(plan),
        task_id=TASK,
        enabled_types=frozenset({"reminder_t1d"}),
    )

    assert [row.type for row in obsolete] == ["reminder_t3d"]


def test_a_delivered_or_cancelled_dst_occurrence_is_not_selected() -> None:
    plan = plan_task_reminders(
        _all_day(date(2026, 11, 4)), user_timezone=NEW_YORK, now=FALL_NOW
    )
    rows = _pending_rows(plan)
    rows[0].status = "sent"
    rows[1].status = "cancelled"

    assert obsolete_pending(rows, task_id=TASK, due_version=2) == ()


def test_dst_occurrences_carry_the_task_generation() -> None:
    plan = plan_task_reminders(
        _all_day(date(2026, 11, 4), due_version=7),
        user_timezone=NEW_YORK,
        now=FALL_NOW,
    )

    assert {row.due_version for row in plan} == {7}
    assert all(row.dedupe_key.split("|")[3] == "7" for row in plan)


def test_an_invalid_local_clock_still_fails_before_any_plan_is_built() -> None:
    with pytest.raises(ValueError):
        plan_task_reminders(
            _all_day(date(2026, 3, 11)),
            user_timezone=NEW_YORK,
            now=SPRING_NOW,
            local_time=time(2, 30, 30),
        )


def test_the_planning_clock_must_still_be_timezone_aware() -> None:
    with pytest.raises(ValueError):
        plan_task_reminders(
            _all_day(date(2026, 3, 11)),
            user_timezone=NEW_YORK,
            now=datetime(2026, 3, 1, 12, 0),
        )

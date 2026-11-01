"""U-S4-JF-01 (J4.3): escalating reminders for confirmed tasks (M8 §1.2).

The pure planner is exercised without PostgreSQL or Gmail. The contract under test:

- a timed task (`due_at`) arms T-48 h, T-24 h and T-2 h before the instant;
- an all-day task (`due_date`) arms T-3 d and T-1 d at the user's local wall clock;
- a task without a resolved date has no reminders, and neither has a task that is not
  `confirmed`;
- thresholds already past at confirmation are omitted, so a late confirmation cannot send a
  retrospective burst;
- every occurrence carries the exact canonical `dedupe_key` of its generation.

No provider, credential, audio or secret is involved.
"""

import uuid
from datetime import UTC, date, datetime, time, timedelta, timezone
from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.services.notifications import dedupe_key, scheduled_reminder_types
from app.services.reminders import (
    DEFAULT_REMINDER_LOCAL_TIME,
    DEFAULT_REMINDER_PREFERENCES,
    ReminderPreferences,
    ReminderScheduleError,
    ReminderTask,
    plan_task_reminders,
)

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
USER = uuid.UUID("22222222-2222-4222-8222-222222222222")
TASK = uuid.UUID("11111111-1111-4111-8111-111111111111")
BOGOTA = "America/Bogota"
TOKYO = "Asia/Tokyo"
TIMED_DUE = datetime(2026, 11, 10, 18, 0, tzinfo=UTC)
ALL_DAY_DUE = date(2026, 11, 10)


def _task(**overrides: Any) -> ReminderTask:
    values: dict[str, Any] = {
        "id": TASK,
        "user_id": USER,
        "status": "confirmed",
        "all_day": False,
        "date_status": "resolved",
        "due_date": None,
        "due_at": TIMED_DUE,
        "due_version": 1,
    }
    values.update(overrides)
    return ReminderTask(**values)


def _all_day(**overrides: Any) -> ReminderTask:
    values: dict[str, Any] = {
        "all_day": True,
        "due_date": ALL_DAY_DUE,
        "due_at": None,
    }
    values.update(overrides)
    return _task(**values)


def _rows(plan: tuple[Any, ...]) -> list[tuple[str, datetime, date]]:
    return [(row.type, row.scheduled_at, row.occurrence) for row in plan]


def _error_code(caught: pytest.ExceptionInfo[ReminderScheduleError]) -> str:
    return caught.value.code


# --- Timed instants --------------------------------------------------------------------


def test_a_timed_task_arms_the_three_expiring_instants() -> None:
    plan = plan_task_reminders(_task(), user_timezone=BOGOTA, now=NOW)

    assert _rows(plan) == [
        ("reminder_t48", datetime(2026, 11, 8, 18, 0, tzinfo=UTC), date(2026, 11, 8)),
        ("reminder_t24", datetime(2026, 11, 9, 18, 0, tzinfo=UTC), date(2026, 11, 9)),
        ("reminder_t2", datetime(2026, 11, 10, 16, 0, tzinfo=UTC), date(2026, 11, 10)),
    ]
    assert [row.due_version for row in plan] == [1, 1, 1]


def test_every_timed_occurrence_carries_the_canonical_dedupe_key() -> None:
    plan = plan_task_reminders(_task(), user_timezone=BOGOTA, now=NOW)

    for row in plan:
        assert row.dedupe_key == dedupe_key(
            user_id=USER,
            type=row.type,
            task_id=TASK,
            due_version=1,
            occurrence=row.occurrence,
        )
    assert len({row.dedupe_key for row in plan}) == len(plan)
    assert plan[0].dedupe_key == f"{USER}|reminder_t48|{TASK}|1|2026-11-08"


def test_the_timed_instant_is_exact_and_independent_of_the_local_day() -> None:
    # 2026-11-10 02:00 UTC is still 2026-11-09 in Bogotá. The threshold moves the instant,
    # not the wall clock of the due date, and `occurrence` is the local day of delivery.
    due = datetime(2026, 11, 10, 2, 0, tzinfo=UTC)
    plan = plan_task_reminders(_task(due_at=due), user_timezone=BOGOTA, now=NOW)

    assert _rows(plan) == [
        ("reminder_t48", datetime(2026, 11, 8, 2, 0, tzinfo=UTC), date(2026, 11, 7)),
        ("reminder_t24", datetime(2026, 11, 9, 2, 0, tzinfo=UTC), date(2026, 11, 8)),
        ("reminder_t2", datetime(2026, 11, 10, 0, 0, tzinfo=UTC), date(2026, 11, 9)),
    ]


def test_a_non_utc_due_instant_is_normalised_before_subtracting_the_threshold() -> None:
    due = datetime(2026, 11, 10, 13, 0, tzinfo=timezone(timedelta(hours=-5)))
    plan = plan_task_reminders(_task(due_at=due), user_timezone=BOGOTA, now=NOW)

    assert [row.scheduled_at for row in plan] == [
        datetime(2026, 11, 8, 18, 0, tzinfo=UTC),
        datetime(2026, 11, 9, 18, 0, tzinfo=UTC),
        datetime(2026, 11, 10, 16, 0, tzinfo=UTC),
    ]


# --- All-day dates ---------------------------------------------------------------------


def test_an_all_day_task_arms_the_two_thresholds_at_the_local_clock() -> None:
    plan = plan_task_reminders(_all_day(), user_timezone=BOGOTA, now=NOW)

    assert _rows(plan) == [
        ("reminder_t3d", datetime(2026, 11, 7, 14, 0, tzinfo=UTC), date(2026, 11, 7)),
        ("reminder_t1d", datetime(2026, 11, 9, 14, 0, tzinfo=UTC), date(2026, 11, 9)),
    ]
    assert [row.dedupe_key for row in plan] == [
        f"{USER}|reminder_t3d|{TASK}|1|2026-11-07",
        f"{USER}|reminder_t1d|{TASK}|1|2026-11-09",
    ]


def test_the_all_day_wall_clock_follows_the_user_zone_not_utc() -> None:
    plan = plan_task_reminders(_all_day(), user_timezone=TOKYO, now=NOW)

    # 09:00 in Tokyo is 00:00 UTC; the occurrence stays the local target day.
    assert _rows(plan) == [
        ("reminder_t3d", datetime(2026, 11, 7, 0, 0, tzinfo=UTC), date(2026, 11, 7)),
        ("reminder_t1d", datetime(2026, 11, 9, 0, 0, tzinfo=UTC), date(2026, 11, 9)),
    ]


def test_the_default_local_time_is_nine_in_the_morning() -> None:
    assert DEFAULT_REMINDER_LOCAL_TIME == time(9, 0)


def test_an_explicit_local_time_moves_the_all_day_instants() -> None:
    plan = plan_task_reminders(
        _all_day(),
        user_timezone=BOGOTA,
        now=NOW,
        local_time=time(18, 30),
    )

    assert [row.scheduled_at for row in plan] == [
        datetime(2026, 11, 7, 23, 30, tzinfo=UTC),
        datetime(2026, 11, 9, 23, 30, tzinfo=UTC),
    ]


# --- No-date and non-confirmed tasks ---------------------------------------------------


@pytest.mark.parametrize("date_status", ["missing", "ambiguous"])
def test_a_task_without_a_date_never_gets_reminders(date_status: str) -> None:
    task = _task(date_status=date_status, due_date=None, due_at=None)

    assert plan_task_reminders(task, user_timezone=BOGOTA, now=NOW) == ()


@pytest.mark.parametrize("status", ["pending", "needs_review", "completed", "dismissed"])
def test_only_a_confirmed_task_is_schedulable(status: str) -> None:
    assert plan_task_reminders(
        _task(status=status), user_timezone=BOGOTA, now=NOW
    ) == ()
    assert plan_task_reminders(
        _all_day(status=status), user_timezone=BOGOTA, now=NOW
    ) == ()


@pytest.mark.parametrize(
    "task",
    [
        _task(date_status="resolved", due_at=None, due_date=None),
        _task(all_day=True, due_at=TIMED_DUE, due_date=ALL_DAY_DUE),
        _all_day(due_date=None, due_at=TIMED_DUE),
    ],
)
def test_an_incoherent_resolved_date_fails_closed(task: ReminderTask) -> None:
    # The DB CHECK forbids these rows; the planner must not invent midnight or an instant.
    assert plan_task_reminders(task, user_timezone=BOGOTA, now=NOW) == ()


# --- Late confirmation -----------------------------------------------------------------


def test_confirming_late_omits_every_threshold_already_past() -> None:
    now = datetime(2026, 11, 9, 19, 0, tzinfo=UTC)

    plan = plan_task_reminders(_task(), user_timezone=BOGOTA, now=now)

    assert _rows(plan) == [
        ("reminder_t2", datetime(2026, 11, 10, 16, 0, tzinfo=UTC), date(2026, 11, 10)),
    ]


def test_a_threshold_due_at_the_confirmation_instant_is_kept() -> None:
    # Exactly T-24 is on time, not retrospective: the minute tick can still deliver it.
    now = datetime(2026, 11, 9, 18, 0, tzinfo=UTC)

    plan = plan_task_reminders(_task(), user_timezone=BOGOTA, now=now)

    assert [row.type for row in plan] == ["reminder_t24", "reminder_t2"]


def test_a_task_whose_due_instant_already_passed_arms_nothing() -> None:
    plan = plan_task_reminders(
        _task(due_at=NOW - timedelta(days=1)), user_timezone=BOGOTA, now=NOW
    )

    assert plan == ()


def test_confirming_late_omits_past_all_day_thresholds_too() -> None:
    now = datetime(2026, 11, 8, 15, 0, tzinfo=UTC)

    plan = plan_task_reminders(_all_day(), user_timezone=BOGOTA, now=now)

    assert _rows(plan) == [
        ("reminder_t1d", datetime(2026, 11, 9, 14, 0, tzinfo=UTC), date(2026, 11, 9)),
    ]


# --- Preferences -----------------------------------------------------------------------


def test_the_frozen_defaults_are_the_contract_arrays() -> None:
    assert DEFAULT_REMINDER_PREFERENCES == ReminderPreferences(
        reminder_offsets_hours=(48, 24, 2),
        reminder_allday_offsets_days=(3, 1),
    )


def test_only_the_enabled_thresholds_are_armed() -> None:
    preferences = ReminderPreferences(
        reminder_offsets_hours=(24,), reminder_allday_offsets_days=(1,)
    )

    timed = plan_task_reminders(
        _task(), user_timezone=BOGOTA, now=NOW, preferences=preferences
    )
    allday = plan_task_reminders(
        _all_day(), user_timezone=BOGOTA, now=NOW, preferences=preferences
    )

    assert [row.type for row in timed] == ["reminder_t24"]
    assert [row.type for row in allday] == ["reminder_t1d"]


def test_an_offset_without_a_frozen_type_arms_nothing() -> None:
    preferences = ReminderPreferences(
        reminder_offsets_hours=(12,), reminder_allday_offsets_days=(2,)
    )

    assert plan_task_reminders(
        _task(), user_timezone=BOGOTA, now=NOW, preferences=preferences
    ) == ()
    assert plan_task_reminders(
        _all_day(), user_timezone=BOGOTA, now=NOW, preferences=preferences
    ) == ()


def test_an_empty_preference_arms_no_reminder_of_that_family() -> None:
    preferences = ReminderPreferences(
        reminder_offsets_hours=(), reminder_allday_offsets_days=()
    )

    assert plan_task_reminders(
        _task(), user_timezone=BOGOTA, now=NOW, preferences=preferences
    ) == ()


def test_a_repeated_offset_does_not_duplicate_the_occurrence() -> None:
    preferences = ReminderPreferences(reminder_offsets_hours=(48, 48))

    plan = plan_task_reminders(
        _task(), user_timezone=BOGOTA, now=NOW, preferences=preferences
    )

    assert [row.type for row in plan] == ["reminder_t48"]


@pytest.mark.parametrize(
    "preferences",
    [
        ReminderPreferences(reminder_offsets_hours=cast(tuple[int, ...], ("48",))),
        ReminderPreferences(reminder_offsets_hours=(True,)),
        ReminderPreferences(reminder_offsets_hours=(-1,)),
        ReminderPreferences(
            reminder_allday_offsets_days=cast(tuple[int, ...], ("1",))
        ),
    ],
)
def test_malformed_thresholds_are_rejected_and_arm_nothing(
    preferences: ReminderPreferences,
) -> None:
    with pytest.raises(ReminderScheduleError) as caught:
        plan_task_reminders(
            _task(), user_timezone=BOGOTA, now=NOW, preferences=preferences
        )

    assert _error_code(caught) == "preference_invalid"


def test_the_shared_preference_mapping_is_ordered_and_drops_unknown_offsets() -> None:
    assert scheduled_reminder_types(
        reminder_offsets_hours=(2, 48, 12), reminder_allday_offsets_days=(1, 2)
    ) == ("reminder_t48", "reminder_t2", "reminder_t1d")


# --- Input validation ------------------------------------------------------------------


def test_a_naive_instant_cannot_be_scheduled() -> None:
    with pytest.raises(ReminderScheduleError) as caught:
        plan_task_reminders(
            _task(due_at=datetime(2026, 11, 10, 18, 0)),
            user_timezone=BOGOTA,
            now=NOW,
        )

    assert _error_code(caught) == "due_invalid"


def test_a_naive_clock_is_rejected() -> None:
    with pytest.raises(ReminderScheduleError) as caught:
        plan_task_reminders(
            _task(), user_timezone=BOGOTA, now=datetime(2026, 11, 2, 12, 0)
        )

    assert _error_code(caught) == "clock_invalid"


@pytest.mark.parametrize("zone", ["", "   ", "Mars/Olympus_Mons"])
def test_an_invalid_user_timezone_is_rejected(zone: str) -> None:
    with pytest.raises(ReminderScheduleError) as caught:
        plan_task_reminders(_all_day(), user_timezone=zone, now=NOW)

    assert _error_code(caught) == "timezone_invalid"


@pytest.mark.parametrize(
    "local_time",
    [time(9, 0, tzinfo=UTC), time(9, 0, 1), time(9, 0, 0, 1)],
)
def test_the_all_day_clock_must_be_a_local_minute(local_time: time) -> None:
    with pytest.raises(ReminderScheduleError) as caught:
        plan_task_reminders(
            _all_day(), user_timezone=BOGOTA, now=NOW, local_time=local_time
        )

    assert _error_code(caught) == "local_time_invalid"


def test_a_non_positive_due_version_is_rejected() -> None:
    with pytest.raises(ReminderScheduleError) as caught:
        plan_task_reminders(
            _task(due_version=0), user_timezone=BOGOTA, now=NOW
        )

    assert _error_code(caught) == "due_version_invalid"


def test_the_task_view_is_built_from_the_orm_shaped_row() -> None:
    row = SimpleNamespace(
        id=TASK,
        user_id=USER,
        status="confirmed",
        all_day=False,
        date_status="resolved",
        due_date=None,
        due_at=TIMED_DUE,
        due_version=3,
        titulo="ignored",
    )

    view = ReminderTask.from_task(row)

    assert view == ReminderTask(
        id=TASK,
        user_id=USER,
        status="confirmed",
        all_day=False,
        date_status="resolved",
        due_date=None,
        due_at=TIMED_DUE,
        due_version=3,
    )

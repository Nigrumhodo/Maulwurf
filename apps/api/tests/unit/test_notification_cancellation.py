"""U-S4-AN-03 (A4.3): changing the due date or preferences cancels only stale occurrences.

The pure cancellation core is exercised without PostgreSQL. The invariant is:

- only `pending` occurrences can become `cancelled`; a `sending` row may already be leaving
  through Gmail and `sent`/`failed`/`delivery_unknown` are delivery history;
- a new `due_version` retires the pending reminders of the previous generation of **that**
  task, and nothing else;
- a preference change retires the pending occurrences whose type is no longer enabled,
  including the digest types; an explicit `test` send is not a preference-driven occurrence;
- an obsolete occurrence is cancelled even when its scheduled instant already passed,
  because sending it late is exactly the retrospective burst M8 forbids.

No provider, credential or secret is involved.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from app.models.notification import DIGEST_TYPES, REMINDER_TYPES
from app.services.notifications import (
    ALLDAY_REMINDER_OFFSETS,
    PREFERENCE_NOTIFICATION_TYPES,
    TIMED_REMINDER_OFFSETS,
    cancel_obsolete,
    enabled_preference_types,
    obsolete_pending,
)

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
TASK = uuid.UUID("33333333-3333-4333-8333-333333333333")
OTHER_TASK = uuid.UUID("44444444-4444-4444-8444-444444444444")
OCCURRENCE = date(2026, 11, 9)
DEFAULT_OFFSETS_HOURS = (48, 24, 2)
DEFAULT_OFFSETS_DAYS = (3, 1)


def _row(status: str = "pending", **overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "status": status,
        "type": "reminder_t24",
        "task_id": TASK,
        "due_version": 1,
        "occurrence": OCCURRENCE,
        "scheduled_at": NOW + timedelta(hours=24),
        "attempts": 0,
        "lease_expires_at": None,
        "sent_at": None,
        "provider_message_id": None,
        "last_error_code": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _enabled(**overrides: Any) -> frozenset[str]:
    values: dict[str, Any] = {
        "reminder_offsets_hours": DEFAULT_OFFSETS_HOURS,
        "reminder_allday_offsets_days": DEFAULT_OFFSETS_DAYS,
        "digest_daily_enabled": True,
        "digest_weekly_enabled": True,
    }
    values.update(overrides)
    return enabled_preference_types(**values)


def _statuses(rows: list[SimpleNamespace]) -> list[str]:
    return [str(row.status) for row in rows]


def _digest(row_type: str = "digest_daily", **overrides: Any) -> SimpleNamespace:
    return _row(type=row_type, task_id=None, due_version=None, **overrides)


# --- Frozen preference vocabulary -------------------------------------------------------


def test_the_offset_maps_cover_every_reminder_type_exactly_once() -> None:
    timed = set(TIMED_REMINDER_OFFSETS)
    allday = set(ALLDAY_REMINDER_OFFSETS)
    assert timed | allday == set(REMINDER_TYPES)
    assert timed & allday == set()
    assert PREFERENCE_NOTIFICATION_TYPES == set(REMINDER_TYPES) | set(DIGEST_TYPES)
    assert "test" not in PREFERENCE_NOTIFICATION_TYPES


def test_the_defaults_enable_every_preference_driven_type() -> None:
    assert _enabled() == set(REMINDER_TYPES) | set(DIGEST_TYPES)


def test_only_the_offsets_present_in_the_preferences_stay_enabled() -> None:
    enabled = enabled_preference_types(
        reminder_offsets_hours=[48, 24],
        reminder_allday_offsets_days=[3],
        digest_daily_enabled=False,
        digest_weekly_enabled=True,
    )
    assert enabled == {
        "reminder_t48",
        "reminder_t24",
        "reminder_t3d",
        "digest_weekly",
    }


def test_an_empty_preference_disables_that_family() -> None:
    enabled = enabled_preference_types(
        reminder_offsets_hours=[],
        reminder_allday_offsets_days=[],
        digest_daily_enabled=False,
        digest_weekly_enabled=False,
    )
    assert enabled == frozenset()


def test_an_offset_without_a_frozen_type_never_invents_one() -> None:
    enabled = enabled_preference_types(
        reminder_offsets_hours=[12, 48],
        reminder_allday_offsets_days=[7],
        digest_daily_enabled=False,
        digest_weekly_enabled=False,
    )
    assert enabled == {"reminder_t48"}


@pytest.mark.parametrize(
    "value",
    [
        "48",
        ["48"],
        [True],
        [1.5],
        [-1],
        None,
    ],
)
def test_a_malformed_preference_fails_closed_instead_of_cancelling_everything(
    value: Any,
) -> None:
    with pytest.raises(ValueError):
        enabled_preference_types(
            reminder_offsets_hours=value,
            reminder_allday_offsets_days=(3, 1),
            digest_daily_enabled=True,
            digest_weekly_enabled=True,
        )


# --- Due-date generation ---------------------------------------------------------------


def test_a_new_due_generation_cancels_only_the_older_pending_reminders() -> None:
    old_pending = _row(due_version=1)
    old_sent = _row(
        "sent",
        due_version=1,
        sent_at=NOW - timedelta(days=1),
        provider_message_id="gmail-msg-1",
    )
    current_pending = _row(due_version=2)
    other_task = _row(task_id=OTHER_TASK, due_version=1)

    rows = [old_pending, old_sent, current_pending, other_task]
    cancelled = cancel_obsolete(rows, task_id=TASK, due_version=2)

    assert cancelled == (old_pending,)
    assert _statuses(rows) == ["cancelled", "sent", "pending", "pending"]


@pytest.mark.parametrize(
    "status",
    ["sending", "sent", "failed", "delivery_unknown", "cancelled"],
)
def test_a_generation_change_never_rewrites_a_non_pending_occurrence(status: str) -> None:
    row = _row(status, due_version=1)
    before = dict(vars(row))

    assert cancel_obsolete([row], task_id=TASK, due_version=2) == ()
    assert vars(row) == before


def test_a_generation_rule_never_cancels_a_task_less_digest() -> None:
    digest = _digest()
    assert obsolete_pending([digest], task_id=TASK, due_version=2) == ()
    assert digest.status == "pending"


def test_a_generation_rule_never_reaches_another_task() -> None:
    # The same due_version value is per task: checking one task must not sweep another.
    other = _row(task_id=OTHER_TASK, due_version=1)
    own = _row(task_id=TASK, due_version=1)

    assert cancel_obsolete([other, own], task_id=TASK, due_version=2) == (own,)
    assert (other.status, own.status) == ("pending", "cancelled")


# --- Preferences -----------------------------------------------------------------------


def test_disabling_a_threshold_cancels_only_its_pending_occurrences() -> None:
    t48 = _row(type="reminder_t48")
    t24 = _row(type="reminder_t24")
    t2 = _row(type="reminder_t2")

    cancelled = cancel_obsolete(
        [t48, t24, t2],
        enabled_types=_enabled(reminder_offsets_hours=[48, 24]),
    )

    assert cancelled == (t2,)
    assert _statuses([t48, t24, t2]) == ["pending", "pending", "cancelled"]


def test_disabling_the_all_day_offsets_cancels_that_family_only() -> None:
    t3d = _row(type="reminder_t3d")
    t1d = _row(type="reminder_t1d")
    t24 = _row(type="reminder_t24")

    cancelled = cancel_obsolete(
        [t3d, t1d, t24],
        enabled_types=_enabled(reminder_allday_offsets_days=[]),
    )

    assert cancelled == (t3d, t1d)
    assert _statuses([t3d, t1d, t24]) == ["cancelled", "cancelled", "pending"]


def test_disabling_one_digest_keeps_the_other_and_never_touches_a_test_send() -> None:
    daily = _digest("digest_daily")
    weekly = _digest("digest_weekly")
    test_send = _digest("test", occurrence=OCCURRENCE)

    cancelled = cancel_obsolete(
        [daily, weekly, test_send],
        enabled_types=_enabled(digest_weekly_enabled=False),
    )

    assert cancelled == (weekly,)
    assert _statuses([daily, weekly, test_send]) == ["pending", "cancelled", "pending"]


def test_a_preference_change_never_rewrites_a_delivered_occurrence() -> None:
    sent = _row(
        "sent",
        sent_at=NOW - timedelta(hours=1),
        provider_message_id="gmail-msg-2",
    )
    in_flight = _row(
        "sending",
        lease_expires_at=NOW + timedelta(minutes=5),
        attempts=1,
    )

    cancelled = cancel_obsolete(
        [sent, in_flight],
        enabled_types=frozenset(),
    )

    assert cancelled == ()
    assert _statuses([sent, in_flight]) == ["sent", "sending"]


def test_a_combined_task_scope_and_preference_change_still_cancels_user_digests() -> None:
    daily = _digest("digest_daily")
    reminder = _row(type="reminder_t48")
    other_task_reminder = _row(task_id=OTHER_TASK, type="reminder_t48")

    cancelled = cancel_obsolete(
        [daily, reminder, other_task_reminder],
        task_id=TASK,
        enabled_types=frozenset(),
    )

    # The task scope belongs to the due-date rule; a preference change remains user-level,
    # so the disabled digest is cancelled while the other task's reminder is untouched.
    assert cancelled == (daily, reminder)
    assert _statuses([daily, reminder, other_task_reminder]) == [
        "cancelled",
        "cancelled",
        "pending",
    ]


# --- No retrospective burst ------------------------------------------------------------


def test_an_obsolete_occurrence_past_its_instant_is_cancelled_instead_of_bursting() -> None:
    stale = _row(due_version=1, scheduled_at=NOW - timedelta(hours=72))
    disabled = _row(type="reminder_t48", scheduled_at=NOW - timedelta(hours=1))

    cancelled = cancel_obsolete(
        [stale, disabled],
        task_id=TASK,
        due_version=2,
        enabled_types=_enabled(reminder_offsets_hours=[24, 2]),
    )

    assert cancelled == (stale, disabled)
    assert _statuses([stale, disabled]) == ["cancelled", "cancelled"]


def test_a_current_occurrence_that_is_already_due_is_left_to_the_scheduler() -> None:
    # A valid due occurrence is not stale: it must remain claimable so the normal reminder
    # can be delivered (the cancellation is not a second scheduler).
    due = _row(due_version=2, scheduled_at=NOW - timedelta(minutes=1))

    assert cancel_obsolete([due], task_id=TASK, due_version=2) == ()
    assert due.status == "pending"


# --- Idempotency and guards ------------------------------------------------------------


def test_cancelling_twice_returns_nothing_the_second_time() -> None:
    row = _row(due_version=1)

    assert cancel_obsolete([row], task_id=TASK, due_version=2) == (row,)
    assert row.status == "cancelled"
    assert cancel_obsolete([row], task_id=TASK, due_version=2) == ()
    assert row.status == "cancelled"


def test_cancelling_keeps_the_scheduling_and_delivery_fields_of_the_row() -> None:
    row = _row(due_version=1, attempts=0)
    before = dict(vars(row))

    cancel_obsolete([row], task_id=TASK, due_version=2)

    assert row.status == "cancelled"
    assert {key: value for key, value in vars(row).items() if key != "status"} == {
        key: value for key, value in before.items() if key != "status"
    }


def test_an_empty_rule_set_is_rejected_instead_of_cancelling_everything() -> None:
    rows = [_row(due_version=1), _digest()]

    with pytest.raises(ValueError):
        obsolete_pending(rows)
    with pytest.raises(ValueError):
        cancel_obsolete(rows)
    assert _statuses(rows) == ["pending", "pending"]


def test_a_generation_rule_requires_its_task_and_a_positive_version() -> None:
    with pytest.raises(ValueError):
        obsolete_pending([_row()], due_version=2)
    with pytest.raises(ValueError):
        obsolete_pending([_row()], task_id=TASK, due_version=0)
    with pytest.raises(ValueError):
        obsolete_pending([_row()], task_id=TASK, due_version=-1)

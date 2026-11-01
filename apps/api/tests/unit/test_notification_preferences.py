"""A4.2: M8 notification preferences are a partial, validated update.

The preferences live on the single per-user `settings` row extended by A4.1. This suite
pins the request/response contract the settings screen consumes (D4.1): every field is
optional on `PATCH /me/notifications`, the response is the complete preference set, the
daily digest time keeps minute precision, and the reminder thresholds are stored in a
canonical order so two equivalent payloads cannot produce two different schedules.

No provider is called and no mail is sent from these values.
"""

from datetime import time

import pytest
from pydantic import ValidationError

from app.schemas.notifications import (
    MAX_OFFSET_DAYS,
    MAX_OFFSET_HOURS,
    MAX_THRESHOLDS,
    NotificationPreferencesOut,
    NotificationPreferencesPatch,
    format_digest_time,
)


def test_a_partial_update_only_carries_the_fields_the_user_changed() -> None:
    patch = NotificationPreferencesPatch(gmail_opt_in=True)

    assert patch.model_dump(exclude_unset=True) == {"gmail_opt_in": True}
    assert patch.digest_daily_time is None
    assert patch.reminder_offsets_hours is None


def test_an_empty_update_is_valid_and_changes_nothing() -> None:
    assert NotificationPreferencesPatch().model_dump(exclude_unset=True) == {}


def test_the_defaults_match_the_m8_contract() -> None:
    out = NotificationPreferencesOut(
        gmail_opt_in=False,
        digest_daily_enabled=False,
        digest_daily_time="07:00",
        digest_weekly_enabled=False,
        digest_weekday=0,
        reminder_offsets_hours=[48, 24, 2],
        reminder_allday_offsets_days=[3, 1],
        updated_at=None,
    )

    assert out.digest_daily_time == "07:00"
    assert out.reminder_offsets_hours == [48, 24, 2]
    assert out.reminder_allday_offsets_days == [3, 1]


def test_the_digest_time_keeps_minute_precision() -> None:
    patch = NotificationPreferencesPatch(digest_daily_time="07:30")

    assert patch.digest_daily_time == time(7, 30)
    assert format_digest_time(time(7, 30)) == "07:30"
    assert format_digest_time(time(0, 5)) == "00:05"


def test_a_digest_time_with_seconds_or_fractions_is_rejected() -> None:
    with pytest.raises(ValidationError):
        NotificationPreferencesPatch(digest_daily_time="07:30:15")
    with pytest.raises(ValidationError):
        NotificationPreferencesPatch(digest_daily_time="07:30:00.500")


@pytest.mark.parametrize("weekday", [-1, 7, 8])
def test_the_weekly_digest_day_is_the_closed_zero_to_six_range(weekday: int) -> None:
    with pytest.raises(ValidationError):
        NotificationPreferencesPatch(digest_weekday=weekday)


def test_the_reminder_thresholds_are_normalized_and_deduplicated() -> None:
    patch = NotificationPreferencesPatch(
        reminder_offsets_hours=[2, 48, 2, 24],
        reminder_allday_offsets_days=[1, 3, 1],
    )

    assert patch.reminder_offsets_hours == [48, 24, 2]
    assert patch.reminder_allday_offsets_days == [3, 1]


def test_an_empty_threshold_list_disables_that_kind_of_reminder() -> None:
    patch = NotificationPreferencesPatch(reminder_offsets_hours=[])

    assert patch.reminder_offsets_hours == []


@pytest.mark.parametrize("value", [0, -1, MAX_OFFSET_HOURS + 1])
def test_hour_thresholds_reject_zero_negative_and_absurd_offsets(value: int) -> None:
    with pytest.raises(ValidationError):
        NotificationPreferencesPatch(reminder_offsets_hours=[value])


@pytest.mark.parametrize("value", [0, -1, MAX_OFFSET_DAYS + 1])
def test_day_thresholds_reject_zero_negative_and_absurd_offsets(value: int) -> None:
    with pytest.raises(ValidationError):
        NotificationPreferencesPatch(reminder_allday_offsets_days=[value])


def test_the_threshold_lists_are_bounded() -> None:
    with pytest.raises(ValidationError):
        NotificationPreferencesPatch(
            reminder_offsets_hours=list(range(1, MAX_THRESHOLDS + 2))
        )


def test_unknown_preference_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        NotificationPreferencesPatch(gmail_send=True)  # type: ignore[call-arg]


def test_an_explicit_null_is_not_a_way_to_erase_a_preference() -> None:
    with pytest.raises(ValidationError):
        NotificationPreferencesPatch(gmail_opt_in=None)
    with pytest.raises(ValidationError):
        NotificationPreferencesPatch(reminder_offsets_hours=None)

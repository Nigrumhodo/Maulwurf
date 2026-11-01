"""J4.6: the pure daily/weekly digest planner (M8 §1.4).

The planner is exercised without PostgreSQL, Gmail or a calendar. The contract under test:

- a daily digest is armed at ``digest_daily_time`` (07:00 by default) in the user's IANA
  zone, using the local date and either today or tomorrow depending on the clock;
- a weekly digest is armed on ``digest_weekday`` (0 = Sunday, the default) and its identity
  is the Sunday that starts the local week, so a weekday change inside the same week keeps
  the same dedupe key;
- the canonical ``dedupe_key`` includes the local period, making a second send per user and
  period impossible by construction;
- DST follows J4.4: a nonexistent wall clock moves to the first valid instant and a repeated
  one is armed once;
- the Gmail opt-in gate and both preference switches are respected.

No provider, credential, audio or secret is involved.
"""

import uuid
from datetime import UTC, date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from app.services.digests import (
    DAILY_WINDOW_DAYS,
    DEFAULT_DIGEST_LOCAL_TIME,
    DEFAULT_DIGEST_WEEKDAY,
    DigestPreferences,
    DigestScheduleError,
    digest_window,
    plan_digest_occurrences,
)
from app.services.notifications import dedupe_key

USER = uuid.UUID("33333333-3333-4333-8333-333333333333")
BOGOTA = "America/Bogota"
TOKYO = "Asia/Tokyo"
NEW_YORK = "America/New_York"


def _preferences(**overrides: Any) -> DigestPreferences:
    values: dict[str, Any] = {
        "gmail_opt_in": True,
        "digest_daily_enabled": True,
        "digest_daily_time": DEFAULT_DIGEST_LOCAL_TIME,
        "digest_weekly_enabled": False,
        "digest_weekday": DEFAULT_DIGEST_WEEKDAY,
    }
    values.update(overrides)
    return DigestPreferences(**values)


def _plan(
    now: datetime, *, timezone: str = BOGOTA, **overrides: Any
) -> tuple[Any, ...]:
    return plan_digest_occurrences(
        _preferences(**overrides), user_id=USER, user_timezone=timezone, now=now
    )


def test_the_frozen_defaults_are_0700_and_sunday() -> None:
    assert DEFAULT_DIGEST_LOCAL_TIME == time(7, 0)
    assert DEFAULT_DIGEST_WEEKDAY == 0


def test_a_daily_digest_uses_the_users_local_date_and_default_hour() -> None:
    # 10:00 UTC is 05:00 in Bogota (UTC-5), still the same local day.
    (occurrence,) = _plan(datetime(2026, 11, 1, 10, 0, tzinfo=UTC))

    assert occurrence.type == "digest_daily"
    assert occurrence.occurrence == date(2026, 11, 1)
    assert occurrence.scheduled_at == datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    assert occurrence.dedupe_key == f"{USER}|digest_daily|-|-|2026-11-01"
    assert occurrence.dedupe_key == dedupe_key(
        user_id=USER,
        type="digest_daily",
        task_id=None,
        due_version=None,
        occurrence=date(2026, 11, 1),
    )


def test_the_local_date_crosses_the_utc_boundary() -> None:
    # 20:00 UTC on Nov 1 is already 05:00 on Nov 2 in Tokyo (UTC+9).
    (occurrence,) = _plan(datetime(2026, 11, 1, 20, 0, tzinfo=UTC), timezone=TOKYO)

    assert occurrence.occurrence == date(2026, 11, 2)
    assert occurrence.scheduled_at == datetime(2026, 11, 1, 22, 0, tzinfo=UTC)


def test_the_daily_hour_is_configurable() -> None:
    (occurrence,) = _plan(
        datetime(2026, 11, 1, 10, 0, tzinfo=UTC),
        digest_daily_time=time(9, 30),
    )

    assert occurrence.scheduled_at == datetime(2026, 11, 1, 14, 30, tzinfo=UTC)


def test_after_the_local_hour_the_next_day_is_armed() -> None:
    # 13:00 UTC is 08:00 in Bogota: the 07:00 occurrence already started.
    (occurrence,) = _plan(datetime(2026, 11, 1, 13, 0, tzinfo=UTC))

    assert occurrence.occurrence == date(2026, 11, 2)
    assert occurrence.scheduled_at == datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


def test_the_exact_scheduled_instant_is_already_due() -> None:
    # At 12:00 UTC (07:00 Bogota) the row for today must already exist; the planner moves
    # to tomorrow because the next occurrence is strictly in the future.
    (occurrence,) = _plan(datetime(2026, 11, 1, 12, 0, tzinfo=UTC))

    assert occurrence.occurrence == date(2026, 11, 2)


def test_a_daily_preference_without_other_flags_arms_nothing() -> None:
    assert _plan(datetime(2026, 11, 1, 10, 0, tzinfo=UTC), digest_daily_enabled=False) == ()


def test_the_gmail_opt_in_gates_new_occurrences() -> None:
    assert _plan(datetime(2026, 11, 1, 10, 0, tzinfo=UTC), gmail_opt_in=False) == ()


def test_a_weekly_digest_defaults_to_sunday_at_the_configured_hour() -> None:
    # Sunday Nov 1, 05:00 in Bogota: today's Sunday occurrence is still ahead.
    (occurrence,) = _plan(
        datetime(2026, 11, 1, 10, 0, tzinfo=UTC),
        digest_daily_enabled=False,
        digest_weekly_enabled=True,
    )

    assert occurrence.type == "digest_weekly"
    assert occurrence.occurrence == date(2026, 11, 1)
    assert occurrence.scheduled_at == datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    assert occurrence.dedupe_key == f"{USER}|digest_weekly|-|-|2026-11-01"


def test_the_weekly_digest_is_armed_even_when_today_is_not_sunday() -> None:
    # Monday Nov 2, 05:00 Bogota: the next weekly occurrence is the following Sunday.
    (occurrence,) = _plan(
        datetime(2026, 11, 2, 10, 0, tzinfo=UTC),
        digest_daily_enabled=False,
        digest_weekly_enabled=True,
    )

    assert occurrence.occurrence == date(2026, 11, 8)
    assert occurrence.scheduled_at == datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


def test_the_weekly_weekday_is_configurable_and_keeps_the_week_identity() -> None:
    # Sunday Nov 1, 05:00 Bogota; Wednesday Nov 4 at 07:00 is the next occurrence, but the
    # period identity is still the Sunday that starts the week.
    (occurrence,) = _plan(
        datetime(2026, 11, 1, 10, 0, tzinfo=UTC),
        digest_daily_enabled=False,
        digest_weekly_enabled=True,
        digest_weekday=3,
    )

    assert occurrence.occurrence == date(2026, 11, 1)
    assert occurrence.scheduled_at == datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


def test_a_weekday_change_inside_the_same_week_keeps_the_dedupe_key() -> None:
    sunday = _plan(
        datetime(2026, 11, 1, 10, 0, tzinfo=UTC),
        digest_daily_enabled=False,
        digest_weekly_enabled=True,
    )[0]
    wednesday = _plan(
        datetime(2026, 11, 1, 10, 0, tzinfo=UTC),
        digest_daily_enabled=False,
        digest_weekly_enabled=True,
        digest_weekday=3,
    )[0]

    assert sunday.dedupe_key == wednesday.dedupe_key
    assert sunday.occurrence == wednesday.occurrence


def test_a_weekday_already_passed_moves_to_the_next_week() -> None:
    # Thursday Nov 5, 05:00 Bogota; Wednesday Nov 4 already passed.
    (occurrence,) = _plan(
        datetime(2026, 11, 5, 10, 0, tzinfo=UTC),
        digest_daily_enabled=False,
        digest_weekly_enabled=True,
        digest_weekday=3,
    )

    assert occurrence.occurrence == date(2026, 11, 8)
    assert occurrence.scheduled_at == datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


def test_an_exact_weekly_instant_moves_to_the_next_week() -> None:
    (occurrence,) = _plan(
        datetime(2026, 11, 1, 12, 0, tzinfo=UTC),
        digest_daily_enabled=False,
        digest_weekly_enabled=True,
    )

    assert occurrence.occurrence == date(2026, 11, 8)


def test_daily_and_weekly_can_coexist_for_the_same_local_period() -> None:
    occurrences = _plan(
        datetime(2026, 11, 1, 10, 0, tzinfo=UTC), digest_weekly_enabled=True
    )

    assert [occurrence.type for occurrence in occurrences] == [
        "digest_daily",
        "digest_weekly",
    ]
    assert {occurrence.occurrence for occurrence in occurrences} == {date(2026, 11, 1)}
    assert len({occurrence.dedupe_key for occurrence in occurrences}) == 2


def test_a_nonexistent_dst_wall_clock_moves_to_the_first_valid_instant() -> None:
    # America/New_York springs forward at 02:00 on 2026-03-08; 02:30 does not exist. The
    # first valid instant is 03:00 EDT (07:00 UTC), not Python's later ghost instant.
    (occurrence,) = _plan(
        datetime(2026, 3, 8, 0, 0, tzinfo=UTC),
        timezone=NEW_YORK,
        digest_daily_time=time(2, 30),
    )

    assert occurrence.scheduled_at == datetime(2026, 3, 8, 7, 0, tzinfo=UTC)
    assert occurrence.scheduled_at.astimezone(ZoneInfo(NEW_YORK)).hour == 3


def test_a_repeated_dst_wall_clock_is_armed_once_at_its_first_fold() -> None:
    # America/New_York falls back at 02:00 on 2026-11-01; 01:30 happens twice.
    (occurrence,) = _plan(
        datetime(2026, 11, 1, 0, 0, tzinfo=UTC),
        timezone=NEW_YORK,
        digest_daily_time=time(1, 30),
    )

    assert occurrence.scheduled_at == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    assert occurrence.scheduled_at.astimezone(ZoneInfo(NEW_YORK)).fold == 0


def test_the_plan_is_deterministic_across_repeated_calls() -> None:
    now = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
    first = _plan(now, digest_weekly_enabled=True)
    second = _plan(now, digest_weekly_enabled=True)

    assert first == second


@pytest.mark.parametrize("weekday", [7, -1, 100])
def test_an_out_of_range_weekday_is_rejected(weekday: int) -> None:
    with pytest.raises(DigestScheduleError) as error:
        _plan(
            datetime(2026, 11, 1, 10, 0, tzinfo=UTC),
            digest_weekly_enabled=True,
            digest_weekday=weekday,
        )

    assert error.value.code == "weekday_invalid"


def test_a_boolean_is_not_a_weekday() -> None:
    with pytest.raises(DigestScheduleError) as error:
        _plan(
            datetime(2026, 11, 1, 10, 0, tzinfo=UTC),
            digest_weekly_enabled=True,
            digest_weekday=True,
        )

    assert error.value.code == "weekday_invalid"


def test_a_naive_clock_is_rejected() -> None:
    with pytest.raises(DigestScheduleError) as error:
        _plan(datetime(2026, 11, 1, 10, 0))

    assert error.value.code == "clock_invalid"


def test_an_invalid_timezone_is_rejected() -> None:
    with pytest.raises(DigestScheduleError) as error:
        _plan(datetime(2026, 11, 1, 10, 0, tzinfo=UTC), timezone="Not/AZone")

    assert error.value.code == "timezone_invalid"


def test_a_blank_timezone_is_rejected() -> None:
    with pytest.raises(DigestScheduleError) as error:
        _plan(datetime(2026, 11, 1, 10, 0, tzinfo=UTC), timezone="   ")

    assert error.value.code == "timezone_invalid"


def test_a_timezone_aware_digest_time_is_rejected() -> None:
    with pytest.raises(DigestScheduleError) as error:
        _plan(
            datetime(2026, 11, 1, 10, 0, tzinfo=UTC),
            digest_daily_time=time(7, 0, tzinfo=UTC),
        )

    assert error.value.code == "local_time_invalid"


@pytest.mark.parametrize("digest_time", [time(7, 0, 15), time(7, 0, 0, 500)])
def test_a_digest_time_below_minute_precision_is_rejected(digest_time: time) -> None:
    with pytest.raises(DigestScheduleError) as error:
        _plan(datetime(2026, 11, 1, 10, 0, tzinfo=UTC), digest_daily_time=digest_time)

    assert error.value.code == "local_time_invalid"


def test_the_daily_window_is_today_and_tomorrow() -> None:
    assert digest_window("digest_daily", date(2026, 11, 1)) == (
        date(2026, 11, 1),
        date(2026, 11, 2),
    )
    assert DAILY_WINDOW_DAYS == 2


def test_the_weekly_window_is_seven_local_days() -> None:
    assert digest_window("digest_weekly", date(2026, 11, 1)) == (
        date(2026, 11, 1),
        date(2026, 11, 7),
    )


def test_an_unknown_digest_type_has_no_window() -> None:
    with pytest.raises(DigestScheduleError) as error:
        digest_window("reminder_t24", date(2026, 11, 1))

    assert error.value.code == "digest_type_invalid"

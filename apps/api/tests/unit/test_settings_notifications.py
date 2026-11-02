"""A4.1: the Gmail/digest preferences extend the existing per-user `settings` row.

S3's Calendar row is the only settings table. This ticket adds opt-in Gmail, daily/weekly
digest preferences and the reminder thresholds to that same row; the defaults must keep
every user opted out and the notification schedule equal to the M8 thresholds until the user
changes them. No scope is granted and no mail is sent from these columns.
"""

from typing import Any, cast

from sqlalchemy.schema import CheckConstraint, Table

from app.models import Base
from app.models.settings import CalendarSettings

SETTINGS = cast(Table, CalendarSettings.__table__)

A41_COLUMNS = (
    "gmail_opt_in",
    "digest_daily_enabled",
    "digest_daily_time",
    "digest_weekly_enabled",
    "digest_weekday",
    "reminder_offsets_hours",
    "reminder_allday_offsets_days",
)


def _check(name: str) -> str:
    expected = f"ck_settings_{name}"
    for constraint in SETTINGS.constraints:
        if isinstance(constraint, CheckConstraint) and constraint.name == expected:
            return " ".join(str(constraint.sqltext).split())
    raise AssertionError(f"CHECK {expected!r} not found on settings")


def _default(column: str) -> str:
    return str(cast(Any, SETTINGS.c[column].server_default).arg)


def test_the_gmail_opt_in_is_disabled_by_default() -> None:
    assert SETTINGS.c.gmail_opt_in.nullable is False
    assert SETTINGS.c.gmail_opt_in.type.__class__.__name__ == "Boolean"
    assert _default("gmail_opt_in") == "false"
    # Opt-in is a preference, not a credential: no token/scope columns are added here.
    assert "gmail_refresh_token" not in SETTINGS.c
    assert "gmail_scopes" not in SETTINGS.c


def test_both_digests_are_disabled_by_default_with_m8_defaults() -> None:
    assert _default("digest_daily_enabled") == "false"
    assert _default("digest_weekly_enabled") == "false"
    assert _default("digest_daily_time") == "'07:00'"
    assert _default("digest_weekday") == "0"
    assert SETTINGS.c.digest_daily_time.type.__class__.__name__ == "Time"
    for column in ("digest_daily_enabled", "digest_weekly_enabled"):
        assert SETTINGS.c[column].nullable is False


def test_the_digest_weekday_is_a_closed_zero_to_six_range() -> None:
    assert _check("digest_weekday") == "digest_weekday BETWEEN 0 AND 6"
    assert SETTINGS.c.digest_weekday.type.__class__.__name__ == "Integer"
    assert SETTINGS.c.digest_weekday.nullable is False


def test_the_m8_thresholds_are_the_defaults() -> None:
    assert _default("reminder_offsets_hours") == "'[48, 24, 2]'::jsonb"
    assert _default("reminder_allday_offsets_days") == "'[3, 1]'::jsonb"
    for column in ("reminder_offsets_hours", "reminder_allday_offsets_days"):
        assert SETTINGS.c[column].type.__class__.__name__ == "JSONB"
        assert SETTINGS.c[column].nullable is False
    assert _check("reminder_offsets_hours") == (
        "jsonb_typeof(reminder_offsets_hours) = 'array'"
    )
    assert _check("reminder_allday_offsets_days") == (
        "jsonb_typeof(reminder_allday_offsets_days) = 'array'"
    )


def test_the_calendar_contract_is_untouched() -> None:
    assert {"calendar_write_calendar_id", "calendar_read_calendar_ids"} <= set(SETTINGS.c.keys())
    assert "calendar_last_manual_sync_at" in SETTINGS.c
    assert _check("coverage_window")  # A3.8's coverage CHECK still exists


def test_a41_extends_the_only_settings_table() -> None:
    assert set(A41_COLUMNS) <= set(SETTINGS.c.keys())
    settings_tables = [
        name for name in Base.metadata.tables if "settings" in name and name != "settings"
    ]
    assert settings_tables == []

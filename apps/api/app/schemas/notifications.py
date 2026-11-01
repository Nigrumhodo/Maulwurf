"""M8 notification API schemas (A4.2): preferences, deliveries and manual actions.

The response shapes deliberately carry no message body, recipient address or provider
payload: the deliveries panel needs identity, status, timing and a redacted error code only.
Preferences are a **partial** update (every field optional); a response always contains the
complete set so the settings screen can re-render without a second endpoint round trip.
"""

from datetime import date, datetime, time
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

NotificationStatus = Literal[
    "pending",
    "sending",
    "sent",
    "failed",
    "delivery_unknown",
    "cancelled",
]
NotificationType = Literal[
    "reminder_t48",
    "reminder_t24",
    "reminder_t2",
    "reminder_t3d",
    "reminder_t1d",
    "digest_daily",
    "digest_weekly",
    "test",
]

# A reminder offset is a positive distance, never an instant. A year is a generous upper
# bound that keeps `due - offset` representable; the exact scheduling policy stays in J4.3.
MAX_OFFSET_HOURS = 24 * 366
MAX_OFFSET_DAYS = 366
MAX_THRESHOLDS = 10

ThresholdHours = Annotated[int, Field(ge=1, le=MAX_OFFSET_HOURS)]
ThresholdDays = Annotated[int, Field(ge=1, le=MAX_OFFSET_DAYS)]


def format_digest_time(value: time) -> str:
    """Render the daily digest preference with the minute precision of an `<input type=time>`."""
    return f"{value.hour:02d}:{value.minute:02d}"


def _normalize_thresholds(values: list[int]) -> list[int]:
    """Canonical order: descending and unique, so equivalent payloads persist identically."""
    return sorted(set(values), reverse=True)


class NotificationPreferencesPatch(BaseModel):
    """Partial M8 preference update; omitted fields keep their stored value."""

    model_config = ConfigDict(extra="forbid")

    gmail_opt_in: bool | None = None
    digest_daily_enabled: bool | None = None
    digest_daily_time: time | None = None
    digest_weekly_enabled: bool | None = None
    digest_weekday: int | None = Field(default=None, ge=0, le=6)
    reminder_offsets_hours: list[ThresholdHours] | None = Field(
        default=None, max_length=MAX_THRESHOLDS
    )
    reminder_allday_offsets_days: list[ThresholdDays] | None = Field(
        default=None, max_length=MAX_THRESHOLDS
    )

    @field_validator("digest_daily_time")
    @classmethod
    def _minute_precision(cls, value: time | None) -> time | None:
        if value is not None and (value.second or value.microsecond):
            raise ValueError("la hora del digest usa precisión de minutos (HH:MM)")
        return value

    @field_validator("reminder_offsets_hours")
    @classmethod
    def _hours(cls, value: list[int] | None) -> list[int] | None:
        return None if value is None else _normalize_thresholds(value)

    @field_validator("reminder_allday_offsets_days")
    @classmethod
    def _days(cls, value: list[int] | None) -> list[int] | None:
        return None if value is None else _normalize_thresholds(value)

    @model_validator(mode="after")
    def _no_explicit_nulls(self) -> "NotificationPreferencesPatch":
        for name in self.model_fields_set:
            if getattr(self, name) is None:
                raise ValueError(f"{name} no admite null")
        return self


class NotificationPreferencesOut(BaseModel):
    gmail_opt_in: bool
    digest_daily_enabled: bool
    digest_daily_time: str
    digest_weekly_enabled: bool
    digest_weekday: int
    reminder_offsets_hours: list[int]
    reminder_allday_offsets_days: list[int]
    updated_at: datetime | None


class NotificationOut(BaseModel):
    """One audited occurrence; no content, recipient or provider payload."""

    id: str
    type: NotificationType
    status: NotificationStatus
    task_id: str | None
    occurrence: date | None
    due_version: int | None
    occurrence_seq: int
    scheduled_at: datetime
    attempts: int
    sent_at: datetime | None
    last_error_code: str | None
    created_at: datetime
    updated_at: datetime


class NotificationListOut(BaseModel):
    items: list[NotificationOut]
    next_cursor: str | None


class NotificationWarningOut(BaseModel):
    code: Literal["delivery_uncertain", "duplicate_risk_acknowledged"]
    message: str


class NotificationActionOut(BaseModel):
    notification: NotificationOut
    warning: NotificationWarningOut

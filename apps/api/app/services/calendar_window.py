"""Local-day calendar windows and the shared calendar error types (J3.7).

Day bounds use the user's IANA timezone. A half-given range is anchored on the bound
the client sent.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Final, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DAYS_BACK = 7
DAYS_FORWARD = 60
# A query can never ask for an unbounded mirror scan; the default window is 68 local days.
MAX_QUERY_DAYS = 366
MAX_PAGES = 100
MAX_PAGE_SIZE = 2500
# The spec refreshes the mirror on demand or hourly; two hours is a conservative staleness
# signal for the dashboard, not a scheduled job of its own.
STALE_AFTER = timedelta(hours=2)
# Each manual refresh fans out to Google (`MAX_PAGES` per selected calendar). A per-user
# cooldown keeps a double click or an authenticated script from spending the project quota;
# the stamp is written before the provider call, so a failed attempt also waits.
MANUAL_SYNC_COOLDOWN_SECONDS = 60
# The `FOR UPDATE` transactions only cover the cooldown decision and the token renewal. A
# longer wait is a transient contention signal: failing fast beats pinning a pooled
# connection (and the user's credential) behind Google latency.
LOCK_TIMEOUT_MS = 2_000
LOCK_RETRY_AFTER_SECONDS = 2

# Local `remote_status` values added by this mirror on top of Google's own vocabulary:
# `missing` = a task-linked row the read did not return; `cancelled` = Google reported the
# linked event as cancelled. Both keep `task_id`; only a later read that returns the event
# rewrites the status. Unlinked rows are deleted instead, because they carry no link.
MISSING: Final = "missing"
CANCELLED: Final = "cancelled"

# Advisory-lock convention (A2.14, `services/audios.py`): the two-int form namespaces our
# locks instead of a bare bigint. The namespace is the same 0x4D57 ("MW") value that module
# uses and the per-user key uses the same derivation; a key collision only serializes two
# unrelated transactions for a moment and can never admit more work than the limits allow.
_ADVISORY_LOCK_NAMESPACE = 0x4D57

# Closed coverage vocabulary consumed by D3.6 and the `get_calendar_events` tool (J3.8).
CoverageNotice = Literal[
    "never_synced",
    "no_read_calendars",
    "calendar_set_changed",
    "outside_coverage",
    "stale",
]
NEVER_SYNCED: Final[CoverageNotice] = "never_synced"
NO_READ_CALENDARS: Final[CoverageNotice] = "no_read_calendars"
CALENDAR_SET_CHANGED: Final[CoverageNotice] = "calendar_set_changed"
OUTSIDE_COVERAGE: Final[CoverageNotice] = "outside_coverage"
STALE: Final[CoverageNotice] = "stale"

class CalendarSyncError(Exception):
    """Typed Calendar read/publication error. `code` is stable and safe to log."""

    code = "calendar_sync_error"

    def __init__(self, code: str, *, field: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.field = field


class CalendarWindowInvalid(CalendarSyncError):
    code = "calendar_window_invalid"


class CalendarEventInvalid(CalendarSyncError):
    code = "calendar_event_invalid"


class CalendarPaginationExhausted(CalendarSyncError):
    code = "calendar_pagination_exhausted"


class CalendarSyncUnavailable(CalendarSyncError):
    """A manual refresh cannot start in the current state (HTTP 409).

    `code` is the closed reason: `google_not_connected`, `missing_calendar_scopes`,
    `no_read_calendars`, `google_invalid_grant` or `calendar_read_failed`.
    """


class CalendarSyncCooldown(CalendarSyncError):
    """A manual refresh was requested inside the per-user cooldown (HTTP 429)."""

    code = "calendar_sync_cooldown"

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(self.code)
        self.retry_after_seconds = retry_after_seconds


class CalendarSyncBusy(CalendarSyncError):
    """The short lock window was not acquired in time (transient, HTTP 503)."""

    code = "calendar_sync_busy"


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        # A stored value can predate validation or come from an old client. UTC keeps the
        # window deterministic instead of turning a read into a 500.
        return ZoneInfo("UTC")


def _valid_timezone(value: object) -> str | None:
    """Keep only an IANA zone `zoneinfo` can resolve; a bogus one is stored as unknown."""
    if not isinstance(value, str) or not value:
        return None
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return value


def _user_lock_key(user_id: uuid.UUID) -> int:
    """Stable 31-bit advisory key for one user (fits the signed `int4` of the lock)."""
    return user_id.int & 0x7FFF_FFFF


@dataclass(frozen=True)
class CalendarWindow:
    """Half-open absolute window plus the IANA zone used to derive its local day bounds."""

    start: datetime
    end: datetime
    timezone: str = "UTC"

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise CalendarWindowInvalid("window_naive")
        if self.start >= self.end:
            raise CalendarWindowInvalid("window_inverted")
        object.__setattr__(self, "start", self.start.astimezone(UTC))
        object.__setattr__(self, "end", self.end.astimezone(UTC))

    @property
    def zone(self) -> ZoneInfo:
        return _zone(self.timezone)

    @property
    def start_date(self) -> date:
        return self.start.astimezone(self.zone).date()

    @property
    def end_date(self) -> date:
        """Last local date the window covers (inclusive)."""
        return (self.end - timedelta(microseconds=1)).astimezone(self.zone).date()

    @classmethod
    def default(cls, *, now: datetime, timezone: str) -> "CalendarWindow":
        zone = _zone(timezone)
        today = now.astimezone(zone).date()
        return cls.from_dates(
            today - timedelta(days=DAYS_BACK),
            today + timedelta(days=DAYS_FORWARD),
            timezone=timezone,
        )

    @classmethod
    def from_dates(
        cls, date_from: date, date_to: date, *, timezone: str
    ) -> "CalendarWindow":
        if date_to < date_from:
            raise CalendarWindowInvalid("window_inverted", field="to")
        if (date_to - date_from).days + 1 > MAX_QUERY_DAYS:
            raise CalendarWindowInvalid("window_too_wide", field="to")
        zone = _zone(timezone)
        start = datetime.combine(date_from, time.min, tzinfo=zone)
        # Inclusive `date_to`: the exclusive instant is the next local midnight.
        end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=zone)
        return cls(start=start, end=end, timezone=timezone)


def _resolve_window(
    *,
    date_from: date | None,
    date_to: date | None,
    timezone: str,
    now: datetime,
) -> CalendarWindow:
    """Complete a half-given range from the bound the client actually sent.

    A lone bound anchors the window (`from` → `from + 60`, `to` → `to - 7`). Filling the
    missing side from today's default would invert a future `from` and answer a misleading
    "`to` cannot be before `from`" for a parameter the client never sent.
    """
    if date_to is None:
        if date_from is None:
            return CalendarWindow.default(now=now, timezone=timezone)
        return CalendarWindow.from_dates(
            date_from, date_from + timedelta(days=DAYS_FORWARD), timezone=timezone
        )
    if date_from is None:
        return CalendarWindow.from_dates(
            date_to - timedelta(days=DAYS_BACK), date_to, timezone=timezone
        )
    return CalendarWindow.from_dates(date_from, date_to, timezone=timezone)

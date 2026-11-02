"""Date resolution against the class (L3.4).

A due date is resolved from the class date and the class timezone snapshot. The server
clock (`now`) is a required injected argument and does not move the result; the upload
instant is not an input at all.

The mention is a closed shape, not free text. The L3.2 schema already carries the
structured due fields this function fills:

- an all-day mention stays `due_date` and never becomes a midnight `due_at`;
- an explicit local time uses J4.4 `resolve_local_wall` (a spring-forward gap moves to the
  next valid instant, a repeated hour is kept once) and stays `date_status=resolved`;
- "next <weekday>" and a weekday that is also the class day are ambiguous: both dates
  null, `date_status=ambiguous`, `needs_review`;
- no mention is `date_status=missing` and `needs_review`.

Impossible calendar dates and unknown timezones raise `DateResolutionError` and produce
no proposal. The DISENO one-liner that sent DST cases to `needs_review` yields to this
ticket and ESPECIFICACION §M4: DST is normalized, relative ambiguity is what needs review.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from typing import NoReturn
from zoneinfo import ZoneInfo

from app.core.validation import is_iana_timezone
from app.schemas.analysis import DateStatus
from app.services.reminders import resolve_local_wall


class MentionKind(StrEnum):
    """Closed date mention. Free text is not accepted."""

    ABSENT = "absent"
    ALL_DAY = "all_day"
    LOCAL_TIME = "local_time"
    DAY_OFFSET = "day_offset"
    WEEKDAY = "weekday"


class WeekdayQualifier(StrEnum):
    """How a weekday is anchored to the class date.

    `coming` is the soonest strictly future occurrence inside seven days. The same weekday
    as the class is ambiguous (the class day versus a week later) and is not guessed.
    `next` is always ambiguous: "next Friday" does not say which Friday.
    """

    COMING = "coming"
    NEXT = "next"


class DateResolutionError(Exception):
    """The mention or the class context cannot produce a proposal."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _fail(code: str, message: str, cause: BaseException | None = None) -> NoReturn:
    error = DateResolutionError(code, message)
    if cause is None:
        raise error
    raise error from cause


@dataclass(frozen=True)
class DateMention:
    """One closed mention. `resolve_due` rejects a combination that does not match `kind`."""

    kind: MentionKind
    due_date: date | None = None
    local_time: time | None = None
    day_offset: int | None = None
    weekday: int | None = None
    qualifier: WeekdayQualifier | None = None


@dataclass(frozen=True)
class ResolvedDue:
    """The exclusive due pair, or neither when the date is missing or ambiguous.

    `needs_review` is true exactly when `date_status` is not `resolved`. A resolved date
    is still only a proposal: this flag does not confirm a task.
    """

    due_date: date | None
    due_at: datetime | None
    timezone: str
    all_day: bool
    date_status: DateStatus
    needs_review: bool
    reason: str | None


def mention_from_calendar(year: int, month: int, day: int) -> DateMention:
    """An all-day mention, or `impossible_date` when the calendar day does not exist."""
    try:
        parsed = date(year, month, day)
    except ValueError as exc:
        _fail("impossible_date", "the calendar date does not exist", exc)
    return DateMention(kind=MentionKind.ALL_DAY, due_date=parsed)


def resolve_due(
    mention: DateMention,
    *,
    class_date: date,
    class_timezone: str,
    now: datetime,
) -> ResolvedDue:
    """Resolve `mention` against the class. `now` is validated and then ignored.

    :raises DateResolutionError: invalid clock, timezone, mention shape, or impossible day.
    """
    _require_clock(now)
    if not is_iana_timezone(class_timezone):
        _fail("timezone_invalid", "the class timezone is not a valid IANA name")
    _require_shape(mention)
    if mention.kind is MentionKind.ABSENT:
        return _unresolved(class_timezone, DateStatus.MISSING, "missing")
    if mention.kind is MentionKind.ALL_DAY:
        if mention.due_date is None:
            _fail("mention_invalid", "the date mention does not match its kind")
        return _resolved_day(mention.due_date, class_timezone)
    if mention.kind is MentionKind.LOCAL_TIME:
        if mention.due_date is None or mention.local_time is None:
            _fail("mention_invalid", "the date mention does not match its kind")
        instant = resolve_local_wall(
            datetime.combine(mention.due_date, mention.local_time),
            ZoneInfo(class_timezone),
        )
        return _resolved_instant(instant, class_timezone)
    if mention.kind is MentionKind.DAY_OFFSET:
        if mention.day_offset is None:
            _fail("mention_invalid", "the date mention does not match its kind")
        try:
            day = class_date + timedelta(days=mention.day_offset)
        except OverflowError as exc:
            _fail("impossible_date", "the date offset does not fall on a real day", exc)
        return _resolved_day(day, class_timezone)
    if mention.weekday is None or mention.qualifier is None:
        _fail("mention_invalid", "the date mention does not match its kind")
    if mention.qualifier is WeekdayQualifier.NEXT:
        return _unresolved(class_timezone, DateStatus.AMBIGUOUS, "ambiguous_relative")
    delta = (mention.weekday - class_date.weekday()) % 7
    if delta == 0:
        return _unresolved(class_timezone, DateStatus.AMBIGUOUS, "ambiguous_weekday")
    return _resolved_day(class_date + timedelta(days=delta), class_timezone)


def _require_clock(now: datetime) -> None:
    if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        _fail("clock_invalid", "the clock must be timezone-aware")


def _require_shape(mention: DateMention) -> None:
    if not isinstance(mention.kind, MentionKind):
        _fail("mention_invalid", "the date mention does not match its kind")
    present = {
        "due_date": mention.due_date is not None,
        "local_time": mention.local_time is not None,
        "day_offset": mention.day_offset is not None,
        "weekday": mention.weekday is not None,
        "qualifier": mention.qualifier is not None,
    }
    allowed = _ALLOWED[mention.kind]
    if present != allowed:
        _fail("mention_invalid", "the date mention does not match its kind")
    if mention.kind is MentionKind.DAY_OFFSET and isinstance(mention.day_offset, bool):
        _fail("mention_invalid", "the date mention does not match its kind")
    if mention.kind is MentionKind.WEEKDAY:
        weekday = mention.weekday
        if (
            isinstance(weekday, bool)
            or not isinstance(weekday, int)
            or not 0 <= weekday <= 6
            or not isinstance(mention.qualifier, WeekdayQualifier)
        ):
            _fail("mention_invalid", "the date mention does not match its kind")


_ALLOWED: dict[MentionKind, dict[str, bool]] = {
    MentionKind.ABSENT: {
        "due_date": False,
        "local_time": False,
        "day_offset": False,
        "weekday": False,
        "qualifier": False,
    },
    MentionKind.ALL_DAY: {
        "due_date": True,
        "local_time": False,
        "day_offset": False,
        "weekday": False,
        "qualifier": False,
    },
    MentionKind.LOCAL_TIME: {
        "due_date": True,
        "local_time": True,
        "day_offset": False,
        "weekday": False,
        "qualifier": False,
    },
    MentionKind.DAY_OFFSET: {
        "due_date": False,
        "local_time": False,
        "day_offset": True,
        "weekday": False,
        "qualifier": False,
    },
    MentionKind.WEEKDAY: {
        "due_date": False,
        "local_time": False,
        "day_offset": False,
        "weekday": True,
        "qualifier": True,
    },
}


def _resolved_day(day: date, timezone: str) -> ResolvedDue:
    return ResolvedDue(
        due_date=day,
        due_at=None,
        timezone=timezone,
        all_day=True,
        date_status=DateStatus.RESOLVED,
        needs_review=False,
        reason=None,
    )


def _resolved_instant(instant: datetime, timezone: str) -> ResolvedDue:
    return ResolvedDue(
        due_date=None,
        due_at=instant,
        timezone=timezone,
        all_day=False,
        date_status=DateStatus.RESOLVED,
        needs_review=False,
        reason=None,
    )


def _unresolved(timezone: str, status: DateStatus, reason: str) -> ResolvedDue:
    return ResolvedDue(
        due_date=None,
        due_at=None,
        timezone=timezone,
        all_day=False,
        date_status=status,
        needs_review=True,
        reason=reason,
    )

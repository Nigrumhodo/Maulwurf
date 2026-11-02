"""U-S3-JL-03 (L3.4): dates resolve against the class, not the server clock.

DST follows J4.4 (`resolve_local_wall`): a nonexistent hour moves to the next valid
instant and a repeated hour is kept once, both still `resolved`. "next Friday" and a
weekday that is the class day are `needs_review` with no guessed date. A date-only
mention never becomes midnight.
"""

from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app.schemas.analysis import DateStatus, ProposedItem, Source, SourceSpan
from app.services.date_resolution import (
    DateMention,
    DateResolutionError,
    MentionKind,
    ResolvedDue,
    WeekdayQualifier,
    mention_from_calendar,
    resolve_due,
)

NEW_YORK = "America/New_York"
MEXICO = "America/Mexico_City"
FRIDAY = 4
QUOTE = "bring the problem set"
SEGMENT = UUID("3f1c9a52-7d4e-4b8a-9c1e-2a6b5d8e0f11")
EARLY = datetime(1990, 1, 1, tzinfo=UTC)
LATE = datetime(2099, 6, 1, tzinfo=UTC)


def _weekday(weekday: int, start: date) -> date:
    return start + timedelta(days=(weekday - start.weekday()) % 7)


def _propose(resolved: ResolvedDue) -> ProposedItem:
    return ProposedItem(
        tipo="tarea",
        titulo="Problem set",
        timezone=resolved.timezone,
        all_day=resolved.all_day,
        date_status=resolved.date_status,
        due_date=resolved.due_date,
        due_at=resolved.due_at,
        fuente=Source(
            spans=[
                SourceSpan(
                    segment_id=SEGMENT,
                    inicio_char=0,
                    fin_char=len(QUOTE),
                    cita_textual=QUOTE,
                )
            ]
        ),
    )


def test_a_nonexistent_hour_moves_to_the_next_valid_instant() -> None:
    mention = DateMention(
        kind=MentionKind.LOCAL_TIME, due_date=date(2026, 3, 8), local_time=time(2, 30)
    )
    resolved = resolve_due(mention, class_date=date(2026, 3, 2), class_timezone=NEW_YORK, now=EARLY)
    assert resolved.date_status is DateStatus.RESOLVED
    assert resolved.needs_review is False
    assert resolved.reason is None
    assert resolved.due_date is None
    assert resolved.due_at == datetime(2026, 3, 8, 7, 0, tzinfo=UTC)
    assert resolved == resolve_due(
        mention, class_date=date(2026, 3, 2), class_timezone=NEW_YORK, now=LATE
    )


def test_a_repeated_hour_is_kept_once_at_the_first_fold() -> None:
    mention = DateMention(
        kind=MentionKind.LOCAL_TIME, due_date=date(2026, 11, 1), local_time=time(1, 30)
    )
    resolved = resolve_due(
        mention, class_date=date(2026, 10, 28), class_timezone=NEW_YORK, now=LATE
    )
    assert resolved.date_status is DateStatus.RESOLVED
    assert resolved.needs_review is False
    assert resolved.due_at is not None
    # `replace(tzinfo=ZoneInfo)` does not compare equal to a `timezone.utc` instant
    # even when both are the same moment; compare the UTC conversion.
    assert resolved.due_at.fold == 0
    assert resolved.due_at.astimezone(UTC) == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    assert resolved.due_at.astimezone(UTC) != datetime(2026, 11, 1, 6, 30, tzinfo=UTC)


def test_an_all_day_mention_never_becomes_midnight() -> None:
    day = date(2026, 3, 8)
    resolved = resolve_due(
        DateMention(kind=MentionKind.ALL_DAY, due_date=day),
        class_date=date(2026, 3, 2),
        class_timezone=NEW_YORK,
        now=EARLY,
    )
    assert resolved.due_date == day
    assert resolved.due_at is None
    assert resolved.all_day is True
    assert resolved.date_status is DateStatus.RESOLVED
    assert resolved.needs_review is False


def test_an_explicit_midnight_is_kept_as_an_instant() -> None:
    resolved = resolve_due(
        DateMention(kind=MentionKind.LOCAL_TIME, due_date=date(2026, 9, 21), local_time=time(0, 0)),
        class_date=date(2026, 9, 16),
        class_timezone=MEXICO,
        now=LATE,
    )
    assert resolved.due_date is None
    assert resolved.all_day is False
    assert resolved.date_status is DateStatus.RESOLVED
    assert resolved.due_at is not None
    assert resolved.due_at.astimezone(ZoneInfo(MEXICO)).time() == time(0, 0)


def test_tomorrow_is_the_day_after_the_class() -> None:
    class_day = date(2026, 9, 16)
    early = resolve_due(
        DateMention(kind=MentionKind.DAY_OFFSET, day_offset=1),
        class_date=class_day,
        class_timezone=MEXICO,
        now=EARLY,
    )
    late = resolve_due(
        DateMention(kind=MentionKind.DAY_OFFSET, day_offset=1),
        class_date=class_day,
        class_timezone=MEXICO,
        now=LATE,
    )
    assert early == late
    assert early.due_date == date(2026, 9, 17)
    assert early.due_at is None
    assert early.all_day is True
    same_day = resolve_due(
        DateMention(kind=MentionKind.DAY_OFFSET, day_offset=0),
        class_date=class_day,
        class_timezone=MEXICO,
        now=LATE,
    )
    assert same_day.due_date == class_day
    yesterday = resolve_due(
        DateMention(kind=MentionKind.DAY_OFFSET, day_offset=-1),
        class_date=class_day,
        class_timezone=MEXICO,
        now=EARLY,
    )
    assert yesterday.due_date == date(2026, 9, 15)


def test_a_coming_weekday_uses_the_class_date() -> None:
    class_day = date(2026, 9, 16)
    assert class_day.weekday() != FRIDAY
    resolved = resolve_due(
        DateMention(kind=MentionKind.WEEKDAY, weekday=FRIDAY, qualifier=WeekdayQualifier.COMING),
        class_date=class_day,
        class_timezone=MEXICO,
        now=LATE,
    )
    assert resolved.due_date == _weekday(FRIDAY, class_day)
    assert resolved.due_date != class_day
    assert resolved.due_at is None
    assert resolved.date_status is DateStatus.RESOLVED
    assert resolved.needs_review is False


def test_next_friday_is_ambiguous() -> None:
    resolved = resolve_due(
        DateMention(kind=MentionKind.WEEKDAY, weekday=FRIDAY, qualifier=WeekdayQualifier.NEXT),
        class_date=date(2026, 9, 16),
        class_timezone=MEXICO,
        now=EARLY,
    )
    assert resolved.date_status is DateStatus.AMBIGUOUS
    assert resolved.needs_review is True
    assert resolved.reason == "ambiguous_relative"
    assert resolved.due_date is None
    assert resolved.due_at is None
    assert resolved.all_day is False


def test_the_class_weekday_itself_is_ambiguous() -> None:
    class_day = _weekday(FRIDAY, date(2026, 9, 16))
    assert class_day.weekday() == FRIDAY
    resolved = resolve_due(
        DateMention(kind=MentionKind.WEEKDAY, weekday=FRIDAY, qualifier=WeekdayQualifier.COMING),
        class_date=class_day,
        class_timezone=MEXICO,
        now=LATE,
    )
    assert resolved.date_status is DateStatus.AMBIGUOUS
    assert resolved.needs_review is True
    assert resolved.reason == "ambiguous_weekday"
    assert resolved.due_date is None
    assert resolved.due_at is None


def test_a_missing_mention_stays_missing() -> None:
    resolved = resolve_due(
        DateMention(kind=MentionKind.ABSENT),
        class_date=date(2026, 9, 16),
        class_timezone=MEXICO,
        now=EARLY,
    )
    assert resolved.date_status is DateStatus.MISSING
    assert resolved.needs_review is True
    assert resolved.reason == "missing"
    assert resolved.due_date is None
    assert resolved.due_at is None


def test_the_injected_clock_does_not_move_the_result() -> None:
    mention = DateMention(kind=MentionKind.DAY_OFFSET, day_offset=1)
    early = resolve_due(mention, class_date=date(2026, 9, 16), class_timezone="UTC", now=EARLY)
    late = resolve_due(mention, class_date=date(2026, 9, 16), class_timezone="UTC", now=LATE)
    assert early == late
    assert early.due_date == date(2026, 9, 17)


def test_an_impossible_calendar_date_is_rejected() -> None:
    with pytest.raises(DateResolutionError) as caught:
        mention_from_calendar(2026, 2, 30)
    assert caught.value.code == "impossible_date"


def test_an_unknown_timezone_is_rejected() -> None:
    with pytest.raises(DateResolutionError) as caught:
        resolve_due(
            DateMention(kind=MentionKind.ABSENT),
            class_date=date(2026, 9, 16),
            class_timezone="Mars/Base",
            now=EARLY,
        )
    assert caught.value.code == "timezone_invalid"
    assert "Mars/Base" not in str(caught.value)


def test_a_naive_clock_is_rejected() -> None:
    with pytest.raises(DateResolutionError) as caught:
        resolve_due(
            DateMention(kind=MentionKind.ABSENT),
            class_date=date(2026, 9, 16),
            class_timezone="UTC",
            now=datetime(2026, 9, 16, 12, 0),
        )
    assert caught.value.code == "clock_invalid"


def test_a_date_offset_past_the_calendar_is_rejected() -> None:
    with pytest.raises(DateResolutionError) as caught:
        resolve_due(
            DateMention(kind=MentionKind.DAY_OFFSET, day_offset=1),
            class_date=date.max,
            class_timezone="UTC",
            now=EARLY,
        )
    assert caught.value.code == "impossible_date"


def test_a_mention_that_does_not_match_its_kind_is_rejected() -> None:
    with pytest.raises(DateResolutionError) as caught:
        resolve_due(
            DateMention(kind=MentionKind.ALL_DAY),
            class_date=date(2026, 9, 16),
            class_timezone="UTC",
            now=EARLY,
        )
    assert caught.value.code == "mention_invalid"
    with pytest.raises(DateResolutionError) as caught_offset:
        resolve_due(
            DateMention(kind=MentionKind.DAY_OFFSET, day_offset=True),
            class_date=date(2026, 9, 16),
            class_timezone="UTC",
            now=EARLY,
        )
    assert caught_offset.value.code == "mention_invalid"


def test_a_resolved_date_fits_the_m4_item() -> None:
    resolved = resolve_due(
        DateMention(kind=MentionKind.ALL_DAY, due_date=date(2026, 9, 21)),
        class_date=date(2026, 9, 16),
        class_timezone=MEXICO,
        now=EARLY,
    )
    item = _propose(resolved)
    assert item.date_status is DateStatus.RESOLVED
    assert item.due_date == date(2026, 9, 21)
    assert item.due_at is None


def test_an_ambiguous_date_fits_the_m4_item_with_no_due() -> None:
    resolved = resolve_due(
        DateMention(kind=MentionKind.WEEKDAY, weekday=FRIDAY, qualifier=WeekdayQualifier.NEXT),
        class_date=date(2026, 9, 16),
        class_timezone=MEXICO,
        now=LATE,
    )
    item = _propose(resolved)
    assert item.date_status is DateStatus.AMBIGUOUS
    assert item.due_date is None
    assert item.due_at is None

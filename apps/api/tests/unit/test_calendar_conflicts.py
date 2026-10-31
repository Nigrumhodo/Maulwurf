"""U-S3-JF-07 (J3.8): timed overlap is not per-day clustering.

The distinction is a product contract (M5): two events that actually share time are a
`time_conflict`; two deadlines that merely fall on the same local day are a
`deadline_clash`; an all-day event is never a time conflict. These cases are pure and do
not touch PostgreSQL; the HTTP/DTO behavior is covered by the integration suite.
"""

from datetime import UTC, date, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.calendar_sync import (
    MANUAL_SYNC_COOLDOWN_SECONDS,
    CalendarEventView,
    manual_sync_cooldown_remaining,
)
from app.services.conflicts import (
    DEADLINE_TIPOS,
    MIN_DEADLINES_PER_DAY,
    ConflictReport,
    Deadline,
    SqlTaskDeadlineSource,
    detect_conflicts,
)

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)


def _timed(
    event_id: str,
    start: datetime,
    *,
    hours: float = 1,
    calendar_id: str = "primary",
    title: str | None = None,
    remote_status: str = "confirmed",
) -> CalendarEventView:
    return CalendarEventView(
        id=uuid4(),
        calendar_id=calendar_id,
        event_id=event_id,
        title=title or event_id,
        source="google",
        start_at=start,
        end_at=start + timedelta(hours=hours),
        start_date=None,
        end_date_exclusive=None,
        timezone="America/Bogota",
        remote_status=remote_status,
        last_synced_at=NOW,
    )


def _all_day(
    event_id: str,
    day: date,
    *,
    days: int = 1,
    calendar_id: str = "primary",
    remote_status: str = "confirmed",
) -> CalendarEventView:
    return CalendarEventView(
        id=uuid4(),
        calendar_id=calendar_id,
        event_id=event_id,
        title=event_id,
        source="google",
        start_at=None,
        end_at=None,
        start_date=day,
        end_date_exclusive=day + timedelta(days=days),
        timezone=None,
        remote_status=remote_status,
        last_synced_at=NOW,
    )


def _deadline(
    day: date | None = None,
    *,
    at: datetime | None = None,
    tipo: str = "examen",
    task_id: UUID | None = None,
) -> Deadline:
    return Deadline(task_id=task_id or uuid4(), tipo=tipo, due_date=day, due_at=at)


def _pairs(report: ConflictReport) -> list[tuple[str, str]]:
    return [(pair.a.event_id, pair.b.event_id) for pair in report.time_conflicts]


def test_edge_touching_events_do_not_overlap() -> None:
    start = NOW.replace(hour=9, minute=0)
    report = detect_conflicts(
        [_timed("a", start), _timed("b", start + timedelta(hours=1))],
        [],
        timezone="America/Bogota",
    )

    assert report.time_conflicts == ()


def test_one_second_of_overlap_is_a_time_conflict() -> None:
    start = NOW.replace(hour=9, minute=0)
    report = detect_conflicts(
        [
            _timed("a", start),
            _timed("b", start + timedelta(minutes=59, seconds=59)),
        ],
        [],
        timezone="America/Bogota",
    )

    assert _pairs(report) == [("a", "b")]


def test_same_day_events_without_overlap_are_not_time_conflicts() -> None:
    morning = NOW.replace(hour=8, minute=0)
    evening = NOW.replace(hour=18, minute=0)
    report = detect_conflicts(
        [_timed("morning", morning), _timed("evening", evening)],
        [],
        timezone="America/Bogota",
    )

    assert report.time_conflicts == ()


def test_all_day_events_are_never_time_conflicts() -> None:
    day = date(2026, 11, 2)
    report = detect_conflicts(
        [
            _all_day("exam-day", day),
            _all_day("project-day", day),
            _timed("class", datetime(2026, 11, 2, 9, 0, tzinfo=UTC)),
        ],
        [],
        timezone="America/Bogota",
    )

    assert report.time_conflicts == ()


def test_cancelled_and_missing_events_are_not_current_conflicts() -> None:
    start = NOW.replace(hour=9, minute=0)
    report = detect_conflicts(
        [
            _timed("live", start),
            _timed("cancelled", start, remote_status="cancelled"),
            _timed("missing", start, remote_status="missing"),
        ],
        [],
        timezone="America/Bogota",
    )

    assert report.time_conflicts == ()


def test_tentative_events_still_count_as_overlaps() -> None:
    start = NOW.replace(hour=9, minute=0)
    report = detect_conflicts(
        [
            _timed("a", start),
            _timed("b", start, remote_status="tentative"),
        ],
        [],
        timezone="America/Bogota",
    )

    assert _pairs(report) == [("a", "b")]


def test_three_mutually_overlapping_events_report_each_pair_once() -> None:
    start = NOW.replace(hour=9, minute=0)
    report = detect_conflicts(
        [
            _timed("c", start + timedelta(minutes=20)),
            _timed("a", start),
            _timed("b", start + timedelta(minutes=10)),
        ],
        [],
        timezone="America/Bogota",
    )

    assert _pairs(report) == [("a", "b"), ("a", "c"), ("b", "c")]


def test_events_in_different_calendars_are_identified_separately() -> None:
    start = NOW.replace(hour=9, minute=0)
    report = detect_conflicts(
        [
            _timed("same", start, calendar_id="one"),
            _timed("same", start, calendar_id="two"),
        ],
        [],
        timezone="America/Bogota",
    )

    assert [(pair.a.calendar_id, pair.b.calendar_id) for pair in report.time_conflicts] == [
        ("one", "two")
    ]


def test_a_single_deadline_is_not_a_cluster() -> None:
    report = detect_conflicts(
        [], [_deadline(date(2026, 11, 2))], timezone="America/Bogota"
    )

    assert report.deadline_clashes == ()


def test_two_deadlines_on_the_same_day_cluster() -> None:
    day = date(2026, 11, 2)
    report = detect_conflicts(
        [],
        [_deadline(day), _deadline(day, tipo="entrega")],
        timezone="America/Bogota",
    )

    assert len(report.deadline_clashes) == 1
    clash = report.deadline_clashes[0]
    assert clash.date == day
    assert len(clash.items) == MIN_DEADLINES_PER_DAY
    assert {item.tipo for item in clash.items} == {"examen", "entrega"}


def test_deadlines_on_different_days_do_not_cluster() -> None:
    report = detect_conflicts(
        [],
        [_deadline(date(2026, 11, 2)), _deadline(date(2026, 11, 3))],
        timezone="America/Bogota",
    )

    assert report.deadline_clashes == ()


def test_due_at_is_grouped_by_the_user_local_day() -> None:
    # 02:00Z and 03:00Z on Nov 2 are both Nov 1 in America/Bogota (UTC-5).
    report = detect_conflicts(
        [],
        [
            _deadline(at=datetime(2026, 11, 2, 2, 0, tzinfo=UTC)),
            _deadline(at=datetime(2026, 11, 2, 3, 0, tzinfo=UTC)),
        ],
        timezone="America/Bogota",
    )

    assert [clash.date for clash in report.deadline_clashes] == [date(2026, 11, 1)]


def test_same_instant_in_another_timezone_lands_on_another_day() -> None:
    instant = datetime(2026, 11, 2, 2, 0, tzinfo=UTC)
    report = detect_conflicts(
        [],
        [_deadline(at=instant), _deadline(at=instant)],
        timezone="Europe/Madrid",
    )

    assert [clash.date for clash in report.deadline_clashes] == [date(2026, 11, 2)]


def test_non_deadline_types_do_not_cluster() -> None:
    day = date(2026, 11, 2)
    ignored = [tipo for tipo in ("recordatorio", "recomendacion")]
    assert set(ignored).isdisjoint(DEADLINE_TIPOS)
    report = detect_conflicts(
        [],
        [_deadline(day, tipo=tipo) for tipo in ignored],
        timezone="America/Bogota",
    )

    assert report.deadline_clashes == ()


def test_deadline_without_a_date_is_ignored() -> None:
    report = detect_conflicts(
        [], [_deadline(), _deadline()], timezone="America/Bogota"
    )

    assert report.deadline_clashes == ()


def test_overlap_and_clustering_are_reported_separately() -> None:
    start = NOW.replace(hour=9, minute=0)
    day = date(2026, 11, 2)
    report = detect_conflicts(
        [_timed("a", start), _timed("b", start + timedelta(minutes=30))],
        [_deadline(day), _deadline(day, tipo="tarea")],
        timezone="America/Bogota",
    )

    assert _pairs(report) == [("a", "b")]
    assert [clash.date for clash in report.deadline_clashes] == [day]
    # A deadline is not a timed block: it can never appear in `time_conflicts`.
    assert all(
        pair.a.event_id in {"a", "b"} and pair.b.event_id in {"a", "b"}
        for pair in report.time_conflicts
    )


def test_output_is_deterministic_regardless_of_input_order() -> None:
    start = NOW.replace(hour=9, minute=0)
    day = date(2026, 11, 2)
    events = [
        _timed("b", start + timedelta(minutes=10)),
        _timed("a", start),
        _timed("c", start + timedelta(minutes=20)),
    ]
    deadlines = [
        _deadline(day, task_id=UUID(int=2)),
        _deadline(day, task_id=UUID(int=1)),
    ]

    first = detect_conflicts(events, deadlines, timezone="America/Bogota")
    second = detect_conflicts(
        list(reversed(events)), list(reversed(deadlines)), timezone="America/Bogota"
    )

    assert _pairs(first) == _pairs(second)
    assert [clash.date for clash in first.deadline_clashes] == [
        clash.date for clash in second.deadline_clashes
    ]
    assert [item.task_id for item in first.deadline_clashes[0].items] == [
        item.task_id for item in second.deadline_clashes[0].items
    ]


class _NoTasksTable:
    """Session stub: only the catalog probe is needed for the missing-table path."""

    async def scalar(self, _statement: object) -> bool:
        return False


async def test_deadline_source_returns_empty_until_the_tasks_table_exists() -> None:
    source = SqlTaskDeadlineSource(cast(AsyncSession, _NoTasksTable()))

    deadlines = await source.list_deadlines(
        user_id=uuid4(),
        date_from=date(2026, 11, 1),
        date_to=date(2026, 11, 30),
        timezone="America/Bogota",
    )

    assert deadlines == []


# --- Manual sync cooldown (J3.8 review) ------------------------------------------------


def test_a_first_manual_sync_is_never_in_cooldown() -> None:
    assert manual_sync_cooldown_remaining(None, now=NOW) == 0


def test_cooldown_reports_the_seconds_left_in_the_window() -> None:
    last = NOW - timedelta(seconds=10)
    assert manual_sync_cooldown_remaining(last, now=NOW) == MANUAL_SYNC_COOLDOWN_SECONDS - 10


def test_cooldown_expires_exactly_at_the_window_boundary() -> None:
    last = NOW - timedelta(seconds=MANUAL_SYNC_COOLDOWN_SECONDS)
    assert manual_sync_cooldown_remaining(last, now=NOW) == 0


def test_cooldown_rounds_a_fraction_up_so_retry_after_is_never_zero() -> None:
    last = NOW - timedelta(seconds=MANUAL_SYNC_COOLDOWN_SECONDS - 0.5)
    assert manual_sync_cooldown_remaining(last, now=NOW) == 1


def test_cooldown_accepts_a_naive_timestamp_as_utc() -> None:
    last = (NOW - timedelta(seconds=5)).replace(tzinfo=None)
    assert manual_sync_cooldown_remaining(last, now=NOW) == MANUAL_SYNC_COOLDOWN_SECONDS - 5


def test_cooldown_never_reports_a_negative_remainder() -> None:
    last = NOW - timedelta(days=1)
    assert manual_sync_cooldown_remaining(last, now=NOW) == 0


def test_cooldown_is_capped_when_the_stamp_is_in_the_future() -> None:
    last = NOW + timedelta(days=1)
    assert manual_sync_cooldown_remaining(last, now=NOW) == MANUAL_SYNC_COOLDOWN_SECONDS

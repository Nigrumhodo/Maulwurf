"""U-S3-JF-05 support: calendar window, Google pagination and publication ordering (J3.7).

These are the deterministic parts of I-S3-JF-05: local-day window math (including DST),
the `events.list` query contract (expanded recurrences, deletions, no `syncToken` mixing,
typed failures for a non-object body), full pagination, event normalization (including an
unresolvable event timezone), half-given range anchoring, and the rule that a failed read
publishes nothing. No Google call, credential or secret is involved.
"""

import uuid
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.calendar_sync import (
    CALENDAR_SET_CHANGED,
    NEVER_SYNCED,
    NO_READ_CALENDARS,
    OUTSIDE_COVERAGE,
    STALE,
    CalendarEventInvalid,
    CalendarPaginationExhausted,
    CalendarRead,
    CalendarSyncResult,
    CalendarWindow,
    CalendarWindowInvalid,
    GoogleCalendarEventSource,
    PublicationResult,
    RemoteCalendarEvent,
    RemoteCancelledEvent,
    _resolve_window,
    build_list_params,
    coverage_status,
    parse_remote_event,
    sync_calendar_window,
)
from app.services.google_calendar import GoogleCalendarService

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)


def _timed(
    event_id: str, start: datetime, *, calendar_id: str = "primary"
) -> RemoteCalendarEvent:
    return RemoteCalendarEvent(
        calendar_id=calendar_id,
        event_id=event_id,
        title=event_id,
        start_at=start,
        end_at=start + timedelta(hours=1),
    )


def _window(days: int = 10) -> CalendarWindow:
    return CalendarWindow.from_dates(
        date(2026, 10, 1), date(2026, 10, days), timezone="UTC"
    )


# --- Window math -----------------------------------------------------------------------


def test_default_window_is_seven_back_and_sixty_forward_in_the_user_timezone() -> None:
    window = CalendarWindow.default(now=NOW, timezone="America/Bogota")

    # Bogota is UTC-5 all year: local midnight maps to 05:00Z.
    assert window.start == datetime(2026, 10, 24, 5, 0, tzinfo=UTC)
    assert window.end == datetime(2026, 12, 31, 5, 0, tzinfo=UTC)
    assert window.start_date == date(2026, 10, 24)
    assert window.end_date == date(2026, 12, 30)


def test_default_window_keeps_local_days_across_a_dst_change() -> None:
    # Madrid leaves CEST on 2026-10-25; the two boundaries must carry different offsets.
    window = CalendarWindow.default(
        now=datetime(2026, 10, 24, 12, 0, tzinfo=UTC), timezone="Europe/Madrid"
    )

    assert window.start == datetime(2026, 10, 16, 22, 0, tzinfo=UTC)  # +02:00
    assert window.end == datetime(2026, 12, 23, 23, 0, tzinfo=UTC)  # +01:00
    assert window.start_date == date(2026, 10, 17)
    assert window.end_date == date(2026, 12, 23)


def test_inverted_or_oversized_windows_are_rejected() -> None:
    with pytest.raises(CalendarWindowInvalid) as inverted:
        CalendarWindow.from_dates(date(2026, 10, 2), date(2026, 10, 1), timezone="UTC")
    assert inverted.value.field == "to"

    with pytest.raises(CalendarWindowInvalid) as oversized:
        CalendarWindow.from_dates(date(2020, 1, 1), date(2026, 1, 1), timezone="UTC")
    assert oversized.value.code == "window_too_wide"


def test_window_rejects_naive_instants() -> None:
    with pytest.raises(CalendarWindowInvalid):
        CalendarWindow(
            start=datetime(2026, 10, 1), end=datetime(2026, 10, 2), timezone="UTC"
        )


def test_a_lone_from_derives_the_end_from_it_instead_of_the_default_window() -> None:
    # 2027-03-01 is far beyond the default +60 days from NOW: completing the range from
    # today's default would invert it and answer a misleading "to before from".
    window = _resolve_window(
        date_from=date(2027, 3, 1), date_to=None, timezone="UTC", now=NOW
    )

    assert window.start_date == date(2027, 3, 1)
    assert window.end_date == date(2027, 4, 30)


def test_a_lone_to_derives_the_start_from_it_instead_of_the_default_window() -> None:
    window = _resolve_window(
        date_from=None, date_to=date(2026, 1, 15), timezone="UTC", now=NOW
    )

    assert window.start_date == date(2026, 1, 8)
    assert window.end_date == date(2026, 1, 15)


# --- Google query contract -------------------------------------------------------------


def test_list_params_expand_recurrences_and_include_deletions() -> None:
    params = build_list_params(
        time_min=datetime(2026, 10, 1, tzinfo=UTC),
        time_max=datetime(2026, 10, 2, tzinfo=UTC),
    )

    assert params["timeMin"] == "2026-10-01T00:00:00Z"
    assert params["timeMax"] == "2026-10-02T00:00:00Z"
    assert params["singleEvents"] == "true"
    assert params["showDeleted"] == "true"
    assert params["orderBy"] == "startTime"
    assert "syncToken" not in params


def test_sync_token_is_never_mixed_with_window_filters() -> None:
    with pytest.raises(ValueError, match="syncToken"):
        build_list_params(
            time_min=datetime(2026, 10, 1, tzinfo=UTC),
            time_max=datetime(2026, 10, 2, tzinfo=UTC),
            sync_token="incremental",
        )


class _PagedGoogle:
    """MockTransport handler that serves prepared pages and records every query."""

    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self._pages = pages
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        token = request.url.params.get("pageToken")
        index = 0 if token is None else int(token.removeprefix("page-"))
        return httpx.Response(200, json=self._pages[index])


async def _source(pages: list[dict[str, Any]], **kwargs: Any) -> tuple[_PagedGoogle, Any]:
    fake = _PagedGoogle(pages)
    service = GoogleCalendarService(
        access_token="test-token",
        base_url="https://calendar.test/v3",
        transport=httpx.MockTransport(fake.handler),
        max_retries=0,
    )
    return fake, GoogleCalendarEventSource(service, **kwargs)


async def test_pagination_follows_every_next_page_token() -> None:
    first = {
        "items": [
            {
                "id": "recurring_20261005T090000Z",
                "status": "confirmed",
                "summary": "Lecture",
                "start": {
                    "dateTime": "2026-10-05T09:00:00-05:00",
                    "timeZone": "America/Bogota",
                },
                "end": {
                    "dateTime": "2026-10-05T10:00:00-05:00",
                    "timeZone": "America/Bogota",
                },
            }
        ],
        "nextPageToken": "page-1",
    }
    second = {
        "items": [
            {
                "id": "g2",
                "status": "confirmed",
                "summary": "Exam",
                "start": {"date": "2026-10-06"},
                "end": {"date": "2026-10-07"},
            }
        ]
    }
    fake, source = await _source([first, second])
    window = _window()

    events = await source.list_events("primary", time_min=window.start, time_max=window.end)

    assert [event.event_id for event in events.events] == [
        "recurring_20261005T090000Z",
        "g2",
    ]
    assert events.events[0].start_at == datetime(2026, 10, 5, 14, 0, tzinfo=UTC)
    assert events.events[0].timezone == "America/Bogota"
    assert events.events[1].start_date == date(2026, 10, 6)
    assert events.events[1].end_date_exclusive == date(2026, 10, 7)
    assert events.cancelled == ()
    assert len(fake.requests) == 2
    assert all("syncToken" not in request.url.params for request in fake.requests)
    assert all(request.url.params["singleEvents"] == "true" for request in fake.requests)
    assert all(request.url.params["showDeleted"] == "true" for request in fake.requests)
    assert fake.requests[0].url.params.get("pageToken") is None
    assert fake.requests[1].url.params["pageToken"] == "page-1"


async def test_pagination_limit_fails_without_returning_a_partial_read() -> None:
    endless = {"items": [], "nextPageToken": "page-1"}
    _fake, source = await _source([endless, endless], max_pages=2)
    window = _window()

    with pytest.raises(CalendarPaginationExhausted):
        await source.list_events("primary", time_min=window.start, time_max=window.end)


class _RawPayloadService:
    """Bypasses `GoogleCalendarService` to pin the adapter's own payload validation."""

    def __init__(self, payload: object) -> None:
        self._payload = payload

    async def request(
        self, method: str, path: str, *, params: Mapping[str, Any] | None = None
    ) -> object:
        del method, path, params
        return self._payload


@pytest.mark.parametrize("payload", [[], None, "events"])
async def test_a_non_object_payload_fails_the_read_with_a_typed_error(
    payload: object,
) -> None:
    stub = _RawPayloadService(payload)
    source = GoogleCalendarEventSource(cast(GoogleCalendarService, stub))
    window = _window()

    with pytest.raises(CalendarEventInvalid) as error:
        await source.list_events("primary", time_min=window.start, time_max=window.end)

    assert error.value.code == "events_payload_invalid"


async def test_cancelled_instances_are_exposed_as_keys_and_active_instances_stay_distinct() -> None:
    items = [
        {
            "id": "series_20261005T090000Z",
            "status": "confirmed",
            "summary": "Weekly lecture",
            "recurringEventId": "series",
            "start": {"dateTime": "2026-10-05T09:00:00-05:00"},
            "end": {"dateTime": "2026-10-05T10:00:00-05:00"},
        },
        {
            "id": "series_20261012T090000Z",
            "status": "cancelled",
            "summary": "Weekly lecture",
            "recurringEventId": "series",
            # Google may omit the block of a cancelled instance: it must not fail the read.
        },
        {
            "id": "series_20261019T090000Z",
            "status": "confirmed",
            "summary": "Weekly lecture",
            "recurringEventId": "series",
            "start": {"dateTime": "2026-10-19T09:00:00-05:00"},
            "end": {"dateTime": "2026-10-19T10:00:00-05:00"},
        },
    ]
    _fake, source = await _source([{"items": items}])
    window = _window(31)

    read = await source.list_events("primary", time_min=window.start, time_max=window.end)

    assert [event.event_id for event in read.events] == [
        "series_20261005T090000Z",
        "series_20261019T090000Z",
    ]
    assert read.cancelled == (
        RemoteCancelledEvent(calendar_id="primary", event_id="series_20261012T090000Z"),
    )


# --- Event normalization ---------------------------------------------------------------


def test_timed_and_all_day_events_are_normalized_to_utc_and_exclusive_dates() -> None:
    timed = parse_remote_event(
        "primary",
        {
            "id": "g1",
            "status": "tentative",
            "summary": "Lab",
            "etag": '"abc"',
            "start": {
                "dateTime": "2026-10-05T09:00:00-05:00",
                "timeZone": "America/Bogota",
            },
            "end": {"dateTime": "2026-10-05T10:00:00-05:00", "timeZone": "America/Bogota"},
        },
    )
    all_day = parse_remote_event(
        "primary",
        {
            "id": "g2",
            "summary": None,
            "start": {"date": "2026-10-06"},
            "end": {"date": "2026-10-08"},
        },
    )

    assert isinstance(timed, RemoteCalendarEvent)
    assert timed.start_at == datetime(2026, 10, 5, 14, 0, tzinfo=UTC)
    assert timed.remote_status == "tentative"
    assert timed.etag == '"abc"'
    assert timed.start_date is None
    assert isinstance(all_day, RemoteCalendarEvent)
    assert all_day.title == ""
    assert all_day.start_date == date(2026, 10, 6)
    assert all_day.end_date_exclusive == date(2026, 10, 8)
    assert all_day.start_at is None
    assert all_day.remote_status == "confirmed"
    assert all_day.source == "google"


def test_cancelled_event_is_exposed_as_a_key_not_a_row() -> None:
    parsed = parse_remote_event(
        "primary",
        {"id": "gone", "status": "cancelled", "start": {"date": "2026-10-06"}},
    )

    # The publication only marks an existing linked row; it never inserts a cancelled one,
    # so no block is parsed or required here.
    assert parsed == RemoteCancelledEvent(calendar_id="primary", event_id="gone")


def test_an_unresolvable_event_timezone_is_stored_as_unknown() -> None:
    parsed = parse_remote_event(
        "primary",
        {
            "id": "g1",
            "start": {
                "dateTime": "2026-10-05T09:00:00-05:00",
                "timeZone": "Mars/Olympus_Mons",
            },
            "end": {"dateTime": "2026-10-05T10:00:00-05:00"},
        },
    )

    assert isinstance(parsed, RemoteCalendarEvent)
    assert parsed.timezone is None


@pytest.mark.parametrize(
    "item",
    [
        {"id": "", "start": {"date": "2026-10-06"}, "end": {"date": "2026-10-07"}},
        {"id": "g1", "start": {"date": "2026-10-06"}},
        # A naive instant is not RFC 3339: guessing a zone would misplace the event.
        {
            "id": "g2",
            "start": {"dateTime": "2026-10-06T09:00:00"},
            "end": {"dateTime": "2026-10-06T10:00:00"},
        },
        {
            "id": "g3",
            "start": {"dateTime": "2026-10-06T10:00:00Z"},
            "end": {"dateTime": "2026-10-06T09:00:00Z"},
        },
        {"id": "g4", "start": {"date": "2026-10-07"}, "end": {"date": "2026-10-07"}},
    ],
)
def test_malformed_events_fail_the_read(item: dict[str, Any]) -> None:
    with pytest.raises(CalendarEventInvalid):
        parse_remote_event("primary", item)


# --- Publication ordering --------------------------------------------------------------


class _RecordingMirror:
    def __init__(self) -> None:
        self.calls: list[list[RemoteCalendarEvent]] = []
        self.cancelled_calls: list[list[RemoteCancelledEvent]] = []

    async def replace_window(
        self,
        *,
        user_id: uuid.UUID,
        window: CalendarWindow,
        calendars: tuple[str, ...],
        events: Iterable[RemoteCalendarEvent],
        cancelled: Iterable[RemoteCancelledEvent] = (),
        synced_at: datetime,
    ) -> PublicationResult:
        del user_id, window, calendars, synced_at
        published = list(events)
        self.calls.append(published)
        self.cancelled_calls.append(list(cancelled))
        return PublicationResult(inserted=len(published), removed=0)


class _Source:
    def __init__(
        self,
        events: dict[str, list[RemoteCalendarEvent]],
        failures: set[str],
        cancelled: dict[str, list[RemoteCancelledEvent]] | None = None,
    ) -> None:
        self._events = events
        self._failures = failures
        self._cancelled = cancelled or {}
        self.calls: list[str] = []

    async def list_events(
        self, calendar_id: str, *, time_min: datetime, time_max: datetime
    ) -> CalendarRead:
        del time_min, time_max
        self.calls.append(calendar_id)
        if calendar_id in self._failures:
            raise CalendarEventInvalid("read_failed")
        return CalendarRead(
            events=tuple(self._events.get(calendar_id, [])),
            cancelled=tuple(self._cancelled.get(calendar_id, [])),
        )


async def test_failed_read_publishes_nothing() -> None:
    events = {"a": [_timed("g1", NOW, calendar_id="a")]}
    source = _Source(events, failures={"b"})
    mirror = _RecordingMirror()

    with pytest.raises(CalendarEventInvalid):
        await sync_calendar_window(
            cast(AsyncSession, object()),
            user_id=uuid.uuid4(),
            window=_window(),
            calendars=["a", "b"],
            source=source,
            mirror=mirror,
        )

    assert source.calls == ["a", "b"]
    assert mirror.calls == []


async def test_empty_calendar_selection_is_a_noop() -> None:
    source = _Source({}, failures=set())
    mirror = _RecordingMirror()

    result = await sync_calendar_window(
        cast(AsyncSession, object()),
        user_id=uuid.uuid4(),
        window=_window(),
        calendars=["", "  "],
        source=source,
        mirror=mirror,
    )

    assert source.calls == []
    assert mirror.calls == []
    assert result.inserted == 0
    assert result.calendars == ()


async def test_duplicate_keys_are_collapsed_before_publication() -> None:
    start = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
    source = _Source({"a": [_timed("g1", start), _timed("g1", start + timedelta(hours=2))]}, set())
    mirror = _RecordingMirror()

    result = await sync_calendar_window(
        cast(AsyncSession, object()),
        user_id=uuid.uuid4(),
        window=_window(),
        calendars=["a"],
        source=source,
        mirror=mirror,
        now=NOW,
    )

    assert mirror.calls == [[_timed("g1", start + timedelta(hours=2))]]
    assert result.inserted == 1
    assert result.last_synced_at == NOW


async def test_cancelled_keys_are_deduplicated_and_forwarded_to_the_mirror() -> None:
    cancelled = [
        RemoteCancelledEvent(calendar_id="a", event_id="gone"),
        RemoteCancelledEvent(calendar_id="a", event_id="gone"),
    ]
    source = _Source({}, set(), cancelled={"a": cancelled})
    mirror = _RecordingMirror()

    await sync_calendar_window(
        cast(AsyncSession, object()),
        user_id=uuid.uuid4(),
        window=_window(),
        calendars=["a"],
        source=source,
        mirror=mirror,
        now=NOW,
    )

    assert mirror.calls == [[]]
    assert mirror.cancelled_calls == [[RemoteCancelledEvent(calendar_id="a", event_id="gone")]]


# --- Coverage decision -----------------------------------------------------------------


def test_coverage_reports_never_synced_without_a_successful_publication() -> None:
    window = _window()
    ok, notice = coverage_status(
        read_calendars=["a"],
        coverage_from=None,
        coverage_to=None,
        coverage_calendars=[],
        last_synced_at=None,
        window=window,
        now=NOW,
    )
    assert (ok, notice) == (False, NEVER_SYNCED)


def test_coverage_flags_an_empty_selection_even_if_old_coverage_exists() -> None:
    window = _window()
    ok, notice = coverage_status(
        read_calendars=[],
        coverage_from=window.start,
        coverage_to=window.end,
        coverage_calendars=["a"],
        last_synced_at=NOW,
        window=window,
        now=NOW,
    )
    assert (ok, notice) == (False, NO_READ_CALENDARS)


def test_coverage_flags_a_changed_calendar_selection() -> None:
    window = _window()
    ok, notice = coverage_status(
        read_calendars=["a", "b"],
        coverage_from=window.start,
        coverage_to=window.end,
        coverage_calendars=["a"],
        last_synced_at=NOW,
        window=window,
        now=NOW,
    )
    assert (ok, notice) == (False, CALENDAR_SET_CHANGED)


def test_coverage_flags_a_partial_window() -> None:
    window = _window()
    ok, notice = coverage_status(
        read_calendars=["a"],
        coverage_from=window.start + timedelta(days=1),
        coverage_to=window.end,
        coverage_calendars=["a"],
        last_synced_at=NOW,
        window=window,
        now=NOW,
    )
    assert (ok, notice) == (False, OUTSIDE_COVERAGE)


def test_coverage_flags_a_stale_mirror() -> None:
    window = _window()
    ok, notice = coverage_status(
        read_calendars=["a"],
        coverage_from=window.start,
        coverage_to=window.end,
        coverage_calendars=["a"],
        last_synced_at=NOW - timedelta(hours=3),
        window=window,
        now=NOW,
    )
    assert (ok, notice) == (False, STALE)


def test_a_complete_fresh_window_is_covered() -> None:
    window = _window()
    ok, notice = coverage_status(
        read_calendars=["a"],
        coverage_from=window.start,
        coverage_to=window.end,
        coverage_calendars=["a"],
        last_synced_at=NOW - timedelta(minutes=30),
        window=window,
        now=NOW,
    )
    assert (ok, notice) == (True, None)


def test_sync_result_carries_what_was_published() -> None:
    result = CalendarSyncResult(
        window=_window(),
        calendars=("a",),
        inserted=2,
        removed=1,
        last_synced_at=NOW,
    )
    assert result.inserted == 2
    assert result.window.start_date == date(2026, 10, 1)


def test_mapping_items_are_accepted_by_the_parser() -> None:
    item: Mapping[str, Any] = {
        "id": "g1",
        "start": {"date": "2026-10-06"},
        "end": {"date": "2026-10-07"},
    }
    parsed = parse_remote_event("primary", item)
    assert parsed is not None and parsed.event_id == "g1"

"""J3.5 / I-S3-JF-03: `[Maulwurf]` Calendar event content.

These are the deterministic content cases of I-S3-JF-03. J3.4 owns the idempotent insert
(double click, lost response, concurrent retry → one event) and is not merged yet; this
suite pins what the job must send to Google once it calls the builder: the `[Maulwurf]`
prefix, the task details, an absolute authenticated reader link without audio or tokens,
all-day dates with an exclusive end, timed instants in RFC 3339 with an IANA zone, and the
confirmed `event_block` verbatim (the builder never invents a duration).
"""

import uuid
from datetime import UTC, date, datetime
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import pytest

from app.services.calendar_event import (
    EVENT_TITLE_PREFIX,
    CalendarEventContentError,
    TaskEventContent,
    TaskSegmentRef,
    build_event_content,
)

ORIGIN = "https://app.maulwurf.example"
AUDIO_ID = uuid.UUID("8f0c1f7e-7f3a-4c1e-9f4b-2b6f0d5a1c11")
SEGMENT_ID = uuid.UUID("a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d")


def _timed(**overrides: object) -> TaskEventContent:
    values: dict[str, object] = {
        "title": "Midterm exam — topics 1 to 4",
        "detail": "Includes trigonometric substitution",
        "subject_name": "Calculus II",
        "tipo": "examen",
        "timezone": "America/Bogota",
        "due_at": datetime(2026, 9, 21, 19, 0, tzinfo=UTC),
        "audio_id": AUDIO_ID,
        "segment": TaskSegmentRef(segment_id=SEGMENT_ID, ordinal=3),
        "event_start_at": datetime(2026, 9, 21, 15, 0, tzinfo=UTC),
        "event_end_at": datetime(2026, 9, 21, 16, 0, tzinfo=UTC),
    }
    values.update(overrides)
    return TaskEventContent(**values)  # type: ignore[arg-type]


def _all_day(**overrides: object) -> TaskEventContent:
    values: dict[str, object] = {
        "title": "Problem set 4",
        "detail": "Chapters 3 and 4",
        "subject_name": "Calculus II",
        "tipo": "entrega",
        "timezone": "America/Bogota",
        "due_date": date(2026, 9, 21),
        "audio_id": AUDIO_ID,
        "segment": TaskSegmentRef(segment_id=SEGMENT_ID, ordinal=7),
        "event_start_date": date(2026, 9, 21),
        "event_end_date_exclusive": date(2026, 9, 22),
    }
    values.update(overrides)
    return TaskEventContent(**values)  # type: ignore[arg-type]


def _error(task: TaskEventContent, *, origin: str = ORIGIN) -> CalendarEventContentError:
    with pytest.raises(CalendarEventContentError) as caught:
        build_event_content(task, public_origin=origin)
    return caught.value


def _link(description: str) -> str:
    marker = "https://"
    start = description.find(marker)
    assert start >= 0, description
    return description[start:]


# --- title and description -------------------------------------------------------------


def test_summary_always_carries_exactly_one_prefix() -> None:
    body = build_event_content(_timed(), public_origin=ORIGIN)

    assert body["summary"] == f"{EVENT_TITLE_PREFIX} Midterm exam — topics 1 to 4"
    assert body["summary"].count(EVENT_TITLE_PREFIX) == 1


def test_an_existing_prefix_is_not_duplicated() -> None:
    task = _timed(title="  [Maulwurf] Midterm exam  ")

    body = build_event_content(task, public_origin=ORIGIN)

    assert body["summary"] == f"{EVENT_TITLE_PREFIX} Midterm exam"


def test_description_carries_task_details_and_the_authenticated_link() -> None:
    body = build_event_content(_timed(), public_origin=ORIGIN)

    description = body["description"]
    assert "Calculus II · examen" in description
    assert "Fecha límite: 2026-09-21 14:00 (America/Bogota)" in description
    assert "Includes trigonometric substitution" in description
    expected = f"{ORIGIN}/biblioteca/{AUDIO_ID}?segment={SEGMENT_ID}"
    assert f"Ver segmento 3 en Maulwurf: {expected}" in description


def test_link_has_no_audio_or_tokens() -> None:
    body = build_event_content(_timed(), public_origin=ORIGIN)

    url = urlsplit(_link(body["description"]))
    assert url.scheme == "https"
    assert url.netloc == "app.maulwurf.example"
    assert url.path == f"/biblioteca/{AUDIO_ID}"
    assert parse_qs(url.query) == {"segment": [str(SEGMENT_ID)]}
    assert url.fragment == ""
    assert set(parse_qs(url.query)) == {"segment"}
    assert "token" not in body["description"].lower()
    assert "audio" not in body["description"].lower()


def test_unsigned_reader_link_without_a_segment_targets_the_class() -> None:
    task = _timed(segment=None)

    body = build_event_content(task, public_origin=ORIGIN)

    assert f"{ORIGIN}/biblioteca/{AUDIO_ID}" in body["description"]
    assert "?segment=" not in body["description"]


def test_manual_task_without_a_class_has_no_link() -> None:
    task = _all_day(audio_id=None, segment=None)

    body = build_event_content(task, public_origin=ORIGIN)

    assert "http://" not in body["description"]
    assert "https://" not in body["description"]
    assert "Ver " not in body["description"]
    assert f"{ORIGIN}/biblioteca/" not in body["description"]


def test_due_date_is_omitted_when_the_date_is_not_resolved() -> None:
    task = _all_day(due_date=None)

    body = build_event_content(task, public_origin=ORIGIN)

    assert "Fecha límite" not in body["description"]
    assert "00:00" not in body["description"]


def test_trailing_slash_in_the_origin_does_not_duplicate_the_separator() -> None:
    body = build_event_content(_timed(), public_origin=f"{ORIGIN}/")

    assert f"{ORIGIN}/biblioteca/" in body["description"]
    assert f"{ORIGIN}//biblioteca/" not in body["description"]


def test_empty_title_is_rejected() -> None:
    error = _error(_timed(title="   "))

    assert error.code == "title_missing"


# --- all-day vs timed ------------------------------------------------------------------


def test_all_day_uses_dates_with_an_exclusive_end() -> None:
    body = build_event_content(_all_day(), public_origin=ORIGIN)

    assert body["start"] == {"date": "2026-09-21"}
    assert body["end"] == {"date": "2026-09-22"}
    assert "dateTime" not in body["start"]
    assert "timeZone" not in body["start"]


def test_the_event_mode_comes_from_the_block_not_from_the_due_pair() -> None:
    # Due date (all-day deadline) but a timed agenda block: both are separate by contract.
    task = _all_day(
        due_at=None,
        event_start_at=datetime(2026, 9, 21, 23, 0, tzinfo=UTC),
        event_end_at=datetime(2026, 9, 21, 23, 25, tzinfo=UTC),
        event_start_date=None,
        event_end_date_exclusive=None,
    )

    body = build_event_content(task, public_origin=ORIGIN)

    assert body["start"] == {
        "dateTime": "2026-09-21T23:00:00Z",
        "timeZone": "America/Bogota",
    }
    assert body["end"] == {
        "dateTime": "2026-09-21T23:25:00Z",
        "timeZone": "America/Bogota",
    }
    assert "date" not in body["start"]
    assert "Fecha límite: 2026-09-21" in body["description"]


def test_timed_block_keeps_the_confirmed_duration_verbatim() -> None:
    task = _timed(
        event_start_at=datetime(2026, 9, 21, 15, 0, tzinfo=UTC),
        event_end_at=datetime(2026, 9, 21, 15, 25, tzinfo=UTC),
    )

    body = build_event_content(task, public_origin=ORIGIN)

    assert body["start"]["dateTime"] == "2026-09-21T15:00:00Z"
    assert body["end"]["dateTime"] == "2026-09-21T15:25:00Z"


def test_timed_block_is_normalized_to_the_same_instant() -> None:
    task = _timed(
        event_start_at=datetime(2026, 9, 21, 10, 0, tzinfo=ZoneInfo("America/Bogota")),
        event_end_at=datetime(2026, 9, 21, 11, 0, tzinfo=ZoneInfo("America/Bogota")),
    )

    body = build_event_content(task, public_origin=ORIGIN)

    assert body["start"]["dateTime"] == "2026-09-21T15:00:00Z"
    assert body["end"]["dateTime"] == "2026-09-21T16:00:00Z"
    assert body["start"]["timeZone"] == "America/Bogota"


def test_timed_block_preserves_the_instant_across_dst() -> None:
    # Both instants are 01:30 local on the US fall-back day: the first is EDT, the second EST.
    early = datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    late = datetime(2026, 11, 1, 6, 30, tzinfo=UTC)
    task = _timed(
        timezone="America/New_York",
        due_at=None,
        event_start_at=early,
        event_end_at=late,
    )

    body = build_event_content(task, public_origin=ORIGIN)

    assert body["start"]["dateTime"] == "2026-11-01T05:30:00Z"
    assert body["end"]["dateTime"] == "2026-11-01T06:30:00Z"
    assert body["start"]["timeZone"] == "America/New_York"


def test_missing_block_is_rejected_instead_of_using_a_default_duration() -> None:
    error = _error(_timed(event_start_at=None, event_end_at=None))

    assert error.code == "event_block_missing"


def test_incomplete_timed_block_is_rejected() -> None:
    error = _error(_timed(event_end_at=None))

    assert error.code == "event_block_invalid"


def test_all_day_pairs_and_timed_pairs_are_mutually_exclusive() -> None:
    error = _error(_all_day(event_start_at=datetime(2026, 9, 21, 15, tzinfo=UTC)))

    assert error.code == "event_block_invalid"


def test_all_day_end_must_be_exclusive_and_later() -> None:
    same_day = _error(_all_day(event_end_date_exclusive=date(2026, 9, 21)))
    inverted = _error(_all_day(event_end_date_exclusive=date(2026, 9, 20)))

    assert same_day.code == "event_block_invalid"
    assert inverted.code == "event_block_invalid"


def test_timed_end_must_be_later_than_start() -> None:
    error = _error(
        _timed(
            event_start_at=datetime(2026, 9, 21, 15, 0, tzinfo=UTC),
            event_end_at=datetime(2026, 9, 21, 15, 0, tzinfo=UTC),
        )
    )

    assert error.code == "event_block_invalid"


def test_timed_block_requires_aware_instants() -> None:
    error = _error(
        _timed(
            event_start_at=datetime(2026, 9, 21, 15, 0),
            event_end_at=datetime(2026, 9, 21, 16, 0),
        )
    )

    assert error.code == "event_block_invalid"


def test_timed_block_requires_an_iana_timezone() -> None:
    error = _error(_timed(timezone="-05:00"))

    assert error.code == "timezone_invalid"


def test_unknown_timezone_is_rejected_even_for_an_all_day_block() -> None:
    error = _error(_all_day(timezone="Mars/Olympus"))

    assert error.code == "timezone_invalid"


# --- due data and origin ---------------------------------------------------------------


def test_due_date_and_due_instant_are_mutually_exclusive() -> None:
    error = _error(_timed(due_date=date(2026, 9, 21)))

    assert error.code == "due_invalid"


def test_due_instant_must_be_aware() -> None:
    error = _error(_timed(due_at=datetime(2026, 9, 21, 14, 0)))

    assert error.code == "due_invalid"


def test_origin_must_be_a_bare_http_origin() -> None:
    for origin in ("", "app.maulwurf.example", "https://app.example/path", "https://a?token=1"):
        assert _error(_timed(), origin=origin).code == "public_origin_invalid"


# --- provider limits and determinism ---------------------------------------------------


def test_summary_is_capped_but_keeps_exactly_one_prefix() -> None:
    body = build_event_content(_timed(title="x" * 2_000), public_origin=ORIGIN)

    assert len(body["summary"]) == 1_024
    assert body["summary"].count(EVENT_TITLE_PREFIX) == 1
    assert body["summary"] == f"{EVENT_TITLE_PREFIX} " + "x" * (1_024 - 11)


def test_a_long_detail_is_shortened_but_the_reader_link_survives() -> None:
    body = build_event_content(_timed(detail="d" * 20_000), public_origin=ORIGIN)

    assert len(body["description"]) <= 8_192
    assert body["description"].endswith(f"?segment={SEGMENT_ID}")
    assert str(AUDIO_ID) in _link(body["description"])


def test_the_same_confirmed_version_always_renders_the_same_body() -> None:
    assert build_event_content(_timed(), public_origin=ORIGIN) == build_event_content(
        _timed(), public_origin=ORIGIN
    )


def test_a_segment_without_a_class_cannot_produce_a_reader_url() -> None:
    body = build_event_content(_timed(audio_id=None), public_origin=ORIGIN)

    assert "https://" not in body["description"]
    assert "?segment=" not in body["description"]

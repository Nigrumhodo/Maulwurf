"""Regression tests for the G6 review findings (calendar remote parsing, mirror, router)."""

from __future__ import annotations

import inspect
from datetime import timedelta

import pytest

from app.routers import calendar as calendar_router
from app.services import calendar_mirror, calendar_remote
from app.services.calendar_remote import parse_remote_event
from app.services.calendar_window import CalendarEventInvalid


def _item(start: str, end: str) -> dict[str, object]:
    return {
        "id": "e1",
        "status": "confirmed",
        "start": {"dateTime": start, "timeZone": "UTC"},
        "end": {"dateTime": end, "timeZone": "UTC"},
    }


def test_zero_duration_event_is_kept_with_minimal_span() -> None:
    event = parse_remote_event(
        "primary", _item("2026-10-01T10:00:00Z", "2026-10-01T10:00:00Z")
    )
    assert event.start_at is not None and event.end_at is not None  # type: ignore[union-attr]
    assert timedelta(0) < event.end_at - event.start_at  # type: ignore[union-attr]


def test_inverted_event_is_still_rejected() -> None:
    with pytest.raises(CalendarEventInvalid):
        parse_remote_event("primary", _item("2026-10-01T11:00:00Z", "2026-10-01T10:00:00Z"))


def test_dead_google_event_source_copy_removed() -> None:
    assert not hasattr(calendar_remote, "google_event_source")


def test_upsert_is_batched_below_asyncpg_parameter_cap() -> None:
    assert calendar_mirror._UPSERT_BATCH_SIZE * 13 < 32767
    assert "_UPSERT_BATCH_SIZE" in inspect.getsource(calendar_mirror.SqlCalendarMirror)


def test_router_maps_decryption_error() -> None:
    src = inspect.getsource(calendar_router)
    assert src.count("except DecryptionError") == 2
    assert "force=True" in src

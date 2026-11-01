"""A4.2 pure support: occurrence sequence, resend eligibility and the listing cursor.

The database behaviour (row creation, uniqueness, tenant filtering) is proven by
`tests/integration/test_notifications_endpoints.py` against PostgreSQL. These cases pin the
decisions that must not depend on the database: only an audited terminal delivery can be
resent, every manual repetition gets the next sequence inside its frozen identity, and the
opaque cursor never accepts a malformed or foreign position.
"""

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.errors import ApiError
from app.services.notification_center import (
    NotificationCursor,
    decode_cursor,
    encode_cursor,
    local_occurrence_date,
)
from app.services.notifications import (
    RESENDABLE_STATUSES,
    NotificationNotResendable,
    ensure_resendable,
    next_occurrence_sequence,
)

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
NOTIFICATION_ID = uuid.UUID("55555555-5555-4555-8555-555555555555")


def _row(status: str) -> SimpleNamespace:
    return SimpleNamespace(status=status)


def _cursor(**overrides: Any) -> NotificationCursor:
    values: dict[str, Any] = {"scheduled_at": NOW, "notification_id": NOTIFICATION_ID}
    values.update(overrides)
    return NotificationCursor(**values)


# --- Manual occurrence sequence -------------------------------------------------------


def test_the_first_manual_occurrence_uses_sequence_one() -> None:
    assert next_occurrence_sequence(None) == 1
    assert next_occurrence_sequence(0) == 1


def test_every_manual_repetition_increments_the_sequence() -> None:
    assert next_occurrence_sequence(1) == 2
    assert next_occurrence_sequence(41) == 42


@pytest.mark.parametrize("value", [-1, -10])
def test_a_negative_stored_sequence_fails_closed(value: int) -> None:
    with pytest.raises(ValueError):
        next_occurrence_sequence(value)


@pytest.mark.parametrize("value", [True, "1", 1.5])
def test_the_sequence_must_be_an_integer(value: object) -> None:
    with pytest.raises(ValueError):
        next_occurrence_sequence(value)  # type: ignore[arg-type]


# --- Resend eligibility ---------------------------------------------------------------


def test_only_terminal_audited_deliveries_can_be_resent() -> None:
    assert RESENDABLE_STATUSES == frozenset({"sent", "failed", "delivery_unknown"})
    for status in RESENDABLE_STATUSES:
        assert ensure_resendable(_row(status)) == status


@pytest.mark.parametrize("status", ["pending", "sending", "cancelled"])
def test_a_queued_in_flight_or_cancelled_occurrence_is_not_resendable(status: str) -> None:
    with pytest.raises(NotificationNotResendable):
        ensure_resendable(_row(status))


# --- Listing cursor -------------------------------------------------------------------


def test_the_cursor_round_trips_and_is_opaque() -> None:
    cursor = _cursor()
    encoded = encode_cursor(cursor)

    assert "2026-11-02" not in encoded
    assert decode_cursor(encoded) == cursor


def test_the_cursor_keeps_microseconds_and_the_uuid() -> None:
    precise = NOW + timedelta(microseconds=123456)
    assert decode_cursor(encode_cursor(_cursor(scheduled_at=precise))).scheduled_at == precise


@pytest.mark.parametrize(
    "value",
    [
        "",
        "not-base64!!",
        "eyJ2Ijo5OSwidCI6IjIwMjYtMTEtMDJUMTI6MDA6MDArMDA6MDAiLCJpIjoiNTU1NTU1NTUtNTU1NS00NTU1LTg1NTUtNTU1NTU1NTU1NTU1In0",
    ],
)
def test_a_malformed_or_unsupported_cursor_is_a_typed_422(value: str) -> None:
    with pytest.raises(ApiError) as exc:
        decode_cursor(value)
    assert exc.value.status_code == 422
    assert exc.value.code == "validation_failed"
    assert "cursor" in exc.value.details.get("fields", {})


def test_a_naive_cursor_timestamp_is_rejected() -> None:
    payload = {
        "v": 1,
        "t": "2026-11-02T12:00:00",
        "i": str(NOTIFICATION_ID),
    }
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()

    with pytest.raises(ApiError):
        decode_cursor(encoded)


def test_a_cursor_with_a_non_uuid_position_is_rejected() -> None:
    encoded = encode_cursor(_cursor())
    data = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    data["i"] = "not-a-uuid"
    tampered = base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    with pytest.raises(ApiError):
        decode_cursor(tampered)


# --- Local occurrence date ------------------------------------------------------------


def test_the_occurrence_uses_the_user_local_date() -> None:
    # 2026-11-03T02:30Z is still 2026-11-02 in Bogota (UTC-5).
    assert local_occurrence_date(
        datetime(2026, 11, 3, 2, 30, tzinfo=UTC), "America/Bogota"
    ) == datetime(2026, 11, 2).date()
    assert local_occurrence_date(
        datetime(2026, 11, 3, 2, 30, tzinfo=UTC), "UTC"
    ) == datetime(2026, 11, 3).date()

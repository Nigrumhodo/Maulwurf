"""U-S4-AN-01 (A4.1): the M8 notification statuses and valid transitions.

The pure state machine is exercised without PostgreSQL; the database CHECKs of the same
contract are asserted from the SQLAlchemy metadata and proven against a real database by
`tests/integration/test_s4_notifications_schema.py`. No provider is called and no secret or
delivery payload is involved.
"""

import re
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy import DateTime
from sqlalchemy.schema import CheckConstraint, Table

from app.models.notification import (
    DIGEST_TYPES,
    NOTIFICATION_STATUSES,
    NOTIFICATION_TYPES,
    REMINDER_TYPES,
    TERMINAL_NOTIFICATION_STATUSES,
    Notification,
)
from app.services.notifications import (
    LEASE_EXPIRED,
    NOTIFICATION_ERROR_CODES,
    RESPONSE_LOST,
    TRANSITIONS,
    NotificationLeaseInvalid,
    NotificationNotDue,
    NotificationTransitionError,
    NotificationUnlinked,
    cancel,
    claim,
    ensure_transition,
    expire_lease,
    is_due,
    lease_expired,
    mark_delivery_unknown,
    mark_failed,
    mark_sent,
    redact_error_code,
)

NOTIFICATIONS = cast(Table, Notification.__table__)
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


def _row(status: str = "pending", **overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "status": status,
        "type": "reminder_t24",
        "task_id": uuid.UUID("33333333-3333-4333-8333-333333333333"),
        "scheduled_at": NOW - timedelta(minutes=1),
        "attempts": 0,
        "lease_expires_at": None,
        "sent_at": None,
        "provider_message_id": None,
        "last_error_code": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _check(table: Table, name: str) -> str:
    expected = f"ck_{table.name}_{name}"
    for constraint in table.constraints:
        if isinstance(constraint, CheckConstraint) and constraint.name == expected:
            return " ".join(str(constraint.sqltext).split())
    raise AssertionError(f"CHECK {expected!r} not found on {table.name}")


def _index(table: Table, name: str) -> tuple[tuple[str, ...], str | None]:
    for index in table.indexes:
        if index.name == name:
            where = index.dialect_options["postgresql"].get("where")
            return (
                tuple(column.name for column in index.columns),
                None if where is None else " ".join(str(where).split()),
            )
    raise AssertionError(f"index {name!r} not found on {table.name}")


# --- Vocabularies ----------------------------------------------------------------------


def test_the_status_vocabulary_is_exactly_the_frozen_m8_set() -> None:
    assert set(NOTIFICATION_STATUSES) == {
        "pending",
        "sending",
        "sent",
        "failed",
        "delivery_unknown",
        "cancelled",
    }
    assert "retry" not in NOTIFICATION_STATUSES
    # The DB CHECK and the pure machine must never drift apart.
    sql = _check(NOTIFICATIONS, "status")
    assert set(re.findall(r"'([a-z_]+)'", sql)) == set(NOTIFICATION_STATUSES)
    assert set(TRANSITIONS) == set(NOTIFICATION_STATUSES)


def test_the_occurrence_types_match_the_frozen_m8_vocabulary() -> None:
    assert set(NOTIFICATION_TYPES) == {
        "reminder_t48",
        "reminder_t24",
        "reminder_t2",
        "reminder_t3d",
        "reminder_t1d",
        "digest_daily",
        "digest_weekly",
        "test",
    }
    assert set(REMINDER_TYPES) == set(NOTIFICATION_TYPES) - set(DIGEST_TYPES) - {"test"}
    assert set(DIGEST_TYPES) == {"digest_daily", "digest_weekly"}
    sql = _check(NOTIFICATIONS, "type")
    assert set(re.findall(r"'([a-z_0-9]+)'", sql)) == set(NOTIFICATION_TYPES)


def test_terminal_states_are_the_non_retryable_delivery_history() -> None:
    assert set(TERMINAL_NOTIFICATION_STATUSES) == {
        "sent",
        "failed",
        "delivery_unknown",
        "cancelled",
    }
    for status in TERMINAL_NOTIFICATION_STATUSES:
        assert TRANSITIONS[status] == frozenset()


# --- Transitions -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("pending", "sending"),
        ("pending", "failed"),
        ("pending", "cancelled"),
        ("sending", "sent"),
        ("sending", "failed"),
        ("sending", "delivery_unknown"),
        # Idempotent self-transitions are accepted no-ops.
        *[(status, status) for status in NOTIFICATION_STATUSES],
    ],
)
def test_valid_transitions_are_accepted(current: str, target: str) -> None:
    assert ensure_transition(current, target) == target


@pytest.mark.parametrize(
    ("current", "target"),
    [
        # No automatic retry: an uncertain or known delivery never goes back.
        ("delivery_unknown", "pending"),
        ("delivery_unknown", "sending"),
        ("failed", "pending"),
        ("failed", "sending"),
        ("sent", "pending"),
        ("sent", "sending"),
        # An in-flight send may have reached Gmail: it cannot be cancelled.
        ("sending", "cancelled"),
        ("sending", "pending"),
        # A cancelled occurrence is history.
        ("cancelled", "pending"),
        ("cancelled", "sending"),
        # Pending cannot jump straight to a delivered/uncertain state.
        ("pending", "sent"),
        ("pending", "delivery_unknown"),
    ],
)
def test_invalid_transitions_are_rejected(current: str, target: str) -> None:
    with pytest.raises(NotificationTransitionError) as failure:
        ensure_transition(current, target)
    assert failure.value.current == current
    assert failure.value.target == target


def test_an_unknown_status_fails_closed_instead_of_widening_the_machine() -> None:
    with pytest.raises(NotificationTransitionError):
        ensure_transition("queued", "pending")
    with pytest.raises(NotificationTransitionError):
        ensure_transition("pending", "queued")


# --- Due state and claim ---------------------------------------------------------------


def test_an_occurrence_is_due_only_from_its_scheduled_instant() -> None:
    assert is_due(_row(scheduled_at=NOW - timedelta(seconds=1)), now=NOW) is True
    assert is_due(_row(scheduled_at=NOW), now=NOW) is True
    assert is_due(_row(scheduled_at=NOW + timedelta(seconds=1)), now=NOW) is False
    assert is_due(_row("sending", scheduled_at=NOW - timedelta(hours=1)), now=NOW) is False


def test_a_claim_leases_a_due_occurrence_and_counts_the_attempt() -> None:
    row = _row()
    assert claim(row, now=NOW, lease_expires_at=NOW + timedelta(minutes=5)) == "sending"
    assert row.status == "sending"
    assert row.attempts == 1
    assert row.lease_expires_at == NOW + timedelta(minutes=5)


def test_a_claim_before_the_scheduled_instant_is_rejected() -> None:
    row = _row(scheduled_at=NOW + timedelta(minutes=1))
    with pytest.raises(NotificationNotDue):
        claim(row, now=NOW, lease_expires_at=NOW + timedelta(minutes=5))
    assert (row.status, row.attempts, row.lease_expires_at) == ("pending", 0, None)


def test_a_claim_requires_a_lease_that_covers_the_claim() -> None:
    for lease in (NOW, NOW - timedelta(seconds=1)):
        row = _row()
        with pytest.raises(NotificationLeaseInvalid):
            claim(row, now=NOW, lease_expires_at=lease)
        assert (row.status, row.attempts, row.lease_expires_at) == ("pending", 0, None)


def test_a_reminder_whose_task_was_deleted_is_never_delivered() -> None:
    # `ON DELETE SET NULL (task_id)` keeps the delivery history; it must not become a
    # sendable orphan whose task no longer exists.
    row = _row(task_id=None)
    with pytest.raises(NotificationUnlinked):
        claim(row, now=NOW, lease_expires_at=NOW + timedelta(minutes=5))
    assert (row.status, row.attempts) == ("pending", 0)


def test_a_task_less_digest_is_still_deliverable() -> None:
    row = _row(type="digest_daily", task_id=None)
    assert claim(row, now=NOW, lease_expires_at=NOW + timedelta(minutes=5)) == "sending"


def test_a_claimed_row_cannot_be_claimed_again() -> None:
    row = _row("sending", lease_expires_at=NOW + timedelta(minutes=5), attempts=1)
    with pytest.raises(NotificationTransitionError):
        claim(row, now=NOW, lease_expires_at=NOW + timedelta(minutes=10))
    assert row.attempts == 1


# --- Lease expiry ----------------------------------------------------------------------


def test_an_expired_lease_is_uncertain_delivery_without_automatic_retry() -> None:
    row = _row("sending", lease_expires_at=NOW - timedelta(seconds=1), attempts=1)
    assert lease_expired(row, now=NOW) is True
    assert expire_lease(row, now=NOW) is True
    assert row.status == "delivery_unknown"
    assert row.last_error_code == LEASE_EXPIRED
    assert expire_lease(row, now=NOW) is False  # terminal: no second expiry
    with pytest.raises(NotificationTransitionError):
        ensure_transition(row.status, "pending")


def test_a_live_lease_is_not_expired() -> None:
    row = _row("sending", lease_expires_at=NOW + timedelta(seconds=1), attempts=1)
    assert lease_expired(row, now=NOW) is False
    assert expire_lease(row, now=NOW) is False
    assert row.status == "sending"


def test_a_sending_row_without_a_lease_fails_closed_as_uncertain() -> None:
    # The DB CHECK forbids this shape; if it is ever seen, delivery cannot be proven and the
    # safe destination is delivery_unknown, never a new pending/retry.
    row = _row("sending")
    assert lease_expired(row, now=NOW) is True
    assert expire_lease(row, now=NOW) is True
    assert row.status == "delivery_unknown"


@pytest.mark.parametrize("status", ["pending", "sent", "failed", "delivery_unknown", "cancelled"])
def test_only_sending_has_an_expirable_lease(status: str) -> None:
    row = _row(status)
    assert lease_expired(row, now=NOW) is False
    assert expire_lease(row, now=NOW) is False
    assert row.status == status


# --- Delivery outcomes -----------------------------------------------------------------


def test_marking_sent_records_the_delivery_time_and_provider_message() -> None:
    row = _row("sending", lease_expires_at=NOW + timedelta(minutes=5), attempts=1)
    assert mark_sent(row, now=NOW, provider_message_id="gmail-msg-1") == "sent"
    assert row.sent_at == NOW
    assert row.provider_message_id == "gmail-msg-1"
    assert row.last_error_code is None


def test_a_sent_row_requires_a_provider_message_id() -> None:
    for value in ("", "   ", "\t\n"):
        row = _row("sending", lease_expires_at=NOW + timedelta(minutes=5))
        with pytest.raises(ValueError):
            mark_sent(row, now=NOW, provider_message_id=value)
        assert (row.status, row.sent_at, row.provider_message_id) == ("sending", None, None)


def test_only_a_sending_row_can_be_marked_sent() -> None:
    with pytest.raises(NotificationTransitionError):
        mark_sent(_row(), now=NOW, provider_message_id="gmail-msg-1")


def test_a_lost_response_is_delivery_unknown_with_a_redacted_code() -> None:
    row = _row("sending", lease_expires_at=NOW + timedelta(minutes=5))
    assert mark_delivery_unknown(row) == "delivery_unknown"
    assert row.last_error_code == RESPONSE_LOST
    assert row.sent_at is None
    assert row.provider_message_id is None


def test_a_known_failure_keeps_a_redacted_code_and_never_sends() -> None:
    row = _row("pending")
    assert mark_failed(row, error_code="provider_rejected") == "failed"
    assert row.status == "failed"
    assert row.last_error_code == "provider_rejected"
    assert row.sent_at is None


def test_a_provider_payload_never_reaches_the_error_column() -> None:
    row = _row("sending", lease_expires_at=NOW + timedelta(minutes=5))
    mark_failed(row, error_code="Bob <bob@example.test> rejected: body was 'Secret'")
    assert row.last_error_code == "unknown"
    assert "@" not in str(row.last_error_code)
    assert " " not in str(row.last_error_code)


def test_only_pending_or_sending_can_fail() -> None:
    for status in ("sent", "failed", "delivery_unknown", "cancelled"):
        with pytest.raises(NotificationTransitionError):
            mark_failed(_row(status), error_code="provider_rejected")


def test_only_pending_occurrences_can_be_cancelled() -> None:
    row = _row("pending")
    assert cancel(row) == "cancelled"
    assert row.status == "cancelled"
    for status in ("sending", "sent", "failed", "delivery_unknown", "cancelled"):
        with pytest.raises(NotificationTransitionError):
            cancel(_row(status))


# --- Redaction -------------------------------------------------------------------------


def test_the_error_vocabulary_is_a_closed_set_of_tokens() -> None:
    assert "unknown" in NOTIFICATION_ERROR_CODES
    assert all(re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code) for code in NOTIFICATION_ERROR_CODES)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("provider_rejected", "provider_rejected"),
        (" PROVIDER_REJECTED ", "provider_rejected"),
        ("lease_expired", "lease_expired"),
        ("", "unknown"),
        ("   ", "unknown"),
        ("Bob <bob@example.test>", "unknown"),
        ("failed: secret transcript text", "unknown"),
        ("x" * 65, "unknown"),
    ],
)
def test_redact_error_code_never_passes_free_text(value: str | None, expected: str | None) -> None:
    assert redact_error_code(value) == expected


# --- Database coherence ----------------------------------------------------------------


def test_the_lease_check_separates_live_claims_from_history() -> None:
    sql = _check(NOTIFICATIONS, "lease")
    assert "status = 'sending' AND lease_expires_at IS NOT NULL" in sql
    assert "status IN ('pending', 'cancelled') AND lease_expires_at IS NULL" in sql
    assert "status IN ('sent', 'failed', 'delivery_unknown')" in sql


def test_a_sent_row_must_carry_its_delivery_time_and_message() -> None:
    assert _check(NOTIFICATIONS, "sent_at") == "(status = 'sent') = (sent_at IS NOT NULL)"
    assert _check(NOTIFICATIONS, "provider_message_id") == (
        "provider_message_id IS NULL OR status = 'sent'"
    )


def test_only_failed_and_unknown_deliveries_carry_an_error_code() -> None:
    assert _check(NOTIFICATIONS, "error_code") == (
        "last_error_code IS NULL OR status IN ('failed', 'delivery_unknown')"
    )
    assert _check(NOTIFICATIONS, "attempts") == "attempts >= 0"
    assert _check(NOTIFICATIONS, "last_error_code") == (
        "last_error_code IS NULL OR last_error_code ~ '^[a-z][a-z0-9_]{0,63}$'"
    )


def test_task_types_carry_coherent_task_and_generation_pairs() -> None:
    sql = _check(NOTIFICATIONS, "type_task")
    # Digests/test sends never belong to a task.
    assert "type IN ('digest_daily', 'digest_weekly', 'test')" in sql
    assert "task_id IS NULL AND due_version IS NULL" in sql
    # A reminder always carries its task and generation; the task FK cascades on delete.
    assert "AND task_id IS NOT NULL AND due_version IS NOT NULL" in sql
    assert "AND task_id IS NULL AND due_version IS NULL" in sql
    assert _check(NOTIFICATIONS, "due_version") == (
        "due_version IS NULL OR due_version >= 1"
    )


def test_the_pending_claim_index_is_partial_and_due_ordered() -> None:
    assert _index(NOTIFICATIONS, "notifications_due") == (
        ("status", "scheduled_at"),
        "status = 'pending'",
    )
    assert _index(NOTIFICATIONS, "notifications_by_task") == (("user_id", "task_id"), None)


def test_no_notification_column_stores_audio_text_or_provider_payloads() -> None:
    assert NOTIFICATIONS.c.dedupe_key.nullable is False
    for column in ("scheduled_at", "type", "dedupe_key", "status"):
        assert NOTIFICATIONS.c[column].nullable is False
    forbidden = {
        "audio",
        "audio_bytes",
        "storage_key",
        "transcript_text",
        "body",
        "payload",
        "error",
    }
    assert forbidden.isdisjoint(NOTIFICATIONS.c.keys())


def test_the_lease_and_schedule_columns_are_instants_not_days() -> None:
    for column in ("scheduled_at", "lease_expires_at", "sent_at", "created_at", "updated_at"):
        assert cast(DateTime, NOTIFICATIONS.c[column].type).timezone is True
    assert NOTIFICATIONS.c.occurrence.type.__class__.__name__ == "Date"
    assert NOTIFICATIONS.c.occurrence.nullable is True
    assert NOTIFICATIONS.c.due_version.nullable is True
    assert NOTIFICATIONS.c.task_id.nullable is True

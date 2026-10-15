"""U-S2-AN-04: tombstone/fencing decisions without Postgres — late writes are rejected.

A2.7 (S2.md §A2.7 on ESPECIFICACION.md M2 and DISENO §3.5–§3.6): which states the explicit
cancel may end, the tombstone's stronger rewrite, the fencing decision A2.10's atomic
commit will reuse, and the typed `409 google_deletion_unavailable` of the explicit remote
option. Pure functions and `SimpleNamespace` doubles, following `test_state_machine.py`;
the SQL side (conditional UPDATE, purge counts) is proven against Postgres by
`tests/integration/test_delete_audio.py` (I-S2-AN-06). No `integration` marker.
"""

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.errors import ApiError, google_deletion_unavailable
from app.models.ingestion import ACTIVE_INGESTION_STATES
from app.routers.audios import AudioDelete
from app.services import state_machine
from app.services.state_machine import (
    TERMINAL_ATTEMPT_STATES,
    late_write_allowed,
    tombstone_target,
)

CANCELLABLE_PRE_COMMIT = ("awaiting_upload", "receiving", "transcribing")


def attempt(status: str, **extra: Any) -> Any:
    """Duck-typed `ingestion_attempts` row: only the attributes the machine touches."""
    fields: dict[str, Any] = {"status": status, "error_code": None}
    fields.update(extra)
    return SimpleNamespace(**fields)


# --- A. Explicit cancel: legal sources and honest finals --------------------------------


@pytest.mark.parametrize("status", CANCELLABLE_PRE_COMMIT)
def test_explicit_cancel_is_legal_before_the_commit(status: str) -> None:
    row = attempt(status)
    assert state_machine.transition(row, "cancelled") == "cancelled"
    assert row.status == "cancelled"
    assert row.error_code is None  # an explicit cancel carries no error code (M2)


def test_cancel_after_the_commit_conflicts_and_keeps_the_transcript() -> None:
    # The commit is the point of no return: cancelling must never destroy its transcript,
    # so the machine answers 409 with both statuses and writes nothing.
    row = attempt("transcript_committed_cleanup_pending")
    with pytest.raises(ApiError) as excinfo:
        state_machine.ensure_transition(row.status, "cancelled")
    error = excinfo.value
    assert (error.status_code, error.code) == (409, "invalid_transition")
    assert error.details == {
        "current_status": "transcript_committed_cleanup_pending",
        "attempted_status": "cancelled",
    }
    assert row.status == "transcript_committed_cleanup_pending"


@pytest.mark.parametrize("final", sorted(set(TERMINAL_ATTEMPT_STATES) - {"cancelled"}))
def test_cancel_on_a_final_conflicts_without_rewriting_its_cause(final: str) -> None:
    row = attempt(final, error_code="original")
    with pytest.raises(ApiError) as excinfo:
        state_machine.ensure_transition(row.status, "cancelled")
    assert excinfo.value.code == "invalid_transition"
    assert row.error_code == "original"  # the history of the loss is never falsified


def test_cancel_on_cancelled_is_a_real_noop() -> None:
    # H1 (review #65, fixed in A2.7): the endpoint answers an idempotent 202 and the
    # no-op rewrites nothing — not even when a caller passes a new error_code.
    row = attempt("cancelled", error_code="upload_expired")
    assert state_machine.transition(row, "cancelled", error_code="otro") == "cancelled"
    assert row.error_code == "upload_expired"


# --- B. Tombstone: stronger than cancel, still honest ----------------------------------


def test_tombstone_ends_every_active_state_including_the_committed_one() -> None:
    for status in ACTIVE_INGESTION_STATES:
        assert tombstone_target(status) == "cancelled"


@pytest.mark.parametrize("final", TERMINAL_ATTEMPT_STATES)
def test_tombstone_preserves_final_states(final: str) -> None:
    assert tombstone_target(final) is None
    assert tombstone_target("nonsense") is None  # unknown statuses fail closed


# --- C. Fencing: a late write with a stale token never passes ---------------------------


def test_late_write_passes_only_with_the_current_token_on_an_active_attempt() -> None:
    assert (
        late_write_allowed(current_token=3, presented_token=3, attempt_status="transcribing")
        is True
    )


def test_late_write_is_rejected_after_the_token_was_invalidated() -> None:
    # Cancel and tombstone bump `fencing_token`; the old owner's commit must be refused.
    assert (
        late_write_allowed(current_token=4, presented_token=3, attempt_status="transcribing")
        is False
    )


def test_late_write_is_rejected_on_a_non_active_attempt() -> None:
    for status in ("cancelled", "rejected", "requires_reupload", "succeeded", "nonsense"):
        assert (
            late_write_allowed(current_token=1, presented_token=1, attempt_status=status)
            is False
        )


# --- D. The explicit remote option is frozen until S3/F2 --------------------------------


def test_remote_google_deletion_is_typed_and_frozen() -> None:
    error = google_deletion_unavailable()
    assert (error.status_code, error.code) == (409, "google_deletion_unavailable")
    assert error.details == {}  # no remote ids or provider data in the payload (M9)


def test_delete_body_defaults_to_keeping_google_events_and_forbids_extra_fields() -> None:
    # The contract freezes the option's name (DISENO §3.5); unknown fields would silently
    # change what a delete means, so they are rejected instead of ignored.
    assert AudioDelete().delete_google_events is False
    with pytest.raises(ValidationError):
        AudioDelete.model_validate({"delete_google_events": False, "force": True})

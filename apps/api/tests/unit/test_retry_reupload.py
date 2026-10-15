"""U-S2-AN-08 (pure side): retry-vs-reupload decisions without Postgres.

A2.8 (S2.md §A2.8 on ESPECIFICACION.md M2 and DISENO §3.5): which stage lists the matrix
accepts, what a retry does with each run state (`create`/`reset`/`reuse`/`conflict`), when a
reupload is refused (`409 transcript_unavailable|invalid_transition`), and the body shape
that answers `422 validation_failed`. Pure functions and Pydantic models, following
`test_cancel_tombstone.py`; the SQL side (idempotent runs, outbox dedupe, new attempt) is
proven against Postgres by `tests/integration/test_retry_reupload.py`. No `integration`
marker.
"""

import uuid
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.errors import ApiError
from app.models.ingestion import ACTIVE_INGESTION_STATES, CLEANUP_GATED_EVENT_TYPES
from app.routers.audios import AudioRetry
from app.services import state_machine
from app.services.runs import (
    RETRYABLE_RUN_STATES,
    STAGE_EVENT_TYPE,
    ReuploadReservation,
    retry_run_action,
    reupload_conflict,
    stage_event_type,
    validate_retry_stages,
)


def validate(stages: list[str], version: int, *, active: int | None = 1) -> None:
    validate_retry_stages(
        stages, version, active_transcript_version=active, required_stages=["index", "analyze"]
    )


# --- A. Stage lists: shape first, then usable text, then what is owed --------------------


@pytest.mark.parametrize("stages", [["index"], ["analyze"], ["index", "analyze"]])
def test_valid_stage_lists_pass(stages: list[str]) -> None:
    validate(stages, 1)


@pytest.mark.parametrize("stages", [[], ["index", "index"], ["asr"]])
def test_bad_stage_lists_fail_closed_as_422(stages: list[str]) -> None:
    with pytest.raises(ApiError) as excinfo:
        validate(stages, 1)
    error = excinfo.value
    assert (error.status_code, error.code) == (422, "validation_failed")
    assert "fields" in error.details


def test_shape_is_checked_before_the_text() -> None:
    # An empty list against a missing transcript is still a 422: the request is malformed,
    # not conflicted, so the answer must not depend on the class's data.
    with pytest.raises(ApiError) as excinfo:
        validate([], 1, active=None)
    assert excinfo.value.status_code == 422


def test_no_usable_transcript_is_transcript_unavailable() -> None:
    with pytest.raises(ApiError) as excinfo:
        validate(["index"], 1, active=None)
    error = excinfo.value
    assert (error.status_code, error.code) == (409, "transcript_unavailable")
    assert error.details == {"active_transcript_version": None}


def test_obsolete_version_conflicts_and_reports_the_active_one() -> None:
    # The caller asked to rerun text that is no longer the usable one: the response hands
    # back the active version so the UI can refresh instead of guessing.
    with pytest.raises(ApiError) as excinfo:
        validate(["index"], 2, active=1)
    error = excinfo.value
    assert (error.status_code, error.code) == (409, "transcript_unavailable")
    assert error.details == {"active_transcript_version": 1}


def test_stage_not_required_conflicts_with_the_owed_stages() -> None:
    # `analyze` is not owed when the class requires only `index`: retrying it would queue
    # work the class never had.
    with pytest.raises(ApiError) as excinfo:
        validate_retry_stages(
            ["analyze"], 1, active_transcript_version=1, required_stages=["index"]
        )
    error = excinfo.value
    assert (error.status_code, error.code) == (409, "invalid_transition")
    assert error.details == {"stage": "analyze", "required_stages": ["index"]}


# --- B. Run states: exactly one honest action per state ---------------------------------


@pytest.mark.parametrize(
    ("status", "action"),
    [
        (None, "create"),
        ("failed", "reset"),
        ("cancelled", "reset"),
        ("pending", "reuse"),
        ("running", "reuse"),
        ("succeeded", "conflict"),
    ],
)
def test_retry_run_action_maps_every_known_state(status: str | None, action: str) -> None:
    assert retry_run_action(status) == action


def test_unknown_run_status_fails_closed() -> None:
    # Corrupt data is never widened into a legal action.
    with pytest.raises(ValueError):
        retry_run_action("nonsense")


def test_only_failed_and_cancelled_are_retryable() -> None:
    assert set(RETRYABLE_RUN_STATES) == {"failed", "cancelled"}
    assert set(RETRYABLE_RUN_STATES) <= set(state_machine.TERMINAL_STAGE_STATES)


def test_every_run_state_has_an_action() -> None:
    for status in state_machine.RUN_STAGE_STATES:
        assert retry_run_action(status) in ("create", "reset", "reuse", "conflict")


# --- C. Reupload guard: only when the text is gone and no session is live ---------------


@pytest.mark.parametrize("status", ("requires_reupload", "rejected", "cancelled"))
def test_reupload_is_allowed_on_a_final_attempt_without_transcript(status: str) -> None:
    reupload_conflict(attempt_status=status, has_usable_transcript=False)  # no raise


def test_reupload_conflicts_when_a_transcript_already_exists() -> None:
    # The button must say «retry analysis/index», never «upload again» (DISENO §3.5).
    with pytest.raises(ApiError) as excinfo:
        reupload_conflict(attempt_status="succeeded", has_usable_transcript=True)
    error = excinfo.value
    assert (error.status_code, error.code) == (409, "invalid_transition")
    assert error.details == {"current_status": "succeeded", "reason": "transcript_available"}


@pytest.mark.parametrize("status", ACTIVE_INGESTION_STATES)
def test_reupload_conflicts_while_any_attempt_is_active(status: str) -> None:
    # A class keeps at most one live attempt (attempts_one_active); reupload cannot open
    # a second session on top of a running one.
    with pytest.raises(ApiError) as excinfo:
        reupload_conflict(attempt_status=status, has_usable_transcript=False)
    assert excinfo.value.details == {"current_status": status}


def test_reupload_conflicts_on_committed_cleanup_pending_even_with_transcript() -> None:
    # `transcript_committed_cleanup_pending` is active, so it blocks reupload in every
    # combination: cleanup is not verified and the text may still land.
    with pytest.raises(ApiError):
        reupload_conflict(
            attempt_status="transcript_committed_cleanup_pending", has_usable_transcript=True
        )


def test_reupload_guard_never_invents_a_conflict_on_an_unknown_status() -> None:
    # An unknown status is not in the active list, so the guard stays silent; the SQL
    # side still requires a previous attempt to copy the consent snapshot from.
    reupload_conflict(attempt_status="nonsense", has_usable_transcript=False)


# --- D. Stage -> outbox type drift (the dispatcher's gate must cover both stages) -------


def test_stage_event_types_are_the_cleanup_gated_ones() -> None:
    # Retry emits enabled events; the dispatcher only publishes them once cleanup is
    # verified, and that gate is keyed by these exact types.
    assert set(STAGE_EVENT_TYPE.values()) == set(CLEANUP_GATED_EVENT_TYPES)
    assert stage_event_type("index") == "index_requested"
    assert stage_event_type("analyze") == "analyze_requested"


def test_unknown_stage_has_no_event_type() -> None:
    with pytest.raises(ValueError):
        stage_event_type("asr")


# --- E. Body schema: 422 before any decision --------------------------------------------


def test_retry_body_requires_at_least_one_stage_and_a_positive_version() -> None:
    with pytest.raises(ValidationError):
        AudioRetry.model_validate({"stages": [], "transcript_version": 1})
    with pytest.raises(ValidationError):
        AudioRetry.model_validate({"stages": ["index"], "transcript_version": 0})


def test_retry_body_rejects_duplicate_and_unknown_stages() -> None:
    with pytest.raises(ValidationError):
        AudioRetry.model_validate({"stages": ["index", "index"], "transcript_version": 1})
    with pytest.raises(ValidationError):
        AudioRetry.model_validate({"stages": ["asr"], "transcript_version": 1})


def test_retry_body_forbids_extra_fields() -> None:
    # An unknown field would silently change what a retry means; reject instead of ignore.
    body: dict[str, Any] = {"stages": ["index"], "transcript_version": 1}
    assert AudioRetry.model_validate(body).stages == ["index"]
    with pytest.raises(ValidationError):
        AudioRetry.model_validate({**body, "force": True})


def test_reupload_reservation_is_frozen_flat_values() -> None:
    audio_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    attempt_id = uuid.UUID("00000000-0000-0000-0000-000000000002")
    reservation = ReuploadReservation(
        audio_id=audio_id,
        attempt_id=attempt_id,
        upload_expires_at=datetime.now(UTC),
    )
    assert reservation.audio_id == audio_id
    with pytest.raises(FrozenInstanceError):
        reservation.attempt_id = uuid.UUID("00000000-0000-0000-0000-000000000003")  # type: ignore[misc]

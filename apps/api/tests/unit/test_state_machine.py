"""U-S2-AN-03: the A2.6 state machine, without Postgres — per-stage states and honest aggregate.

`app/services/state_machine.py` is the single transition authority of the contract
(ESPECIFICACION.md M2, DISENO §2.4/§3.5): typed terminal destinations for the ASR attempt,
`409 invalid_transition` on every illegal move, derived stage states where `not_requested`
never counts as success, and the honest `processing|ready|partial|failed` aggregate.
Following the style of `test_dedupe_identity.py`: pure functions and `SimpleNamespace`
doubles, no DB, no HTTP.

HTTP-level behavior of the callers (cancel endpoint A2.7, retry/reupload A2.8, commit
A2.10) is covered by their own suites; this file proves the machine they share. No
`integration` marker: runs in `python-unit`.
"""

import re
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.schema import CheckConstraint

from app.core.errors import ApiError
from app.models.ingestion import ACTIVE_INGESTION_STATES, INGESTION_STATES
from app.models.transcript import ProcessingRun
from app.services import state_machine
from app.services.state_machine import (
    ATTEMPT_TRANSITIONS,
    LOSS_REASONS,
    TERMINAL_ATTEMPT_STATES,
    aggregate_status,
    blocks_derived_stages,
    ensure_transition,
    stage_status,
    transition,
    transition_for_loss,
)

ASR_HAPPY_PATH = (
    "awaiting_upload",
    "receiving",
    "transcribing",
    "transcript_committed_cleanup_pending",
    "succeeded",
)


def attempt(status: str = "awaiting_upload", **extra: Any) -> Any:
    """Duck-typed `ingestion_attempts` row: only the attributes the machine touches."""
    fields: dict[str, Any] = {"status": status, "error_code": None}
    fields.update(extra)
    return SimpleNamespace(**fields)


def run(status: str, version: int = 1) -> Any:
    """Duck-typed `processing_runs` row, oldest→newest by construction in each test."""
    return SimpleNamespace(status=status, transcript_version=version)


def aggregate(
    attempt_status: str | None = "succeeded",
    *,
    has_transcript: bool = True,
    required: tuple[str, ...] = ("index",),
    stages: dict[str, str] | None = None,
) -> str:
    return aggregate_status(
        attempt_status=attempt_status,
        has_transcript=has_transcript,
        required_stages=required,
        stages=stages if stages is not None else {"index": "succeeded", "analyze": "not_requested"},
    )


# --- A. Happy ASR path and the aggregate that follows it -------------------------------


def test_happy_asr_path_walks_only_legal_transitions() -> None:
    current = ASR_HAPPY_PATH[0]
    for target in ASR_HAPPY_PATH[1:]:
        assert ensure_transition(current, target) == target
        current = target


def test_aggregate_walks_processing_to_ready() -> None:
    assert aggregate("awaiting_upload", has_transcript=False, stages={}) == "processing"
    assert aggregate("transcribing", has_transcript=False, stages={}) == "processing"
    assert aggregate(
        "transcript_committed_cleanup_pending",
        stages={"index": "pending", "analyze": "not_requested"},
    ) == "processing"
    assert aggregate("succeeded", stages={"index": "succeeded"}) == "ready"


def test_transition_writes_only_status_and_error_code() -> None:
    row = attempt("transcribing", received_bytes=42)
    transition(row, "cancelled")
    assert row.status == "cancelled"
    assert row.error_code is None  # untouched when not given
    assert row.received_bytes == 42  # nothing else is mutated


# --- B. Typed terminal destinations ---------------------------------------------------


@pytest.mark.parametrize(
    ("reason", "final_state", "error_code"),
    [
        ("upload_expired", "cancelled", "upload_expired"),
        ("unsupported_format", "rejected", "unsupported_format"),
        ("no_usable_text", "rejected", "no_usable_text"),
        ("technical_loss", "requires_reupload", "technical_loss"),
        ("explicit_cancel", "cancelled", None),
    ],
)
def test_loss_reasons_map_to_documented_finals(
    reason: str, final_state: str, error_code: str | None
) -> None:
    assert state_machine.loss_destination(reason) == (final_state, error_code)


@pytest.mark.parametrize(
    ("reason", "source"),
    [
        ("upload_expired", "awaiting_upload"),
        ("unsupported_format", "receiving"),
        ("unsupported_format", "transcribing"),
        ("no_usable_text", "transcribing"),
        ("technical_loss", "transcribing"),
        ("explicit_cancel", "receiving"),
        ("explicit_cancel", "transcribing"),
    ],
)
def test_loss_transitions_apply_from_legal_sources(reason: str, source: str) -> None:
    row = attempt(source)
    final, code = state_machine.loss_destination(reason)
    assert transition_for_loss(row, reason) == final
    assert row.status == final
    assert row.error_code == code


def test_unknown_loss_reason_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="unknown loss reason"):
        state_machine.loss_destination("providerExploded")


def test_typed_finals_from_an_illegal_source_conflict() -> None:
    # A commit is the point of no return: no typed loss may replace `succeeded`.
    for reason in LOSS_REASONS:
        row = attempt("transcript_committed_cleanup_pending")
        with pytest.raises(ApiError) as excinfo:
            transition_for_loss(row, reason)
        assert excinfo.value.status_code == 409
        assert row.status == "transcript_committed_cleanup_pending"  # unchanged


# --- C. Invalid transitions → 409 invalid_transition ----------------------------------


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("succeeded", "transcribing"),
        ("cancelled", "transcribing"),
        ("rejected", "succeeded"),
        ("awaiting_upload", "succeeded"),  # never skip the commit
        ("awaiting_upload", "transcribing"),  # receiving is mandatory
        ("receiving", "transcript_committed_cleanup_pending"),  # no commit before ASR
        ("transcript_committed_cleanup_pending", "cancelled"),  # cancel after commit conflicts
        ("transcript_committed_cleanup_pending", "rejected"),
        ("requires_reupload", "transcribing"),
    ],
)
def test_invalid_transitions_raise_409_with_both_statuses(current: str, target: str) -> None:
    row = attempt(current)
    with pytest.raises(ApiError) as excinfo:
        transition(row, target)
    error = excinfo.value
    assert error.status_code == 409
    assert error.code == "invalid_transition"
    assert error.details == {"current_status": current, "attempted_status": target}
    assert row.status == current  # the rejected move writes nothing


def test_unknown_current_status_fails_closed() -> None:
    with pytest.raises(ApiError) as excinfo:
        ensure_transition("nonsense", "transcribing")
    assert excinfo.value.status_code == 409
    with pytest.raises(ApiError) as excinfo_self:
        ensure_transition("nonsense", "nonsense")
    assert excinfo_self.value.status_code == 409


# --- D. Final states are absorbing -----------------------------------------------------


@pytest.mark.parametrize("final", TERMINAL_ATTEMPT_STATES)
def test_finals_reject_every_move_except_themselves(final: str) -> None:
    for target in INGESTION_STATES:
        if target == final:
            continue
        with pytest.raises(ApiError) as excinfo:
            ensure_transition(final, target)
        assert excinfo.value.code == "invalid_transition"


@pytest.mark.parametrize("status", INGESTION_STATES)
def test_self_transition_is_an_accepted_noop(status: str) -> None:
    assert ensure_transition(status, status) == status
    row = attempt(status, error_code="kept")
    transition(row, status)
    assert row.status == status
    assert row.error_code == "kept"  # a no-op never clears a persisted code


# --- E. Cleanup is independent and never success ---------------------------------------


@pytest.mark.parametrize("status", sorted(ACTIVE_INGESTION_STATES))
def test_only_succeeded_releases_the_cleanup_gate(status: str) -> None:
    assert blocks_derived_stages(status) is True


@pytest.mark.parametrize("status", sorted(set(TERMINAL_ATTEMPT_STATES)))
def test_finals_do_not_report_a_cleanup_block(status: str) -> None:
    assert blocks_derived_stages(status) is False


def test_cleanup_pending_is_never_ready_even_with_succeeded_stage_runs() -> None:
    # The outbox gate keeps index/analyze at bay until cleanup is verified; a stale or
    # synthetic `succeeded` run must not let the aggregate claim success (M2 honesty).
    assert (
        aggregate(
            "transcript_committed_cleanup_pending",
            stages={"index": "succeeded", "analyze": "not_requested"},
        )
        == "processing"
    )


# --- F. Stage derivation ---------------------------------------------------------------


def test_required_stage_without_runs_is_pending_not_not_requested() -> None:
    assert stage_status([], required=True) == "pending"


def test_non_required_stage_without_runs_is_not_requested() -> None:
    assert stage_status([], required=False) == "not_requested"


def test_outstanding_work_beats_terminal_rows_and_running_beats_pending() -> None:
    assert stage_status([run("succeeded"), run("running")], required=True) == "running"
    assert stage_status([run("succeeded"), run("pending")], required=True) == "pending"
    assert stage_status([run("pending"), run("running")], required=True) == "running"


def test_latest_terminal_row_decides() -> None:
    assert stage_status([run("failed"), run("succeeded")], required=True) == "succeeded"
    assert stage_status([run("succeeded"), run("cancelled")], required=True) == "cancelled"
    assert stage_status([run("succeeded"), run("failed")], required=True) == "failed"


def test_runs_of_a_different_transcript_version_are_ignored() -> None:
    runs = [run("succeeded", version=1), run("running", version=2)]
    assert stage_status(runs, required=True, active_transcript_version=1) == "succeeded"
    assert stage_status(runs, required=True, active_transcript_version=2) == "running"
    # Without an active version every run counts (defensive default).
    assert stage_status(runs, required=True) == "running"


def test_unknown_run_status_rows_are_ignored() -> None:
    assert stage_status([run("weird")], required=True) == "pending"


# --- G. `not_requested` ≠ success ------------------------------------------------------


def test_not_requested_analysis_does_not_block_ready() -> None:
    # F1: index only. A correct index allows chat even if analysis was never requested.
    assert aggregate(stages={"index": "succeeded", "analyze": "not_requested"}) == "ready"


def test_all_required_not_requested_never_reads_ready() -> None:
    # Data anomaly defense: a required stage stuck at `not_requested` is owed work.
    assert (
        aggregate(
            "succeeded",
            required=("index", "analyze"),
            stages={"index": "not_requested", "analyze": "not_requested"},
        )
        == "processing"
    )


def test_required_stage_with_unknown_status_reads_pending_not_success() -> None:
    assert (
        aggregate("succeeded", required=("index", "analyze"), stages={"index": "succeeded"})
        == "processing"
    )


# --- H. Aggregate matrix ---------------------------------------------------------------


def test_no_usable_transcript_is_failed() -> None:
    assert aggregate(None, has_transcript=False, stages={}) == "failed"
    assert aggregate(None, has_transcript=True, stages={"index": "succeeded"}) == "failed"
    assert aggregate("rejected", has_transcript=False, stages={}) == "failed"
    assert aggregate("requires_reupload", has_transcript=False, stages={}) == "failed"
    assert aggregate("cancelled", has_transcript=False, stages={}) == "failed"
    # Anomaly defense: a "succeeded" attempt without a transcript is not `ready`.
    assert aggregate("succeeded", has_transcript=False, stages={}) == "failed"


def test_active_attempt_is_processing_even_with_stale_runs() -> None:
    for status in ACTIVE_INGESTION_STATES:
        assert aggregate(status, stages={"index": "succeeded"}) == "processing"


def test_one_required_stage_failed_keeps_processing_while_another_runs() -> None:
    assert (
        aggregate(
            "succeeded",
            required=("index", "analyze"),
            stages={"index": "failed", "analyze": "running"},
        )
        == "processing"
    )


def test_partial_when_a_required_stage_is_terminal_failed_or_cancelled() -> None:
    assert (
        aggregate(
            "succeeded",
            required=("index", "analyze"),
            stages={"index": "succeeded", "analyze": "failed"},
        )
        == "partial"
    )
    assert (
        aggregate(
            "succeeded",
            required=("index", "analyze"),
            stages={"index": "cancelled", "analyze": "succeeded"},
        )
        == "partial"
    )


def test_empty_required_stages_with_transcript_reads_ready() -> None:
    # Documented edge: nothing required and a usable transcript ⇒ nothing is owed.
    assert aggregate("succeeded", required=(), stages={}) == "ready"


# --- I. Drift between the machine and the DB CHECKs of migration 0002 ------------------


def _check_values(table_args: tuple[Any, ...], name: str) -> set[str]:
    # Declarative applies the `ck_<table>_<name>` naming convention to the model CHECKs.
    for arg in table_args:
        if isinstance(arg, CheckConstraint) and str(arg.name).endswith(f"_{name}"):
            return set(re.findall(r"'([a-z_]+)'", str(arg.sqltext)))
    raise AssertionError(f"CHECK {name!r} not found on the model")


def test_attempt_states_match_the_ingestion_attempts_check() -> None:
    assert set(ATTEMPT_TRANSITIONS) == set(INGESTION_STATES)
    assert set(ACTIVE_INGESTION_STATES) <= set(INGESTION_STATES)
    assert set(TERMINAL_ATTEMPT_STATES) <= set(INGESTION_STATES)
    # Finals are absorbing in both the table and the constant.
    for final in TERMINAL_ATTEMPT_STATES:
        assert ATTEMPT_TRANSITIONS[final] == frozenset()


def test_stage_states_match_the_processing_runs_checks() -> None:
    assert _check_values(ProcessingRun.__table_args__, "stage") == set(state_machine.STAGES)
    assert _check_values(ProcessingRun.__table_args__, "status") == set(
        state_machine.RUN_STAGE_STATES
    )
    assert set(state_machine.TERMINAL_STAGE_STATES) <= set(state_machine.RUN_STAGE_STATES)
    assert set(state_machine.STAGE_STATES) == set(state_machine.RUN_STAGE_STATES) | {
        "not_requested"
    }
    assert set(LOSS_REASONS) == {
        "upload_expired",
        "unsupported_format",
        "no_usable_text",
        "technical_loss",
        "explicit_cancel",
    }

"""Unit support for the S3.1 backfill (I-S3-SG-01).

The integration suite proves the queue and the absence of ingestion writes; these cases pin
the pure decisions that must not depend on a database: the F1 → F2 upgrade, the class-page
availability rule and the report accounting.
"""

import uuid

import pytest

from app.services.analysis_backfill import (
    BackfillItem,
    BackfillReport,
    BackfillStatus,
    analysis_backfill_available,
    analysis_required,
    classify_existing_run,
    with_analysis_stage,
)


def test_with_analysis_stage_preserves_order_and_is_idempotent() -> None:
    assert with_analysis_stage(["index"]) == ["index", "analyze"]
    assert with_analysis_stage(["index", "analyze"]) == ["index", "analyze"]
    assert analysis_required(["index", "analyze"]) is True
    assert analysis_required(["index"]) is False


@pytest.mark.parametrize(
    (
        "has_usable_transcript",
        "attempt_status",
        "cleanup_status",
        "analyze_state",
        "has_current_run",
        "analysis_already_required",
        "has_previous_run",
        "expected",
    ),
    [
        # F1 without any analysis run: the backfill is the explicit upgrade path.
        (True, "succeeded", "verified", "not_requested", False, False, False, True),
        (True, "succeeded", "verified", "failed", True, True, True, True),
        (True, "succeeded", "verified", "cancelled", True, True, True, True),
        # Active or successful analysis of the current versions: the stage is the queue.
        (True, "succeeded", "verified", "pending", True, True, True, False),
        (True, "succeeded", "verified", "running", True, True, True, False),
        (True, "succeeded", "verified", "succeeded", True, True, True, False),
        # No run for the active transcript/current versions: a version changed and
        # reanalysis is offered even when the stage list already owes analysis (S3.4).
        (True, "succeeded", "verified", "pending", False, True, True, True),
        (True, "succeeded", "verified", "not_requested", True, False, False, True),
        # F2 without any analysis run already owes analysis, so `retry` is its entry point;
        # the class page must not offer the backfill that the service resolves as
        # `already_f2_no_run` (parity with `classify_existing_run`).
        (True, "succeeded", "verified", "pending", False, True, False, False),
        # Cleanup and success gates fail closed regardless of the run/version inputs.
        (False, "succeeded", "verified", "not_requested", False, False, False, False),
        (
            True,
            "transcript_committed_cleanup_pending",
            "pending",
            "not_requested",
            False,
            False,
            False,
            False,
        ),
        (True, "succeeded", "pending", "not_requested", False, False, False, False),
        (True, "rejected", "verified", "not_requested", False, False, False, False),
        (True, None, None, "not_requested", False, False, False, False),
    ],
)
def test_analysis_backfill_available_mirrors_classify_existing_run(
    has_usable_transcript: bool,
    attempt_status: str | None,
    cleanup_status: str | None,
    analyze_state: str,
    has_current_run: bool,
    analysis_already_required: bool,
    has_previous_run: bool,
    expected: bool,
) -> None:
    assert (
        analysis_backfill_available(
            has_usable_transcript=has_usable_transcript,
            attempt_status=attempt_status,
            cleanup_status=cleanup_status,
            analyze_state=analyze_state,
            has_current_run=has_current_run,
            analysis_already_required=analysis_already_required,
            has_previous_run=has_previous_run,
        )
        is expected
    )


@pytest.mark.parametrize(
    ("analysis_already_required", "existing_status", "has_previous_run", "expected"),
    [
        # F1 without an analysis run is the only case that may queue (and only on apply).
        (False, None, False, None),
        # F2 without a run already owes analysis; retry is its entry point, not the backfill.
        (True, None, False, BackfillStatus.ALREADY_F2_NO_RUN),
        # A previous run under another transcript/config version is history: analysis is
        # owed for the current versions, so a NEW run is queued (S3.4).
        (False, None, True, None),
        (True, None, True, None),
        (False, "succeeded", False, BackfillStatus.ALREADY_ANALYZED),
        (True, "succeeded", False, BackfillStatus.ALREADY_ANALYZED),
        (False, "pending", False, BackfillStatus.ALREADY_QUEUED),
        (False, "running", False, BackfillStatus.ALREADY_QUEUED),
        (False, "failed", False, None),
        (False, "cancelled", False, None),
        (True, "failed", False, None),
        # Corrupt data fails closed instead of widening the decision.
        (False, "paused", False, BackfillStatus.UNKNOWN_RUN_STATE),
        (True, "paused", True, BackfillStatus.UNKNOWN_RUN_STATE),
    ],
)
def test_classify_existing_run_decides_before_any_write(
    analysis_already_required: bool,
    existing_status: str | None,
    has_previous_run: bool,
    expected: BackfillStatus | None,
) -> None:
    assert (
        classify_existing_run(
            analysis_already_required=analysis_already_required,
            existing_status=existing_status,
            has_previous_run=has_previous_run,
        )
        is expected
    )


def test_report_counts_every_status_once() -> None:
    audio_id = uuid.uuid4()
    report = BackfillReport(
        items=(
            BackfillItem(audio_id=audio_id, status=BackfillStatus.QUEUED),
            BackfillItem(audio_id=uuid.uuid4(), status=BackfillStatus.NO_TRANSCRIPT),
            BackfillItem(audio_id=uuid.uuid4(), status=BackfillStatus.QUEUED),
        ),
        truncated=False,
        applied=True,
    )

    assert report.counts[BackfillStatus.QUEUED] == 2
    assert report.counts[BackfillStatus.NO_TRANSCRIPT] == 1
    assert sum(report.counts.values()) == len(report.items)

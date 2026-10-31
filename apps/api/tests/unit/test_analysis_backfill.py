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
    with_analysis_stage,
)


def test_with_analysis_stage_preserves_order_and_is_idempotent() -> None:
    assert with_analysis_stage(["index"]) == ["index", "analyze"]
    assert with_analysis_stage(["index", "analyze"]) == ["index", "analyze"]
    assert analysis_required(["index", "analyze"]) is True
    assert analysis_required(["index"]) is False


@pytest.mark.parametrize(
    ("has_usable_transcript", "attempt_status", "cleanup_status", "analyze_state", "expected"),
    [
        (True, "succeeded", "verified", "not_requested", True),
        (True, "succeeded", "verified", "failed", True),
        (True, "succeeded", "verified", "cancelled", True),
        (True, "succeeded", "verified", "pending", False),
        (True, "succeeded", "verified", "running", False),
        (True, "succeeded", "verified", "succeeded", False),
        (False, "succeeded", "verified", "not_requested", False),
        (True, "transcript_committed_cleanup_pending", "pending", "not_requested", False),
        (True, "succeeded", "pending", "not_requested", False),
        (True, "rejected", "verified", "not_requested", False),
        (True, None, None, "not_requested", False),
    ],
)
def test_analysis_backfill_available_fails_closed(
    has_usable_transcript: bool,
    attempt_status: str | None,
    cleanup_status: str | None,
    analyze_state: str,
    expected: bool,
) -> None:
    assert (
        analysis_backfill_available(
            has_usable_transcript=has_usable_transcript,
            attempt_status=attempt_status,
            cleanup_status=cleanup_status,
            analyze_state=analyze_state,
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

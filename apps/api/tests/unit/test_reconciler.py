"""Pure rules of the J2.4 reconciler: grace margin, Redis keys, retry budget, state changes."""

import uuid
from datetime import UTC, datetime, timedelta

from app.models import OutboxEvent, ProcessingRun
from app.services.index_job import LEASE
from app.services.reconciliation import (
    DISPATCH_GRACE,
    LEASE_EXPIRED_CODE,
    mark_lease_expired,
    reset_to_pending,
)
from app.workers.index_job import JOB_TIMEOUT_S, MAX_TRIES
from app.workers.reconciler import has_retry_budget, outbox_job_keys


def test_dispatch_grace_outlasts_a_live_job() -> None:
    # A job that is still running must never look lost, so the grace is longer than its lease.
    assert DISPATCH_GRACE > LEASE > timedelta(seconds=JOB_TIMEOUT_S)


def test_outbox_job_keys_cover_queued_running_and_kept_result() -> None:
    event_id = uuid.UUID("00000000-0000-4000-8000-000000000001")
    assert outbox_job_keys(event_id) == (
        "arq:job:outbox:00000000-0000-4000-8000-000000000001",
        "arq:in-progress:outbox:00000000-0000-4000-8000-000000000001",
        "arq:result:outbox:00000000-0000-4000-8000-000000000001",
    )


def test_retry_budget_allows_claims_below_max_tries_only() -> None:
    assert has_retry_budget(MAX_TRIES - 1)
    assert not has_retry_budget(MAX_TRIES)


def test_reset_to_pending_clears_the_publication_stamp() -> None:
    event = OutboxEvent(status="dispatched", published_at=datetime.now(UTC))

    reset_to_pending(event)

    assert event.status == "pending"
    assert event.published_at is None


def test_mark_lease_expired_closes_the_run_with_its_code() -> None:
    now = datetime.now(UTC)
    run = ProcessingRun(status="running", started_at=now - LEASE - timedelta(seconds=1))

    mark_lease_expired(run, now=now)

    assert run.status == "failed"
    assert run.error_code == LEASE_EXPIRED_CODE == "lease_expired"
    assert run.finished_at == now

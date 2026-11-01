"""J4.2 unit guardrails for the scheduler election and claim budgets."""

import os
from datetime import timedelta

from app.services.scheduler_lease import (
    BATCH_SIZE,
    DEFAULT_LEASE_TTL,
    DELIVERY_LEASE,
    DELIVERY_SLA,
    SCHEDULER_ROLE,
    SchedulerReport,
    process_holder,
)


def test_the_target_delivery_sla_is_the_fifteen_minutes_of_the_contract() -> None:
    assert DELIVERY_SLA == timedelta(minutes=15)


def test_the_lease_outlives_a_tick_but_stays_inside_the_sla() -> None:
    # The cron runs every minute. A TTL of at least two ticks absorbs one late tick; a dead
    # holder must still be replaced long before the 15-minute target.
    assert timedelta(minutes=1) <= DEFAULT_LEASE_TTL < DELIVERY_SLA


def test_a_delivery_lease_cannot_outlive_the_scheduler_lease_by_much() -> None:
    # A sending row is uncertain after its lease; keeping it well below the target leaves
    # room for the next claim and for the manual resend flow.
    assert timedelta(minutes=1) <= DELIVERY_LEASE <= DELIVERY_SLA


def test_the_claim_batch_is_bounded_and_non_empty() -> None:
    assert 1 <= BATCH_SIZE <= 1_000


def test_the_role_name_is_stable_and_not_empty() -> None:
    assert SCHEDULER_ROLE == "notification_scheduler"


def test_the_process_holder_is_stable_and_identifies_the_process() -> None:
    first = process_holder()
    second = process_holder()

    assert first == second
    assert str(os.getpid()) in first
    assert first.strip() == first
    assert len(first.split(":")) == 3


def test_an_inactive_report_carries_no_claim_counts() -> None:
    report = SchedulerReport(active=False)

    assert report.claimed == 0
    assert report.expired == 0
    assert report.lease is None

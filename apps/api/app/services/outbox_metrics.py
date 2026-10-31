"""Outbox metrics for `/internal/metrics` (J2.5): lag, status counts and retries.

Lag is the age of the oldest pending event the dispatcher can take right now. The filter
mirrors the batch filter in `workers/dispatcher.py` (cleanup gate, types without consumer,
retry wait). A blocked event is an expected wait, not dispatcher lag.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.prometheus import Sample
from app.models import OutboxEvent
from app.models.ingestion import OUTBOX_STATES
from app.services.outbox import CLEANUP_GATED_TYPES, KNOWN_WITHOUT_CONSUMER
from app.workers.dispatcher import LATEST_CLEANUP

GATED_TYPES = tuple(sorted(CLEANUP_GATED_TYPES))
WITHOUT_CONSUMER = tuple(sorted(KNOWN_WITHOUT_CONSUMER))
# A2.17: role created by the RLS migration; NOLOGIN and without BYPASSRLS.
JOBS_ROLE = "maulwurf_jobs"


async def collect_outbox_samples(
    db: AsyncSession, *, now: datetime | None = None, cross_tenant: bool = False
) -> list[Sample]:
    """Outbox samples read in one session; no rows are changed.

    ``cross_tenant`` is the internal metrics aggregate: it switches the transaction to
    the jobs role and its explicit cross-tenant policy before reading. The API request
    path keeps the default and stays tenant-bound.
    """
    if cross_tenant:
        await db.execute(text(f'SET LOCAL ROLE "{JOBS_ROLE}"'))
        await db.execute(text("SELECT set_config('app.cross_tenant', 'on', true)"))
    instant = now or datetime.now(UTC)
    gated = OutboxEvent.type.in_(GATED_TYPES)
    can_advance = or_(
        ~gated,
        OutboxEvent.resource_type != "audio",
        and_(OutboxEvent.type == "index_requested", OutboxEvent.resource_version.is_(None)),
        LATEST_CLEANUP == "verified",
    )
    dispatchable = and_(
        OutboxEvent.status == "pending",
        OutboxEvent.enabled.is_(True),
        OutboxEvent.published_at.is_(None),
        OutboxEvent.type.not_in(WITHOUT_CONSUMER),
        or_(OutboxEvent.next_attempt_at.is_(None), OutboxEvent.next_attempt_at <= instant),
        can_advance,
    )
    oldest = await db.scalar(select(func.min(OutboxEvent.created_at)).where(dispatchable))
    lag = 0.0 if oldest is None else max(0.0, (instant - oldest).total_seconds())

    rows = (
        await db.execute(select(OutboxEvent.status, func.count()).group_by(OutboxEvent.status))
    ).all()
    counts: dict[str, int] = {status: int(total) for status, total in rows}
    retrying = await db.scalar(
        select(func.count()).where(OutboxEvent.status == "pending", OutboxEvent.attempts > 0)
    )

    samples = [
        Sample(
            "maulwurf_outbox_lag_seconds",
            "gauge",
            "Age of the oldest pending event the dispatcher can take.",
            lag,
        ),
        Sample(
            "maulwurf_outbox_retrying_events",
            "gauge",
            "Pending events with at least one failed attempt.",
            float(retrying or 0),
        ),
    ]
    samples.extend(
        Sample(
            "maulwurf_outbox_events",
            "gauge",
            "Outbox events by status.",
            float(counts.get(state, 0)),
            {"status": state},
        )
        for state in OUTBOX_STATES
    )
    return samples

"""Dispatcher de la outbox (J1.5): de `outbox_events` en PostgreSQL a jobs ARQ en Redis.

Cada ciclo toma un lote de eventos pendientes con `FOR UPDATE SKIP LOCKED` (dos
schedulers no publican el mismo evento), aplica `services.outbox.decide` y encola solo IDs.
Entrega al menos una vez: si el commit falla después de encolar, el evento sigue pendiente
y el siguiente ciclo lo reintenta; `_job_id = outbox:<id>` evita duplicarlo mientras el job
exista en Redis, y los consumidores deben ser idempotentes. Sin reintentos con backoff ni
`attempts` en S1 (J2.1).
"""
import logging
import uuid
from collections import Counter
from datetime import UTC, datetime

from arq.connections import ArqRedis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import IngestionAttempt, OutboxEvent
from app.services.outbox import CLEANUP_GATED_TYPES, Outcome, decide

logger = logging.getLogger(__name__)

BATCH_SIZE = 100


async def _latest_cleanup_status(
    db: AsyncSession, user_id: uuid.UUID, audio_id: uuid.UUID
) -> str | None:
    status: str | None = await db.scalar(
        select(IngestionAttempt.cleanup_status)
        .where(IngestionAttempt.user_id == user_id, IngestionAttempt.audio_id == audio_id)
        .order_by(IngestionAttempt.created_at.desc())
        .limit(1)
    )
    return status


def _job_args(event: OutboxEvent, job: str) -> tuple[object, ...]:
    # Solo IDs y versión: nunca payload, audio, tokens ni texto.
    if job == "index":
        return (str(event.resource_id), event.resource_version)
    return (str(event.resource_id),)


async def dispatch_pending(
    db: AsyncSession, redis: ArqRedis, *, queue_name: str, batch_size: int = BATCH_SIZE
) -> Counter[Outcome]:
    """Publica los eventos listos del lote; devuelve cuántos cayeron en cada resultado."""
    events = (
        await db.scalars(
            select(OutboxEvent)
            .where(
                OutboxEvent.status == "pending",
                OutboxEvent.published_at.is_(None),
                OutboxEvent.enabled.is_(True),
            )
            .order_by(OutboxEvent.created_at)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
    ).all()

    report: Counter[Outcome] = Counter()
    now = datetime.now(UTC)
    for event in events:
        cleanup = (
            await _latest_cleanup_status(db, event.user_id, event.resource_id)
            if event.type in CLEANUP_GATED_TYPES
            else None
        )
        decision = decide(
            event.type,
            enabled=event.enabled,
            blocked_reason=event.blocked_reason,
            cleanup_status=cleanup,
        )
        report[decision.outcome] += 1
        if decision.outcome is Outcome.UNKNOWN_TYPE:
            logger.warning("outbox.unknown_type event_id=%s type=%s", event.id, event.type)
        if not decision.publish or decision.job is None:
            continue
        await redis.enqueue_job(
            decision.job,
            *_job_args(event, decision.job),
            _job_id=f"outbox:{event.id}",
            _queue_name=queue_name,
        )
        event.status = "dispatched"
        event.published_at = now
    return report

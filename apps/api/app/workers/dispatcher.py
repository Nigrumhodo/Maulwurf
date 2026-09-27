"""Dispatcher de la outbox (J1.5): de `outbox_events` en PostgreSQL a jobs ARQ en Redis.

Cada ciclo toma un lote de eventos pendientes con `FOR UPDATE SKIP LOCKED` (dos
schedulers no publican el mismo evento), aplica `services.outbox.decide` y encola solo IDs.

- Los tipos conocidos sin consumidor en S1 no entran en el lote: siguen `pending` sin
  ocupar huecos, así un backlog suyo no puede dejar sin despachar a index/analyze.
- Un tipo desconocido, o un tipo con gate sobre un recurso que no es un audio, pasa a
  `skipped` (terminal, nada se publica) en vez de reaparecer en cada ciclo.
- Entrega al menos una vez, no exactamente una: si el commit falla después de encolar, el
  evento sigue pendiente y el siguiente ciclo lo reencola. `_job_id = outbox:<id>` solo
  deduplica mientras el job o su resultado siguen en Redis (`keep_result = 60 s`); pasado
  ese tiempo puede repetirse, así que los consumidores deben ser idempotentes.
- Sin reintentos con backoff, `attempts` ni `next_attempt_at` en S1 (J2.1).
"""
import logging
import uuid
from collections import Counter
from datetime import UTC, datetime

from arq.connections import ArqRedis
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import IngestionAttempt, OutboxEvent
from app.services.outbox import (
    CLEANUP_GATED_TYPES,
    KNOWN_WITHOUT_CONSUMER,
    Decision,
    Outcome,
    decide,
)

logger = logging.getLogger(__name__)

BATCH_SIZE = 100
AttemptKey = tuple[uuid.UUID, uuid.UUID]  # (user_id, audio_id)


async def _latest_cleanup_statuses(
    db: AsyncSession, keys: set[AttemptKey]
) -> dict[AttemptKey, str]:
    """`cleanup_status` del último intento de cada audio del lote, en una sola consulta.

    `id DESC` desempata: `created_at` es `now()` de la transacción y dos intentos del
    mismo audio pueden compartirlo.
    """
    if not keys:
        return {}
    rows = await db.execute(
        select(
            IngestionAttempt.user_id, IngestionAttempt.audio_id, IngestionAttempt.cleanup_status
        )
        .where(tuple_(IngestionAttempt.user_id, IngestionAttempt.audio_id).in_(keys))
        .distinct(IngestionAttempt.user_id, IngestionAttempt.audio_id)
        .order_by(
            IngestionAttempt.user_id,
            IngestionAttempt.audio_id,
            IngestionAttempt.created_at.desc(),
            IngestionAttempt.id.desc(),
        )
    )
    return {(user_id, audio_id): status for user_id, audio_id, status in rows.all()}


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
                OutboxEvent.type.not_in(KNOWN_WITHOUT_CONSUMER),
            )
            .order_by(OutboxEvent.created_at, OutboxEvent.id)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
    ).all()

    gated = [e for e in events if e.type in CLEANUP_GATED_TYPES and e.resource_type == "audio"]
    cleanups = await _latest_cleanup_statuses(db, {(e.user_id, e.resource_id) for e in gated})

    report: Counter[Outcome] = Counter()
    now = datetime.now(UTC)
    for event in events:
        if event.type in CLEANUP_GATED_TYPES and event.resource_type != "audio":
            # El gate lee `ingestion_attempts` con resource_id como audio_id: sin un audio
            # no hay cleanup que verificar, así que no se publica nunca.
            decision = Decision(Outcome.UNKNOWN_TYPE)
            logger.warning(
                "outbox.gated_non_audio event_id=%s type=%s resource_type=%s",
                event.id, event.type, event.resource_type,
            )
        else:
            decision = decide(
                event.type,
                enabled=event.enabled,
                blocked_reason=event.blocked_reason,
                cleanup_status=cleanups.get((event.user_id, event.resource_id)),
            )
            if decision.outcome is Outcome.UNKNOWN_TYPE:
                logger.warning("outbox.unknown_type event_id=%s type=%s", event.id, event.type)
        report[decision.outcome] += 1

        if decision.outcome is Outcome.UNKNOWN_TYPE:
            event.status = "skipped"
            continue
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

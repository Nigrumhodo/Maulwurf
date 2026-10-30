"""Dispatcher de la outbox (J1.5, J2.1): de `outbox_events` en PostgreSQL a jobs ARQ en Redis.

Cada ciclo toma un lote con `FOR UPDATE SKIP LOCKED` (dos schedulers no publican el mismo
evento), aplica `services.outbox.decide`, revalida el evento y encola solo IDs.

- El lote solo contiene eventos que pueden avanzar: los publicables, los que hay que
  descartar y los que ya cumplen su espera de reintento. Quedan fuera, sin ocupar huecos,
  los tipos sin consumidor en S1, los index/analyze sin cleanup verificado y los reintentos
  pendientes (`next_attempt_at` en el futuro).
- Cada fila cuenta una sola vez con su resultado final (`Outcome`).
- Errores de encolado (`workers/outbox_errors.py`):
  - infra (Redis caído, OOM): se corta el ciclo sin tocar los eventos restantes ni gastar
    intentos. Se reintenta en el siguiente ciclo del scheduler.
  - transitorio: `attempts + 1` y `next_attempt_at` con backoff y jitter; al llegar a
    `max_attempts` pasa a `failed`.
  - permanente: `failed` sin reintento.
- Revalidación justo antes de publicar (`workers/outbox_revalidation.py`): audio borrado,
  dueño sin cuenta activa o versión desplazada dejan el evento `skipped` sin publicar.
- Entrega al menos una vez, no exactamente una: si el commit falla después de encolar, el
  evento sigue pendiente y el siguiente ciclo lo reencola. `_job_id = outbox:<id>` solo
  deduplica mientras el job o su resultado siguen en Redis (`keep_result = 60 s`); pasado
  ese tiempo puede repetirse, así que los consumidores deben ser idempotentes.
- El encolado ocurre con los row locks tomados: si Redis se degrada, la transacción se
  alarga y otros schedulers saltan esas filas (SKIP LOCKED). Aceptable con lote 100 y cron
  minutal; revisar si aparece presión real.
"""

import logging
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime

from arq.connections import ArqRedis
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import IngestionAttempt, OutboxEvent
from app.services.outbox import (
    CLEANUP_GATED_TYPES,
    KNOWN_WITHOUT_CONSUMER,
    Decision,
    Outcome,
    decide,
)
from app.services.outbox_retry import DEFAULT_RETRY, RetryPolicy
from app.workers.outbox_delivery import enqueue
from app.workers.outbox_revalidation import revalidate

logger = logging.getLogger(__name__)

BATCH_SIZE = 100
# Tuplas ordenadas: SQL estable entre procesos (un frozenset itera en orden arbitrario).
GATED_TYPES = tuple(sorted(CLEANUP_GATED_TYPES))
WITHOUT_CONSUMER = tuple(sorted(KNOWN_WITHOUT_CONSUMER))

# `cleanup_status` del último intento del audio, correlacionado con el evento. Única
# definición de "último intento": filtra el lote y es lo que recibe `decide()`.
# `id DESC` solo hace el orden determinista ante un empate de `created_at`; no es
# cronológico (UUIDv4). Un empate exige dos intentos del mismo audio en una transacción,
# y `attempts_one_active` impide dos activos a la vez.
LATEST_CLEANUP = (
    select(IngestionAttempt.cleanup_status)
    .where(
        IngestionAttempt.user_id == OutboxEvent.user_id,
        IngestionAttempt.audio_id == OutboxEvent.resource_id,
    )
    .order_by(IngestionAttempt.created_at.desc(), IngestionAttempt.id.desc())
    .limit(1)
    .correlate(OutboxEvent)
    .scalar_subquery()
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _malformed(event: OutboxEvent) -> str | None:
    """Motivo por el que un evento con gate nunca podrá publicarse, o `None`."""
    if event.type not in CLEANUP_GATED_TYPES:
        return None
    if event.resource_type != "audio":
        return "gated_non_audio"
    if event.type == "index_requested" and event.resource_version is None:
        return "missing_version"
    return None


def _decide(event: OutboxEvent, cleanup_status: str | None) -> Decision:
    reason = _malformed(event)
    if reason is not None:
        logger.warning("outbox.%s event_id=%s type=%s", reason, event.id, event.type)
        return Decision(Outcome.UNKNOWN_TYPE)
    decision = decide(
        event.type,
        enabled=event.enabled,
        blocked_reason=event.blocked_reason,
        cleanup_status=cleanup_status,
    )
    if decision.outcome is Outcome.UNKNOWN_TYPE:
        logger.warning("outbox.unknown_type event_id=%s type=%s", event.id, event.type)
    return decision


async def dispatch_pending(
    db: AsyncSession,
    redis: ArqRedis,
    *,
    queue_name: str,
    batch_size: int = BATCH_SIZE,
    retry: RetryPolicy = DEFAULT_RETRY,
    clock: Callable[[], datetime] = _utc_now,
) -> Counter[Outcome]:
    """Publica los eventos listos del lote; devuelve cuántos cayeron en cada resultado."""
    gated = OutboxEvent.type.in_(GATED_TYPES)
    can_advance = or_(
        ~gated,  # desconocidos (se descartan) y cualquier tipo futuro con consumidor
        OutboxEvent.resource_type != "audio",  # gate imposible: se descarta
        and_(OutboxEvent.type == "index_requested", OutboxEvent.resource_version.is_(None)),
        LATEST_CLEANUP == "verified",
    )
    now = clock()
    rows = (
        await db.execute(
            select(OutboxEvent, LATEST_CLEANUP)
            .where(
                OutboxEvent.status == "pending",
                OutboxEvent.published_at.is_(None),
                OutboxEvent.enabled.is_(True),
                OutboxEvent.type.not_in(WITHOUT_CONSUMER),
                or_(OutboxEvent.next_attempt_at.is_(None), OutboxEvent.next_attempt_at <= now),
                can_advance,
            )
            .order_by(OutboxEvent.created_at, OutboxEvent.id)
            .limit(batch_size)
            .with_for_update(skip_locked=True, of=OutboxEvent)
        )
    ).all()

    report: Counter[Outcome] = Counter()
    for event, cleanup_status in rows:
        outcome = await _dispatch_one(
            db, redis, event, cleanup_status, queue_name=queue_name, retry=retry,
            clock=clock, report=report,
        )
        report[outcome] += 1
        if outcome is Outcome.DEFERRED_INFRA:
            # Redis no responde: el resto del lote fallaría igual, y cada fallo bloquea tiempo.
            break
    return report


async def _dispatch_one(
    db: AsyncSession,
    redis: ArqRedis,
    event: OutboxEvent,
    cleanup_status: str | None,
    *,
    queue_name: str,
    retry: RetryPolicy,
    clock: Callable[[], datetime],
    report: Counter[Outcome],
) -> Outcome:
    decision = _decide(event, cleanup_status)
    if decision.outcome is Outcome.UNKNOWN_TYPE:
        event.status = "skipped"
        return Outcome.UNKNOWN_TYPE
    if not decision.publish or decision.job is None:
        return decision.outcome

    rejection = await revalidate(db, event)
    if rejection is not None:
        logger.info("outbox.revalidation_rejected event_id=%s reason=%s", event.id, rejection)
        event.status = "skipped"
        return rejection
    return await enqueue(
        redis, event, decision.job, queue_name=queue_name, retry=retry, clock=clock,
        report=report,
    )

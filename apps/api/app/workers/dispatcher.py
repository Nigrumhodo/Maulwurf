"""Outbox dispatcher (J1.5, J2.1): from `outbox_events` in PostgreSQL to ARQ jobs in Redis.

Cada ciclo toma un lote con `FOR UPDATE SKIP LOCKED` (dos schedulers no publican el mismo
event), applies `services.outbox.decide`, revalidates the event and enqueues only IDs.

- The batch only contains events that can advance: publishable ones, ones to discard,
  and those whose retry wait has elapsed. Left out without leaving gaps or staying `pending`:
  types with no consumer yet, index/analyze events without verified cleanup, and retries
  still waiting (`next_attempt_at` in the future).
- Task events (`sync_task_event`, J3.4) are enqueued with `(task_id, task_version)` and are
  not revalidated against the audio table; only `resource_type='audio'` rows take that path.
- Each row is counted once, with its final result (`Outcome`).
- Enqueue errors (`workers/outbox_errors.py`):
  - infra (Redis down, OOM): the cycle stops without touching the remaining events or spending
    attempts. It is retried in the scheduler's next cycle.
  - transient: `attempts + 1` and `next_attempt_at` with backoff and jitter; once
    `max_attempts` is reached, it moves to `failed`.
  - permanent: `failed` with no retry.
- Revalidation right before publishing (`workers/outbox_revalidation.py`): deleted audio,
  an owner without an active account, or a superseded version, leaves the event
  `skipped` unpublished.
- Entrega al menos una vez, no exactamente una: si el commit falla después de encolar, el
  evento sigue pendiente y el siguiente ciclo lo reencola. `_job_id = outbox:<id>` solo
  deduplica mientras el job o su resultado siguen en Redis (`keep_result = 60 s`); pasado
  ese tiempo puede repetirse, así que los consumidores deben ser idempotentes.
- The batch is claimed and committed before Redis. A compare-and-set records the enqueue
  result afterward, matching the reconciler, so a slow Redis does not hold row locks.
"""

import logging
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from arq.connections import ArqRedis
from sqlalchemy import and_, or_, select, update
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
from app.workers.outbox_delivery import EnqueueResult, enqueue_ids
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
    """Motivo por el que un evento nunca podrá publicarse, o `None`."""
    if event.type == "sync_task_event":
        # J3.4 receives (task_id, task_version); anything else would be a future bug.
        if event.resource_type != "task":
            return "sync_non_task"
        if event.resource_version is None:
            return "missing_version"
        return None
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
    # Tests read the same ORM instances after this returns. Ending the claim transaction
    # must not expire them into a synchronous lazy load. The flag lives on the sync session.
    expire = db.sync_session.expire_on_commit
    db.sync_session.expire_on_commit = False
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
    ready: list[_Publish] = []
    held: dict[uuid.UUID, OutboxEvent] = {}
    for event, cleanup_status in rows:
        decision = _decide(event, cleanup_status)
        if decision.outcome is Outcome.UNKNOWN_TYPE:
            event.status = "skipped"
            report[Outcome.UNKNOWN_TYPE] += 1
            continue
        if not decision.publish or decision.job is None:
            report[decision.outcome] += 1
            continue
        rejection = (
            await revalidate(db, event) if event.resource_type == "audio" else None
        )
        if rejection is not None:
            logger.info(
                "outbox.revalidation_rejected event_id=%s reason=%s", event.id, rejection
            )
            event.status = "skipped"
            report[rejection] += 1
            continue
        held[event.id] = event
        ready.append(
            _Publish(
                event_id=event.id,
                job=decision.job,
                resource_id=event.resource_id,
                resource_version=event.resource_version,
                attempts=event.attempts,
                max_attempts=event.max_attempts,
            )
        )
    # Release the claim before Redis. Skips above become durable with this commit.
    await db.commit()

    try:
        for plan in ready:
            result = await enqueue_ids(
                redis,
                event_id=plan.event_id,
                job=plan.job,
                resource_id=plan.resource_id,
                resource_version=plan.resource_version,
                attempts=plan.attempts,
                max_attempts=plan.max_attempts,
                queue_name=queue_name,
                retry=retry,
                clock=clock,
            )
            if result.outcome is Outcome.DEDUPLICATED:
                report[Outcome.DEDUPLICATED] += 1
            counted = (
                Outcome.PUBLISH if result.outcome is Outcome.DEDUPLICATED else result.outcome
            )
            report[counted] += 1
            if result.outcome is not Outcome.DEFERRED_INFRA:
                _remember_enqueue(held.get(plan.event_id), result)
                await _record_enqueue(db, plan.event_id, result)
            if result.outcome is Outcome.DEFERRED_INFRA:
                # Redis is not responding: the rest of the batch would fail the same way,
                # and each failure blocks for a while.
                break
        return report
    finally:
        db.sync_session.expire_on_commit = expire


@dataclass(frozen=True)
class _Publish:
    event_id: uuid.UUID
    job: str
    resource_id: uuid.UUID
    resource_version: int | None
    attempts: int
    max_attempts: int


def _remember_enqueue(event: OutboxEvent | None, result: EnqueueResult) -> None:
    """Keep the caller's identity-map instance aligned with the compare-and-set."""
    if event is None or result.attempts is None:
        return
    event.attempts = result.attempts
    if result.status is not None:
        event.status = result.status
    if result.published_at is not None:
        event.published_at = result.published_at
    if result.next_attempt_at is not None:
        event.next_attempt_at = result.next_attempt_at


async def _record_enqueue(db: AsyncSession, event_id: uuid.UUID, result: EnqueueResult) -> None:
    """Compare-and-set the enqueue result. A row that left `pending` is left alone."""
    if result.attempts is None:
        return
    values: dict[str, object] = {"attempts": result.attempts}
    if result.status is not None:
        values["status"] = result.status
    if result.published_at is not None:
        values["published_at"] = result.published_at
    if result.next_attempt_at is not None:
        values["next_attempt_at"] = result.next_attempt_at
    await db.execute(
        update(OutboxEvent)
        .where(
            OutboxEvent.id == event_id,
            OutboxEvent.status == "pending",
            OutboxEvent.published_at.is_(None),
        )
        .values(**values)
    )
    await db.commit()

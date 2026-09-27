"""Dispatcher de la outbox (J1.5): de `outbox_events` en PostgreSQL a jobs ARQ en Redis.

Cada ciclo toma un lote con `FOR UPDATE SKIP LOCKED` (dos schedulers no publican el mismo
evento), aplica `services.outbox.decide` y encola solo IDs.

- El lote solo contiene eventos que pueden avanzar: los publicables y los que hay que
  descartar. Quedan fuera, sin ocupar huecos y `pending`, los tipos sin consumidor en S1 y
  los index/analyze cuyo último intento aún no tiene cleanup verificado. Así ningún
  backlog atascado deja sin despachar a los eventos listos más nuevos. `decide()` sigue
  aplicando el gate sobre lo seleccionado (defensa en profundidad, fail closed).
- Un tipo desconocido, un gate sobre un recurso que no es audio o un index sin versión
  pasan a `skipped` (terminal, nada se publica) en vez de reaparecer en cada ciclo.
- Entrega al menos una vez, no exactamente una: si el commit falla después de encolar, el
  evento sigue pendiente y el siguiente ciclo lo reencola. `_job_id = outbox:<id>` solo
  deduplica mientras el job o su resultado siguen en Redis (`keep_result = 60 s`); pasado
  ese tiempo puede repetirse, así que los consumidores deben ser idempotentes.
- El encolado ocurre con los row locks tomados: si Redis se degrada, la transacción se
  alarga y otros schedulers saltan esas filas (SKIP LOCKED). Aceptable con lote 100 y cron
  minutal; revisar si aparece presión real.
- En S1 el ciclo de vida termina en `dispatched`: sin ack del consumidor, reintentos,
  `attempts` ni `next_attempt_at` (J2.1).
"""
import logging
from collections import Counter
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


def _job_args(event: OutboxEvent, job: str) -> tuple[object, ...]:
    # Solo IDs y versión: nunca payload, audio, tokens ni texto.
    if job == "index":
        return (str(event.resource_id), event.resource_version)
    return (str(event.resource_id),)


def _malformed(event: OutboxEvent) -> str | None:
    """Motivo por el que un evento con gate nunca podrá publicarse, o `None`."""
    if event.type not in CLEANUP_GATED_TYPES:
        return None
    if event.resource_type != "audio":
        return "gated_non_audio"
    if event.type == "index_requested" and event.resource_version is None:
        return "missing_version"
    return None


async def dispatch_pending(
    db: AsyncSession, redis: ArqRedis, *, queue_name: str, batch_size: int = BATCH_SIZE
) -> Counter[Outcome]:
    """Publica los eventos listos del lote; devuelve cuántos cayeron en cada resultado."""
    gated = OutboxEvent.type.in_(GATED_TYPES)
    can_advance = or_(
        ~gated,  # desconocidos (se descartan) y cualquier tipo futuro con consumidor
        OutboxEvent.resource_type != "audio",  # gate imposible: se descarta
        and_(OutboxEvent.type == "index_requested", OutboxEvent.resource_version.is_(None)),
        LATEST_CLEANUP == "verified",
    )
    rows = (
        await db.execute(
            select(OutboxEvent, LATEST_CLEANUP)
            .where(
                OutboxEvent.status == "pending",
                OutboxEvent.published_at.is_(None),
                OutboxEvent.enabled.is_(True),
                OutboxEvent.type.not_in(WITHOUT_CONSUMER),
                can_advance,
            )
            .order_by(OutboxEvent.created_at, OutboxEvent.id)
            .limit(batch_size)
            .with_for_update(skip_locked=True, of=OutboxEvent)
        )
    ).all()

    report: Counter[Outcome] = Counter()
    now = datetime.now(UTC)
    for event, cleanup_status in rows:
        reason = _malformed(event)
        if reason is not None:
            decision = Decision(Outcome.UNKNOWN_TYPE)
            logger.warning("outbox.%s event_id=%s type=%s", reason, event.id, event.type)
        else:
            decision = decide(
                event.type,
                enabled=event.enabled,
                blocked_reason=event.blocked_reason,
                cleanup_status=cleanup_status,
            )
            if decision.outcome is Outcome.UNKNOWN_TYPE:
                logger.warning("outbox.unknown_type event_id=%s type=%s", event.id, event.type)
        report[decision.outcome] += 1

        if decision.outcome is Outcome.UNKNOWN_TYPE:
            event.status = "skipped"
            continue
        if not decision.publish or decision.job is None:
            continue
        job = await redis.enqueue_job(
            decision.job,
            *_job_args(event, decision.job),
            _job_id=f"outbox:{event.id}",
            _queue_name=queue_name,
        )
        if job is None:
            # `arq:job/result:<id>` ya existía: un ciclo anterior encoló y su commit falló.
            # El job ya está en cola, así que marcar dispatched es correcto.
            report[Outcome.DEDUPLICATED] += 1
        event.status = "dispatched"
        event.published_at = now
    return report

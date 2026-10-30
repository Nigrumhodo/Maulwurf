"""Encolado de un evento de la outbox y contabilidad de sus reintentos (J2.1).

Solo IDs y versión llegan a Redis: nunca payload, audio, tokens ni texto.
"""

import logging
from collections import Counter
from collections.abc import Callable
from datetime import datetime

from arq.connections import ArqRedis

from app.models import OutboxEvent
from app.services.outbox import Outcome
from app.services.outbox_retry import RetryPolicy
from app.workers.outbox_errors import ErrorKind, classify_enqueue_error

logger = logging.getLogger(__name__)


def job_args(event: OutboxEvent, job: str) -> tuple[object, ...]:
    # Solo IDs y versión: nunca payload, audio, tokens ni texto.
    if job == "index":
        return (str(event.resource_id), event.resource_version)
    return (str(event.resource_id),)


async def enqueue(
    redis: ArqRedis,
    event: OutboxEvent,
    job: str,
    *,
    queue_name: str,
    retry: RetryPolicy,
    clock: Callable[[], datetime],
    report: Counter[Outcome],
) -> Outcome:
    try:
        queued = await redis.enqueue_job(
            job,
            *job_args(event, job),
            _job_id=f"outbox:{event.id}",
            _queue_name=queue_name,
        )
    except Exception as exc:
        return _record_enqueue_failure(event, classify_enqueue_error(exc), exc, retry, clock)

    event.attempts += 1
    event.status = "dispatched"
    event.published_at = clock()
    if queued is None:
        # `arq:job/result:<id>` ya existía: un ciclo anterior encoló y su commit falló.
        # El job ya está en cola, así que marcar dispatched es correcto.
        report[Outcome.DEDUPLICATED] += 1
    return Outcome.PUBLISH


def _record_enqueue_failure(
    event: OutboxEvent,
    kind: ErrorKind,
    exc: BaseException,
    retry: RetryPolicy,
    clock: Callable[[], datetime],
) -> Outcome:
    # Solo el tipo: el mensaje puede incluir el DSN o datos del servidor.
    error = type(exc).__name__
    if kind is ErrorKind.INFRA:
        logger.warning("outbox.deferred_infra event_id=%s error=%s", event.id, error)
        return Outcome.DEFERRED_INFRA

    event.attempts += 1
    if kind is ErrorKind.PERMANENT or event.attempts >= event.max_attempts:
        event.status = "failed"
        logger.warning("outbox.failed event_id=%s attempts=%s error=%s", event.id,
                       event.attempts, error)
        return Outcome.FAILED
    event.next_attempt_at = clock() + retry.delay(event.attempts)
    logger.info("outbox.retry_scheduled event_id=%s attempts=%s error=%s", event.id,
                event.attempts, error)
    return Outcome.RETRY_SCHEDULED

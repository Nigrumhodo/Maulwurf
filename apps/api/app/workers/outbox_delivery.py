"""Enqueues an outbox event and tracks its retry accounting (J2.1).

Only IDs and the version reach Redis: never payload, audio, tokens or text.
"""

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from arq.connections import ArqRedis

from app.services.outbox import Outcome
from app.services.outbox_retry import RetryPolicy
from app.workers.outbox_errors import ErrorKind, classify_enqueue_error

logger = logging.getLogger(__name__)


def job_args(job: str, resource_id: uuid.UUID, resource_version: int | None) -> tuple[object, ...]:
    # Only IDs and version: never payload, audio, tokens or text.
    if job in {"index", "sync_task_event"}:
        return (str(resource_id), resource_version)
    return (str(resource_id),)


@dataclass(frozen=True)
class EnqueueResult:
    """Redis outcome plus the row fields a later compare-and-set may write."""

    outcome: Outcome
    attempts: int | None = None
    status: str | None = None
    published_at: datetime | None = None
    next_attempt_at: datetime | None = None


async def enqueue_ids(
    redis: ArqRedis,
    *,
    event_id: uuid.UUID,
    job: str,
    resource_id: uuid.UUID,
    resource_version: int | None,
    attempts: int,
    max_attempts: int,
    queue_name: str,
    retry: RetryPolicy,
    clock: Callable[[], datetime],
) -> EnqueueResult:
    """Enqueue one job. No database session is touched."""
    try:
        queued = await redis.enqueue_job(
            job,
            *job_args(job, resource_id, resource_version),
            _job_id=f"outbox:{event_id}",
            _queue_name=queue_name,
        )
    except Exception as exc:
        return _enqueue_failure(
            event_id, attempts, max_attempts, classify_enqueue_error(exc), exc, retry, clock
        )

    published_at = clock()
    if queued is None:
        # `arq:job/result:<id>` already existed: an earlier cycle enqueued it and its commit failed.
        # The job is already queued, so marking it dispatched is correct.
        return EnqueueResult(
            outcome=Outcome.DEDUPLICATED,
            attempts=attempts + 1,
            status="dispatched",
            published_at=published_at,
        )
    return EnqueueResult(
        outcome=Outcome.PUBLISH,
        attempts=attempts + 1,
        status="dispatched",
        published_at=published_at,
    )


def _enqueue_failure(
    event_id: uuid.UUID,
    attempts: int,
    max_attempts: int,
    kind: ErrorKind,
    exc: BaseException,
    retry: RetryPolicy,
    clock: Callable[[], datetime],
) -> EnqueueResult:
    # Only the type: the message may include the DSN or server data.
    error = type(exc).__name__
    if kind is ErrorKind.INFRA:
        logger.warning("outbox.deferred_infra event_id=%s error=%s", event_id, error)
        return EnqueueResult(outcome=Outcome.DEFERRED_INFRA)

    spent = attempts + 1
    if kind is ErrorKind.PERMANENT or spent >= max_attempts:
        logger.warning(
            "outbox.failed event_id=%s attempts=%s error=%s", event_id, spent, error
        )
        return EnqueueResult(outcome=Outcome.FAILED, attempts=spent, status="failed")
    logger.info(
        "outbox.retry_scheduled event_id=%s attempts=%s error=%s", event_id, spent, error
    )
    return EnqueueResult(
        outcome=Outcome.RETRY_SCHEDULED,
        attempts=spent,
        next_attempt_at=clock() + retry.delay(spent),
    )

"""Classifies errors raised when enqueuing an outbox job (J2.1).

Three classes, each handled differently:
- `INFRA`: Redis down, slow or out of memory. No event is at fault, so the cycle
  aborts without spending attempts (otherwise a Redis restart would kill the whole outbox, against
  ADR-0005: jobs may be lost, data must not be).
- `TRANSIENT`: failure of one specific command that may succeed on another attempt for the same
  event. It consumes an attempt, with backoff.
- `PERMANENT`: anything else (e.g. non-serializable arguments). Never retried.
"""

from enum import StrEnum

from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError
from redis.exceptions import TimeoutError as RedisTimeoutError

# Redis rejects every write (noeviction, loading or busy): this affects all events.
_INFRA_RESPONSE_PREFIXES = ("OOM", "LOADING", "BUSY")
_TRANSIENT_RESPONSE_PREFIXES = ("TRYAGAIN",)


class ErrorKind(StrEnum):
    INFRA = "infra"
    TRANSIENT = "transient"
    PERMANENT = "permanent"


def classify_enqueue_error(exc: BaseException) -> ErrorKind:
    """Classifies an `enqueue_job` error. When in doubt, `PERMANENT` (do not retry)."""
    if isinstance(exc, RedisConnectionError | RedisTimeoutError | OSError):
        return ErrorKind.INFRA
    if isinstance(exc, ResponseError):
        # Only the prefix: the full message may carry server data.
        message = str(exc)
        if message.startswith(_INFRA_RESPONSE_PREFIXES):
            return ErrorKind.INFRA
        if message.startswith(_TRANSIENT_RESPONSE_PREFIXES):
            return ErrorKind.TRANSIENT
    return ErrorKind.PERMANENT

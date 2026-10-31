"""Classifies errors raised while indexing one class (J2.2).

Transient errors (embeddings provider throttling or outage, database serialization failure or
deadlock, lost connection) are retried with backoff. Everything else is permanent: a retry
would fail the same way. Unknown errors are permanent too (fail closed), as in
`outbox_errors.classify_enqueue_error`.
"""

from typing import Any

import httpx
from sqlalchemy.exc import DBAPIError, OperationalError

from app.services.embeddings import EmbeddingProviderError, EmbeddingRateLimited
from app.workers.outbox_errors import ErrorKind

# serialization_failure, deadlock_detected: PostgreSQL asks the client to run the work again.
_RETRYABLE_SQLSTATES = frozenset({"40001", "40P01"})


def classify_index_error(exc: BaseException) -> ErrorKind:
    """Returns `TRANSIENT` for errors worth another attempt, `PERMANENT` for everything else."""
    if isinstance(exc, EmbeddingRateLimited | EmbeddingProviderError | httpx.TransportError):
        return ErrorKind.TRANSIENT
    if isinstance(exc, OperationalError):
        return ErrorKind.TRANSIENT
    if isinstance(exc, DBAPIError) and _sqlstate(exc) in _RETRYABLE_SQLSTATES:
        return ErrorKind.TRANSIENT
    return ErrorKind.PERMANENT


def _sqlstate(exc: DBAPIError) -> str | None:
    # The driver error may sit under SQLAlchemy's adapter; read the code from the first
    # layer that carries one. Only the code is read, never the message (it can echo data).
    candidate: Any = exc.orig
    for _ in range(3):
        if candidate is None:
            return None
        code = getattr(candidate, "sqlstate", None) or getattr(candidate, "pgcode", None)
        if isinstance(code, str):
            return code
        candidate = getattr(candidate, "__cause__", None)
    return None

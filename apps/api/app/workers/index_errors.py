"""Classifies errors raised while indexing one class (J2.2).

Transient errors (embeddings provider throttling or outage, database serialization failure or
deadlock, lost connection) are retried with backoff. Everything else is permanent: a retry
would fail the same way. Unknown errors are permanent too (fail closed), as in
`outbox_errors.classify_enqueue_error`.
"""

import httpx
from sqlalchemy.exc import DBAPIError, OperationalError

from app.services.embeddings import EmbeddingProviderError, EmbeddingRateLimited
from app.workers.outbox_errors import ErrorKind
from app.workers.retry import RETRYABLE_SQLSTATES, sqlstate_of


def classify_index_error(exc: BaseException) -> ErrorKind:
    """Returns `TRANSIENT` for errors worth another attempt, `PERMANENT` for everything else."""
    if isinstance(exc, EmbeddingRateLimited | EmbeddingProviderError | httpx.TransportError):
        return ErrorKind.TRANSIENT
    if isinstance(exc, OperationalError):
        return ErrorKind.TRANSIENT
    if isinstance(exc, DBAPIError) and sqlstate_of(exc) in RETRYABLE_SQLSTATES:
        return ErrorKind.TRANSIENT
    return ErrorKind.PERMANENT



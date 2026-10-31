"""Bounded backoff+jitter and error classification for analysis (S3.6, U-S3-SG-05).

The policy mirrors the indexing budget: one delivery plus at most three transient retries
(ESPECIFICACION J2.2). A permanent error is never retried. The delay is exponential with
bounded additive jitter from `services.outbox_retry.RetryPolicy`, so a provider outage is
not hammered by every class at the same instant.

The analyzer (`analyze(audio_id)`) is not part of `develop` yet (L3.6); this module is the
pure decision core it must consume, and the worker configuration uses `MAX_TRIES` so the
fail-closed budget cannot drift from the policy.
"""

from dataclasses import dataclass
from typing import Any, Final

import httpx
from sqlalchemy.exc import DBAPIError, OperationalError

from app.services.llm import LLMProviderError, LLMRateLimited
from app.services.outbox_retry import DEFAULT_RETRY, RetryPolicy
from app.workers.outbox_errors import ErrorKind

MAX_TRANSIENT_RETRIES: Final = 3
# One delivery plus three transient retries (ESPECIFICACION J2.2).
MAX_TRIES: Final = MAX_TRANSIENT_RETRIES + 1
RETRY_EXHAUSTED_CODE: Final = "retry_exhausted"
UNEXPECTED_CODE: Final = "analysis_unexpected"
# serialization_failure, deadlock_detected: PostgreSQL asks the client to run the work again.
_RETRYABLE_SQLSTATES = frozenset({"40001", "40P01"})


@dataclass(frozen=True)
class RetryDecision:
    """What the worker must do with one failed analysis delivery."""

    kind: ErrorKind
    retries: bool
    delay_seconds: float | None
    exhausted: bool
    error_code: str


def classify_analysis_error(exc: BaseException) -> ErrorKind:
    """Returns `TRANSIENT` for errors worth another attempt, `PERMANENT` otherwise."""
    if isinstance(exc, LLMRateLimited | LLMProviderError | httpx.TransportError):
        return ErrorKind.TRANSIENT
    if isinstance(exc, TimeoutError | ConnectionError):
        return ErrorKind.TRANSIENT
    if isinstance(exc, OperationalError):
        return ErrorKind.TRANSIENT
    if isinstance(exc, DBAPIError) and _sqlstate(exc) in _RETRYABLE_SQLSTATES:
        return ErrorKind.TRANSIENT
    return ErrorKind.PERMANENT


def retry_decision(
    exc: BaseException,
    *,
    job_try: int,
    policy: RetryPolicy = DEFAULT_RETRY,
    max_transient_retries: int = MAX_TRANSIENT_RETRIES,
) -> RetryDecision:
    """Decide retry vs. give up for the `job_try`-th delivery (ARQ starts at 1)."""
    if job_try < 1:
        raise ValueError("job_try must be >= 1")
    if max_transient_retries < 0:
        raise ValueError("max_transient_retries must be >= 0")
    kind = classify_analysis_error(exc)
    if kind is ErrorKind.PERMANENT:
        return RetryDecision(kind, False, None, False, _error_code(exc))
    if job_try > max_transient_retries:
        return RetryDecision(kind, False, None, True, RETRY_EXHAUSTED_CODE)
    delay = policy.delay(job_try).total_seconds()
    return RetryDecision(kind, True, delay, False, _error_code(exc))


def _error_code(exc: BaseException) -> str:
    code = getattr(exc, "code", None)
    return code if isinstance(code, str) and code else UNEXPECTED_CODE


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

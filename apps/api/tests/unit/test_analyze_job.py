"""L3.6 consumes `retry_decision`: a permanent error is not retried."""

from uuid import uuid4

import pytest
from arq.worker import Retry

from app.services.llm import LLMProviderError, LLMRateLimited
from app.workers.jobs.analyze import analyze
from app.workers.outbox_errors import ErrorKind
from app.workers.retry import RetryDecision


async def test_a_permanent_analysis_error_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail(*_args: object, **_kwargs: object) -> None:
        raise LLMProviderError("rejected", details={"status": 400})

    monkeypatch.setattr("app.workers.jobs.analyze.run_analyze", fail)
    await analyze({}, str(uuid4()))


async def test_a_transient_analysis_error_uses_the_bounded_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = LLMRateLimited("quota", details={"status": 429})

    async def fail(*_args: object, **_kwargs: object) -> None:
        raise error

    monkeypatch.setattr("app.workers.jobs.analyze.run_analyze", fail)
    monkeypatch.setattr(
        "app.workers.jobs.analyze.retry_decision",
        lambda exc, *, job_try: RetryDecision(
            ErrorKind.TRANSIENT, True, 1.25, False, "llm_rate_limited"
        ),
    )
    with pytest.raises(Retry) as caught:
        await analyze({"job_try": 1}, str(uuid4()))
    assert caught.value.defer_score == 1250

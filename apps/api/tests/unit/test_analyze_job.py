"""L3.6 consumes `retry_decision`: a permanent error is not retried."""

import asyncio
from uuid import uuid4

import pytest
from arq.worker import Retry

from app.services.analysis import ExtractionModel
from app.services.index_job import RunClaim
from app.services.llm import LLMProviderError, LLMRateLimited
from app.workers.jobs.analyze import _Claim, analyze, run_analyze
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


async def test_cancellation_records_the_run_and_still_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = _Claim(
        user_id=uuid4(),
        audio_id=uuid4(),
        subject_id=uuid4(),
        transcript_version=1,
        run=RunClaim(run_id=uuid4(), fencing_token=1),
    )
    recorded: list[str] = []

    async def claim_ok(*_args: object, **_kwargs: object) -> _Claim:
        return claim

    async def explode(*_args: object, **_kwargs: object) -> None:
        raise asyncio.CancelledError()

    async def record(_sessions: object, _run: object, code: str) -> None:
        recorded.append(code)

    monkeypatch.setattr("app.workers.jobs.analyze._claim_cross_tenant", claim_ok)
    monkeypatch.setattr("app.workers.jobs.analyze._extract_and_publish", explode)
    monkeypatch.setattr("app.workers.jobs.analyze._record", record)

    def sessions() -> None:
        raise AssertionError("the cancellation path must not open a new session itself")

    class _Unused:
        async def complete(self, *, system: str, user: str) -> str:
            raise AssertionError("the model is not called")

    def models() -> ExtractionModel:
        return _Unused()

    with pytest.raises(asyncio.CancelledError):
        await run_analyze(sessions, models, claim.audio_id, job_try=1)  # type: ignore[arg-type]
    assert recorded == ["analysis_unexpected"]

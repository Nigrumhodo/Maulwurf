"""J2.2 unit tests: error classification, retry budget and generation naming. No database.

The database behavior (idempotency, tombstone, lease, atomic activation) is covered by
`tests/integration/test_index_job.py` against real PostgreSQL.
"""

import uuid
from typing import Any

import httpx
import pytest
from arq.worker import Retry
from sqlalchemy.exc import DBAPIError, OperationalError

from app.services import index_job
from app.services.embeddings import (
    EmbeddingDimensionMismatch,
    EmbeddingInputTooLong,
    EmbeddingNotConfigured,
    EmbeddingProviderError,
    EmbeddingRateLimited,
)
from app.services.index_job import IndexSkip, generation_skip, index_version_for
from app.services.indexing import INDEX_ACTIVE, INDEX_BUILDING, INDEX_RETIRED, GenerationConflict
from app.workers import index_job as worker
from app.workers.index_errors import classify_index_error
from app.workers.outbox_errors import ErrorKind


class _DriverError(Exception):
    """Stands in for asyncpg/psycopg errors: only the `sqlstate` attribute is read."""

    def __init__(self, sqlstate: str) -> None:
        super().__init__("driver message that must never be logged")
        self.sqlstate = sqlstate


@pytest.mark.parametrize(
    "exc",
    [
        EmbeddingRateLimited("cuota"),
        EmbeddingProviderError("5xx"),
        httpx.ConnectError("sin conexión"),
        httpx.ReadTimeout("lento"),
        OperationalError("SELECT 1", None, _DriverError("08006")),
        DBAPIError("SELECT 1", None, _DriverError("40001")),
        DBAPIError("SELECT 1", None, _DriverError("40P01")),
    ],
)
def test_transient_errors_are_retried(exc: BaseException) -> None:
    assert classify_index_error(exc) is ErrorKind.TRANSIENT


@pytest.mark.parametrize(
    "exc",
    [
        EmbeddingInputTooLong("demasiado largo"),
        EmbeddingDimensionMismatch("dimensión"),
        EmbeddingNotConfigured("sin configurar"),
        GenerationConflict("otra generación"),
        DBAPIError("SELECT 1", None, _DriverError("23505")),
        ValueError("bug"),
    ],
)
def test_permanent_errors_are_not_retried(exc: BaseException) -> None:
    assert classify_index_error(exc) is ErrorKind.PERMANENT


def test_index_version_is_stable_per_class_version() -> None:
    audio_id = uuid.uuid4()
    first = index_version_for(audio_id, 2)
    assert first == index_version_for(audio_id, 2)
    assert first != index_version_for(audio_id, 3)
    assert first != index_version_for(uuid.uuid4(), 2)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (INDEX_ACTIVE, IndexSkip.ALREADY_INDEXED),
        (INDEX_RETIRED, IndexSkip.SUPERSEDED),
        (INDEX_BUILDING, None),
    ],
)
def test_existing_generation_decides_the_skip(status: str, expected: IndexSkip | None) -> None:
    assert generation_skip(status) is expected


async def _run_index_failing(
    monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> list[bool]:
    """Replaces the pipeline with one that fails; returns `retries_left` for every call."""
    seen: list[bool] = []

    async def failing(*args: Any, retries_left: bool, **kwargs: Any) -> None:
        seen.append(retries_left)
        raise error

    monkeypatch.setattr(worker, "run_index", failing)
    return seen


async def _deliver(job_try: int) -> None:
    await worker.index({"job_try": job_try}, str(uuid.uuid4()), 1)


@pytest.mark.parametrize("job_try", [1, 2, 3])
async def test_transient_failure_is_retried_while_budget_remains(
    monkeypatch: pytest.MonkeyPatch, job_try: int
) -> None:
    seen = await _run_index_failing(monkeypatch, EmbeddingRateLimited("cuota"))

    with pytest.raises(Retry):
        await _deliver(job_try)
    assert seen == [True]


async def test_transient_failure_gives_up_after_three_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = await _run_index_failing(monkeypatch, EmbeddingRateLimited("cuota"))

    await _deliver(worker.MAX_TRIES)  # 1 delivery + 3 retries: the fourth try is the last
    assert seen == [False]


async def test_permanent_failure_is_never_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = await _run_index_failing(monkeypatch, EmbeddingInputTooLong("largo"))

    await _deliver(1)  # returns normally: ARQ does not retry a logged permanent failure
    assert seen == [True]


def test_arq_budget_matches_the_retry_policy() -> None:
    # Keeps the ARQ budget and the in-job check in step: one delivery plus three retries.
    assert worker.MAX_TRIES == 4
    assert index_job.LEASE.total_seconds() > worker.JOB_TIMEOUT_S

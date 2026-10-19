"""U-S2-JL-02: batch embeddings against a compatible provider (L2.2, M6/D5).

No network and no database: `httpx.MockTransport` serves the responses and an injected
`sleeper` records the delays (it never really sleeps). Docstring and `U-` level per
`docs/PLAN_TESTS.md` §0.4.6: a test that needed the network would be `I-`/`P-`.
"""

import json
import logging
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.services import embeddings as embeddings_module
from app.services.embeddings import (
    EmbeddingDimensionMismatch,
    EmbeddingInputTooLong,
    EmbeddingNotConfigured,
    EmbeddingProviderError,
    EmbeddingRateLimited,
    HttpEmbeddingProvider,
)

DIMENSION = 1536
BASE_URL = "https://embeddings.test/v1"
ENDPOINT = f"{BASE_URL}/embeddings"


class _Sleeper:
    """Stand-in for `asyncio.sleep`: records the backoff delays without waiting."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def _provider(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    batch_size: int = 100,
    max_retries: int = 3,
    dimension: int = DIMENSION,
    sleeper: _Sleeper | None = None,
) -> HttpEmbeddingProvider:
    return HttpEmbeddingProvider(
        base_url=BASE_URL,
        api_key="test-key",
        model="text-embedding-3-small",
        dimension=dimension,
        batch_size=batch_size,
        max_retries=max_retries,
        timeout_seconds=5.0,
        transport=httpx.MockTransport(handler),
        sleeper=sleeper or _Sleeper(),
        jitter=lambda: 0.0,
    )


def _vectors(*, count: int, dimension: int = DIMENSION) -> list[dict[str, Any]]:
    return [{"index": index, "embedding": [0.5] * dimension} for index in range(count)]


def _ok(request: httpx.Request) -> httpx.Response:
    payload = json.loads(request.content)
    return httpx.Response(
        200,
        json={
            "data": _vectors(count=len(payload["input"])),
            "model": payload["model"],
            "usage": {"total_tokens": len(payload["input"])},
        },
    )


# --- 1. Bounded batches ------------------------------------------------------------------


async def test_embeddings_splits_into_bounded_batches() -> None:
    """The batch size is a hard ceiling: 250 texts with batch 100 give 100/100/50."""
    sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sizes.append(len(json.loads(request.content)["input"]))
        return _ok(request)

    vectors = await _provider(handler, batch_size=100).embed([f"texto {i}" for i in range(250)])

    assert sizes == [100, 100, 50]
    assert len(vectors) == 250


# --- 2. Order from the `index` field ----------------------------------------------------


async def test_embeddings_preserve_order_by_index_field() -> None:
    """Out-of-order vectors are reordered by `index`, not by their position in `data`."""
    texts = [f"t{i}" for i in range(3)]

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        ordered = [
            {"index": index, "embedding": [float(index)] * DIMENSION}
            for index in range(len(payload["input"]))
        ]
        return httpx.Response(200, json={"data": list(reversed(ordered)), "model": "m"})

    vectors = await _provider(handler).embed(texts)

    assert [vector[0] for vector in vectors] == [0.0, 1.0, 2.0]


# --- 3. Empty input ---------------------------------------------------------------------


async def test_embeddings_empty_input_skips_provider() -> None:
    """With no texts the provider is not called: zero requests and zero vectors."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return _ok(request)

    assert await _provider(handler).embed([]) == []
    assert calls == []


# --- 4. Registered dimension ------------------------------------------------------------


async def test_embeddings_reject_dimension_mismatch() -> None:
    """A provider of another dimension is a hard error, not a silent truncation."""

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        return httpx.Response(
            200, json={"data": _vectors(count=len(payload["input"]), dimension=10), "model": "m"}
        )

    with pytest.raises(EmbeddingDimensionMismatch) as failure:
        await _provider(handler).embed(["uno"])

    assert failure.value.details == {"expected": DIMENSION, "observed": 10}


# --- 4b. Malformed vector components ---------------------------------------------------


@pytest.mark.parametrize("component", ["no-numero", float("nan"), float("inf")])
async def test_embeddings_reject_malformed_components(component: Any) -> None:
    """A non-numeric or non-finite component is a typed error, never a bare `ValueError`.

    A `NaN` would otherwise reach the cosine HNSW index and poison the distances without
    any error at all.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        data = [
            {"index": index, "embedding": [component] * DIMENSION}
            for index in range(len(payload["input"]))
        ]
        # `text=`: httpx refuses to *encode* NaN/Infinity with `json=`, but Python's decoder
        # accepts them on the way in — exactly the malformed body a provider could send.
        return httpx.Response(200, text=json.dumps({"data": data}))

    with pytest.raises(EmbeddingProviderError) as failure:
        await _provider(handler).embed(["uno"])

    assert failure.value.code == "embedding_provider_error"
    assert failure.value.details["index"] == 0


# --- 5. Bounded retries and `Retry-After` -----------------------------------------------


async def test_embeddings_retry_transient_errors_within_budget() -> None:
    """Two 5xx and then 200: three attempts, the batch still succeeds."""
    calls: list[int] = []
    sleeper = _Sleeper()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(len(json.loads(request.content)["input"]))
        if len(calls) < 3:
            return httpx.Response(503, text="zzz")
        return _ok(request)

    vectors = await _provider(handler, sleeper=sleeper).embed(["uno"])

    assert len(calls) == 3
    assert len(vectors) == 1
    assert sleeper.delays == [0.5, 1.0]


async def test_embeddings_exhausted_budget_is_typed() -> None:
    """Exhausting the retry budget is `embedding_provider_error`, not a loop."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(500, text="boom")

    with pytest.raises(EmbeddingProviderError) as failure:
        await _provider(handler, max_retries=2).embed(["uno"])

    assert len(calls) == 3
    assert failure.value.code == "embedding_provider_error"
    assert failure.value.details["attempts"] == 3


async def test_embeddings_rate_limit_honours_retry_after() -> None:
    """429 with `Retry-After` uses that delay; once quota is exhausted the error is typed."""
    sleeper = _Sleeper()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "7"}, text="slow down")

    with pytest.raises(EmbeddingRateLimited) as failure:
        await _provider(handler, max_retries=1, sleeper=sleeper).embed(["uno"])

    assert sleeper.delays == [7.0]
    assert failure.value.code == "embedding_rate_limited"


# --- 6. Client errors: not a single retry -----------------------------------------------


@pytest.mark.parametrize("status", [400, 401, 403, 404, 413])
async def test_embeddings_never_retry_client_errors(status: int) -> None:
    """A request error is never retried: a single call."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(status)
        return httpx.Response(status, text="bad request")

    with pytest.raises(EmbeddingProviderError) as failure:
        await _provider(handler, max_retries=3).embed(["uno"])

    assert len(calls) == 1
    assert failure.value.details["status"] == status


# --- 7. Text over the model limit -------------------------------------------------------


async def test_embeddings_map_overlong_input() -> None:
    """A 400 for the model token limit is `embedding_input_too_long` (an L2.1/D5 defect)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": {"message": "This model's maximum context length is 8191 tokens"}},
        )

    with pytest.raises(EmbeddingInputTooLong) as failure:
        await _provider(handler).embed(["uno"])

    assert failure.value.code == "embedding_input_too_long"


# --- 8. No content in logs --------------------------------------------------------------


async def test_embeddings_logs_contain_no_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Adapter logs carry only model/batch/status: never the text and never the key."""
    secret_text = "contenido-privado-de-clase-1234"
    with caplog.at_level(logging.DEBUG):
        await _provider(_ok).embed([secret_text])

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert caplog.records
    assert secret_text not in logged
    assert "test-key" not in logged


# --- Provider configuration -------------------------------------------------------------


def test_embeddings_fail_closed_without_provider_settings() -> None:
    """Without base_url/api_key there is no default provider: `embedding_not_configured`."""
    values: dict[str, Any] = {
        "secret_key": "test-secret",
        "encryption_key": "test-encryption",
        "oauth_state_secret": "test-oauth-state",
    }
    bare = Settings(_env_file=None, **values)

    with pytest.raises(EmbeddingNotConfigured) as failure:
        HttpEmbeddingProvider.from_settings(bare)

    assert failure.value.code == "embedding_not_configured"
    assert embeddings_module.is_configured(bare) is False

    configured = Settings(
        _env_file=None,
        embedding_base_url=BASE_URL,
        embedding_api_key="test-key",
        **values,
    )
    provider = HttpEmbeddingProvider.from_settings(configured, sleeper=_Sleeper())

    assert (provider.model, provider.dimension, provider.batch_size) == (
        configured.embedding_model,
        configured.embedding_dimension,
        configured.embedding_batch_size,
    )
    assert embeddings_module.is_configured(configured) is True

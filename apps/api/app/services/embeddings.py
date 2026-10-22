"""Batch embeddings against an embeddings-API compatible provider (L2.2, M6).

Ticket decisions and why:

- **No new dependency:** `httpx` is already in `pyproject.toml`; the client posts to
  `<embedding_base_url>/embeddings` with `{"input": [...], "model": ...}` and reads
  `{"data": [{"index", "embedding"}]}`. The `EmbeddingProvider` protocol lets the
  implementation change (e.g. LiteLLM) without touching `indexing.py`; the provider and the
  dimension are fixed by D5/D3-Texto (ESPECIFICACION §12).
- **Fail closed:** without `embedding_base_url` + `embedding_api_key` there is no default
  provider and no fake vectors: `EmbeddingNotConfigured` (`embedding_not_configured`).
- **Dimension and model are never mixed:** every response is validated against the registered
  dimension; `embeddings.vector` is `vector(1536)` in migration 0002, so a provider of
  another dimension is a hard error, not a silent truncation.
- **Bounded budget (guardrail 10):** exponential backoff + jitter retries only for 429/5xx/
  network, `Retry-After` honoured, at most `embedding_max_retries`; 400/401/403/404/409/413/
  422 are never retried (409 is a definitive state conflict, not a transient failure).
- **Bounded concurrency (PR #71):** every batch shares one `httpx.AsyncClient` (one connection
  pool, no per-batch handshake) and batches run through an `asyncio.Semaphore` of
  `embedding_max_concurrency` slots — 1 by default (sequential) until D5 fixes the provider
  quota. `asyncio.gather` reassembles the results in batch order.
- **No content in logs:** only model, batch size, status, attempt and delay. Never the text,
  the vectors or the key.

Response order is rebuilt from the `index` field, never from the position: a provider can
return the vectors out of order, and misassigning them would corrupt the index.
"""

import asyncio
import logging
import math
import secrets
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol

import httpx

from app.core.config import Settings
from app.core.config import settings as live_settings

logger = logging.getLogger(__name__)

EMBEDDINGS_PATH = "embeddings"
# Backoff base with a 2^(attempt-1) factor plus multiplicative jitter. `Retry-After` is capped
# too: a huge header must not block the job indefinitely.
_BACKOFF_BASE_SECONDS = 0.5
_MAX_RETRY_AFTER_SECONDS = 60.0
_RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})
# 400 is handled separately: it can be an overlong batch (model limit) or a malformed request,
# and that distinction changes the error code the job publishes.
_TOO_LONG_MARKERS = ("context length", "maximum context", "too long", "too many tokens")


class EmbeddingError(Exception):
    """Typed adapter error. `code` is the same one J2.2 will publish in the run."""

    code = "embedding_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class EmbeddingNotConfigured(EmbeddingError):
    code = "embedding_not_configured"


class EmbeddingProviderError(EmbeddingError):
    code = "embedding_provider_error"


class EmbeddingRateLimited(EmbeddingError):
    code = "embedding_rate_limited"


class EmbeddingDimensionMismatch(EmbeddingError):
    code = "embedding_dimension_mismatch"


class EmbeddingInputTooLong(EmbeddingError):
    code = "embedding_input_too_long"


class EmbeddingProvider(Protocol):
    """Minimal contract consumed by `indexing.py` (any provider can implement it)."""

    @property
    def model(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    @property
    def batch_size(self) -> int: ...

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


async def embed_query(provider: EmbeddingProvider, text: str) -> list[float]:
    """Embed exactly one query string and validate the adapter's one-vector contract.

    Keeping this helper outside `SearchService` lets callers run the network call while no
    database session is open and still share the same strict response validation.
    """
    vectors = await provider.embed([text])
    if len(vectors) != 1:
        raise EmbeddingProviderError(
            "El proveedor devolvió un número de vectores inesperado.",
            details={"expected": 1, "observed": len(vectors)},
        )
    return vectors[0]


def _unit_interval() -> float:
    return secrets.randbelow(1_000_000) / 1_000_000


def _backoff(attempt: int, jitter: Callable[[], float]) -> float:
    base = _BACKOFF_BASE_SECONDS * float(1 << (attempt - 1))
    drawn = min(max(jitter(), 0.0), 1.0)
    return base + (drawn * base)


def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        seconds = float(raw.strip())
    except ValueError:
        return None  # HTTP date or non-numeric value: fall back to our own backoff.
    if seconds < 0:
        return None
    return min(seconds, _MAX_RETRY_AFTER_SECONDS)


def is_configured(settings: Settings = live_settings) -> bool:
    return settings.embedding_base_url is not None and settings.embedding_api_key is not None


class HttpEmbeddingProvider:
    """Batched client: one shared HTTP client, bounded retries and bounded concurrency."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        dimension: int,
        batch_size: int,
        max_retries: int = 3,
        timeout_seconds: float = 30.0,
        max_concurrency: int = 1,
        transport: httpx.AsyncBaseTransport | None = None,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = _unit_interval,
    ) -> None:
        self._endpoint = f"{base_url.rstrip('/')}/{EMBEDDINGS_PATH}"
        self._api_key = api_key
        self._model = model
        self._dimension = dimension
        self._batch_size = batch_size
        self._max_attempts = max_retries + 1
        self._timeout = timeout_seconds
        # 1 = sequential: the default until D5 fixes the provider quota (guardrail 10).
        self._max_concurrency = max(1, max_concurrency)
        self._transport = transport
        self._sleep = sleeper
        self._jitter = jitter

    @classmethod
    def from_settings(
        cls, settings: Settings = live_settings, **overrides: Any
    ) -> "HttpEmbeddingProvider":
        if settings.embedding_base_url is None or settings.embedding_api_key is None:
            raise EmbeddingNotConfigured(
                "Falta configurar el proveedor de embeddings (MAULWURF_EMBEDDING_*)."
            )
        return cls(
            base_url=str(settings.embedding_base_url),
            api_key=settings.embedding_api_key.get_secret_value(),
            model=settings.embedding_model,
            dimension=settings.embedding_dimension,
            batch_size=settings.embedding_batch_size,
            max_retries=settings.embedding_max_retries,
            timeout_seconds=settings.embedding_timeout_seconds,
            max_concurrency=settings.embedding_max_concurrency,
            **overrides,
        )

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def batch_size(self) -> int:
        return self._batch_size

    @property
    def max_concurrency(self) -> int:
        return self._max_concurrency

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """One vector per text, in input order. An empty batch never calls the provider.

        All batches share a single `httpx.AsyncClient` (one connection pool) and are limited by
        an `asyncio.Semaphore`: at most `max_concurrency` requests in flight. `asyncio.gather`
        returns the batch results in argument order, so the vectors stay aligned with the input.
        With `return_exceptions=True` no batch is left orphaned when another one fails: the
        loop below re-raises the first failure in batch order after the in-flight batches end.
        """
        pending = list(texts)
        if not pending:
            return []
        batches = [
            pending[start : start + self._batch_size]
            for start in range(0, len(pending), self._batch_size)
        ]
        slots = asyncio.Semaphore(self._max_concurrency)
        async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout) as client:
            grouped = await asyncio.gather(
                *(self._embed_with_slot(client, slots, batch) for batch in batches),
                return_exceptions=True,
            )
        vectors: list[list[float]] = []
        for item in grouped:
            if isinstance(item, BaseException):
                raise item
            vectors.extend(item)
        return vectors

    async def _embed_with_slot(
        self, client: httpx.AsyncClient, slots: asyncio.Semaphore, batch: Sequence[str]
    ) -> list[list[float]]:
        """One batch holding one semaphore slot, so at most `max_concurrency` run at once."""
        async with slots:
            return await self._embed_batch(client, batch)

    async def _embed_batch(
        self, client: httpx.AsyncClient, batch: Sequence[str]
    ) -> list[list[float]]:
        payload = {"input": list(batch), "model": self._model}
        headers = {"Authorization": f"Bearer {self._api_key}"}
        attempt = 0
        while True:
            attempt += 1
            try:
                response = await client.post(self._endpoint, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                if attempt >= self._max_attempts:
                    raise EmbeddingProviderError(
                        "El proveedor de embeddings no respondió.",
                        details={"attempts": attempt, "error": type(exc).__name__},
                    ) from exc
                await self._pause(attempt, None)
                continue
            if response.status_code == httpx.codes.OK:
                vectors = self._parse(response, expected=len(batch))
                logger.info(
                    "embeddings.batch model=%s size=%d dimension=%d",
                    self._model,
                    len(batch),
                    self._dimension,
                )
                return vectors
            if response.status_code in _RETRYABLE_STATUS:
                if attempt >= self._max_attempts:
                    raise self._exhausted(response, attempt)
                await self._pause(attempt, _retry_after_seconds(response))
                continue
            raise self._client_error(response)

    async def _pause(self, attempt: int, retry_after: float | None) -> None:
        delay = retry_after if retry_after is not None else _backoff(attempt, self._jitter)
        logger.warning(
            "embeddings.retry model=%s attempt=%d delay=%.2f", self._model, attempt, delay
        )
        await self._sleep(delay)

    def _exhausted(self, response: httpx.Response, attempt: int) -> EmbeddingError:
        details: dict[str, Any] = {"status": response.status_code, "attempts": attempt}
        if response.status_code == httpx.codes.TOO_MANY_REQUESTS:
            retry_after = _retry_after_seconds(response)
            if retry_after is not None:
                details["retry_after"] = retry_after
            return EmbeddingRateLimited(
                "El proveedor de embeddings rechazó la petición por cuota.", details=details
            )
        return EmbeddingProviderError(
            "El proveedor de embeddings devolvió un error temporal.", details=details
        )

    def _client_error(self, response: httpx.Response) -> EmbeddingError:
        details = {"status": response.status_code}
        if response.status_code == httpx.codes.BAD_REQUEST:
            body = response.text.casefold()
            if any(marker in body for marker in _TOO_LONG_MARKERS):
                # The chunk exceeds the model limit: a chunking/D5 defect, not a quota issue.
                return EmbeddingInputTooLong(
                    "El texto excede el límite de tokens del modelo de embeddings.", details=details
                )
        return EmbeddingProviderError(
            "El proveedor de embeddings rechazó la petición.", details=details
        )

    def _parse(self, response: httpx.Response, *, expected: int) -> list[list[float]]:
        try:
            body = response.json()
        except ValueError as exc:
            raise EmbeddingProviderError(
                "Respuesta no JSON del proveedor de embeddings.",
                details={"status": response.status_code},
            ) from exc
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list) or len(data) != expected:
            raise EmbeddingProviderError(
                "Respuesta del proveedor con un número de vectores inesperado.",
                details={"status": response.status_code, "expected": expected},
            )
        slots: dict[int, list[float]] = {}
        for item in data:
            if not isinstance(item, dict):
                raise EmbeddingProviderError("Elemento inválido en la respuesta de embeddings.")
            index = item.get("index")
            embedding = item.get("embedding")
            if not isinstance(index, int) or not 0 <= index < expected or index in slots:
                raise EmbeddingProviderError(
                    "Índice de vector inválido o duplicado.",
                    details={"status": response.status_code},
                )
            if not isinstance(embedding, list) or len(embedding) != self._dimension:
                observed = len(embedding) if isinstance(embedding, list) else None
                raise EmbeddingDimensionMismatch(
                    "El proveedor devolvió vectores de otra dimensión.",
                    details={"expected": self._dimension, "observed": observed},
                )
            slots[index] = self._components(embedding, index=index, status=response.status_code)
        return [slots[position] for position in range(expected)]

    def _components(self, embedding: list[Any], *, index: int, status: int) -> list[float]:
        """Validate what a vector carries: numbers, finite, and of the registered shape.

        `float()` would otherwise leak a bare `ValueError` out of the typed error contract
        that the job publishes, and a `NaN`/`Inf` component would poison the cosine HNSW
        distances instead of failing the batch.
        """
        try:
            values = [float(component) for component in embedding]
        except (TypeError, ValueError) as exc:
            raise EmbeddingProviderError(
                "El proveedor devolvió un vector con componentes no numéricos.",
                details={"status": status, "index": index},
            ) from exc
        if not all(math.isfinite(value) for value in values):
            raise EmbeddingProviderError(
                "El proveedor devolvió un vector con componentes no finitos.",
                details={"status": status, "index": index},
            )
        return values

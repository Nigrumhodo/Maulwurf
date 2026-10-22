"""OpenAI-compatible streaming LLM adapter (L2.5, M7).

The provider is optional and fail-closed: without `MAULWURF_LLM_BASE_URL` and
`MAULWURF_LLM_API_KEY` there is no default model and no fake completion. Tests inject a
deterministic fake; `httpx.MockTransport` exercises this adapter without network access.

Only the text deltas leave this module. The transcript/question never appear in a log line:
errors carry status codes, attempt numbers and provider error codes, never content.
"""

import asyncio
import json
import logging
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.core.config import Settings
from app.core.config import settings as live_settings

logger = logging.getLogger(__name__)

CHAT_COMPLETIONS_PATH = "chat/completions"
_BACKOFF_BASE_SECONDS = 0.5
_MAX_RETRY_AFTER_SECONDS = 60.0
_RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})
_CONTEXT_MARKERS = (
    "context length",
    "maximum context",
    "too many tokens",
    "max_tokens",
    "context window",
)


class LLMError(Exception):
    """Typed adapter error; the SSE layer maps `code` to the terminal event."""

    code = "llm_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class LLMNotConfigured(LLMError):
    code = "llm_not_configured"


class LLMProviderError(LLMError):
    code = "llm_provider_error"


class LLMRateLimited(LLMError):
    code = "llm_rate_limited"


class LLMContextExceeded(LLMError):
    code = "llm_context_exceeded"


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str


class LLMProvider(Protocol):
    """Minimal contract consumed by `RAGService`; any provider can implement it."""

    @property
    def model(self) -> str: ...

    @property
    def context_window(self) -> int: ...

    def stream(
        self, messages: Sequence[ChatMessage], *, max_tokens: int
    ) -> AsyncIterator[str]: ...


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
        return None
    if seconds < 0:
        return None
    return min(seconds, _MAX_RETRY_AFTER_SECONDS)


class HttpChatProvider:
    """Streaming client for LiteLLM/OpenAI `POST <base>/chat/completions`."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        context_window: int,
        max_retries: int = 2,
        timeout_seconds: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = _unit_interval,
    ) -> None:
        if context_window <= 0:
            raise ValueError("context_window debe ser positivo")
        self._endpoint = f"{base_url.rstrip('/')}/{CHAT_COMPLETIONS_PATH}"
        self._api_key = api_key
        self._model = model
        self._context_window = context_window
        self._max_attempts = max_retries + 1
        self._timeout = timeout_seconds
        self._transport = transport
        self._sleep = sleeper
        self._jitter = jitter

    @classmethod
    def from_settings(
        cls, settings: Settings = live_settings, **overrides: Any
    ) -> "HttpChatProvider":
        if settings.llm_base_url is None or settings.llm_api_key is None:
            raise LLMNotConfigured("Falta configurar el proveedor LLM (MAULWURF_LLM_*).")
        if settings.llm_model_window is None:
            raise LLMNotConfigured("Falta MAULWURF_LLM_MODEL_WINDOW.")
        return cls(
            base_url=str(settings.llm_base_url),
            api_key=settings.llm_api_key.get_secret_value(),
            model=settings.llm_model,
            context_window=settings.llm_model_window,
            max_retries=settings.llm_max_retries,
            timeout_seconds=settings.llm_timeout_seconds,
            **overrides,
        )

    @property
    def model(self) -> str:
        return self._model

    @property
    def context_window(self) -> int:
        return self._context_window

    async def stream(
        self, messages: Sequence[ChatMessage], *, max_tokens: int
    ) -> AsyncIterator[str]:
        """Yield text deltas in order. Retry only before the first non-empty delta.

        A retry after text was emitted would duplicate part of the answer, so the adapter
        fails closed from the first visible token onward.
        """
        if max_tokens < 1:
            raise LLMProviderError(
                "El presupuesto de salida del LLM no es positivo.",
                details={"reason": "invalid_max_tokens"},
            )
        payload = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "text/event-stream",
        }
        attempt = 0
        first_delta_sent = False
        while True:
            attempt += 1
            try:
                async with httpx.AsyncClient(
                    transport=self._transport,
                    timeout=httpx.Timeout(self._timeout, connect=min(10.0, self._timeout)),
                ) as client:
                    async with client.stream(
                        "POST", self._endpoint, json=payload, headers=headers
                    ) as response:
                        if response.status_code != httpx.codes.OK:
                            error = await self._status_error(response, attempt)
                            if (
                                response.status_code in _RETRYABLE_STATUS
                                and attempt < self._max_attempts
                            ):
                                await self._pause(attempt, _retry_after_seconds(response))
                                continue
                            raise error
                        async for line in response.aiter_lines():
                            data = _sse_data(line)
                            if data is None:
                                continue
                            if data == "[DONE]":
                                return
                            delta = _parse_delta(data)
                            if delta is None:
                                continue
                            first_delta_sent = True
                            yield delta
                        return
            except httpx.HTTPError as exc:
                if first_delta_sent or attempt >= self._max_attempts:
                    raise LLMProviderError(
                        "El proveedor LLM no respondió.",
                        details={"attempts": attempt, "error": type(exc).__name__},
                    ) from exc
                await self._pause(attempt, None)

    async def _pause(self, attempt: int, retry_after: float | None) -> None:
        delay = retry_after if retry_after is not None else _backoff(attempt, self._jitter)
        logger.warning("llm.retry model=%s attempt=%d delay=%.2f", self._model, attempt, delay)
        await self._sleep(delay)

    async def _status_error(self, response: httpx.Response, attempt: int) -> LLMError:
        details: dict[str, Any] = {"status": response.status_code, "attempts": attempt}
        if response.status_code == httpx.codes.BAD_REQUEST:
            body = (await response.aread()).decode("utf-8", errors="replace").casefold()
            if any(marker in body for marker in _CONTEXT_MARKERS):
                return LLMContextExceeded(
                    "El contexto excede la ventana del modelo.", details=details
                )
        if response.status_code == httpx.codes.TOO_MANY_REQUESTS:
            retry_after = _retry_after_seconds(response)
            if retry_after is not None:
                details["retry_after"] = retry_after
            return LLMRateLimited(
                "El proveedor LLM rechazó la petición por cuota.", details=details
            )
        return LLMProviderError(
            "El proveedor LLM rechazó la petición.", details=details
        )


def _sse_data(line: str) -> str | None:
    """Extract the data payload of one SSE line; comments/other fields are ignored."""
    if not line or line.startswith(":"):
        return None
    if not line.startswith("data:"):
        return None
    return line[len("data:") :].strip()


def _parse_delta(data: str) -> str | None:
    """Validate one `chat.completion.chunk` and return its text delta, if any."""
    try:
        payload: Any = json.loads(data)
    except ValueError as exc:
        raise LLMProviderError(
            "El proveedor LLM devolvió un evento SSE no JSON.",
            details={"reason": "invalid_sse_json"},
        ) from exc
    if not isinstance(payload, dict):
        raise LLMProviderError(
            "El proveedor LLM devolvió un evento SSE inválido.",
            details={"reason": "invalid_sse_payload"},
        )
    if "error" in payload:
        # Never forward the provider's error body: it may echo prompt content.
        raise LLMProviderError(
            "El proveedor LLM devolvió un error dentro del stream.",
            details={"reason": "stream_error"},
        )
    choices = payload.get("choices")
    if not isinstance(choices, list):
        raise LLMProviderError(
            "El proveedor LLM devolvió una respuesta sin choices.",
            details={"reason": "missing_choices"},
        )
    if not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        raise LLMProviderError(
            "El proveedor LLM devolvió un choice inválido.",
            details={"reason": "invalid_choice"},
        )
    delta = first.get("delta")
    if not isinstance(delta, dict):
        return None
    content = delta.get("content")
    if content is None:
        return None
    if not isinstance(content, str):
        raise LLMProviderError(
            "El proveedor LLM devolvió un delta no textual.",
            details={"reason": "non_text_delta"},
        )
    return content

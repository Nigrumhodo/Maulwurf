"""U-S2-JL-07: streaming LLM adapter, retries and fail-closed provider errors (L2.5)."""

import json
from collections.abc import AsyncIterator

import httpx
import pytest

from app.core.config import Settings
from app.services import llm as llm_module
from app.services.llm import (
    ChatMessage,
    HttpChatProvider,
    LLMContextExceeded,
    LLMNotConfigured,
    LLMProviderError,
    LLMRateLimited,
    close_llm_http_client,
    get_llm_http_client,
)

SECRETS = {
    "secret_key": "s3cret-value-for-test",
    "encryption_key": "enc-value-for-test",
    "oauth_state_secret": "state-value-for-test",
}


def _provider(
    handler: httpx.MockTransport,
    *,
    max_retries: int = 2,
    calls: list[float] | None = None,
) -> HttpChatProvider:
    async def sleeper(delay: float) -> None:
        if calls is not None:
            calls.append(delay)

    return HttpChatProvider(
        base_url="https://llm.example/v1",
        api_key="test-key",
        model="test-model",
        context_window=8192,
        max_retries=max_retries,
        transport=handler,
        sleeper=sleeper,
        jitter=lambda: 0.5,
    )


def _sse(*deltas: str) -> bytes:
    frames = [
        f'data: {json.dumps({"choices": [{"delta": {"content": delta}}]})}\n\n'
        for delta in deltas
    ]
    return "".join(frames).encode() + b"data: [DONE]\n\n"


async def _collect(provider: HttpChatProvider) -> list[str]:
    return [
        delta
        async for delta in provider.stream(
            [ChatMessage(role="user", content="pregunta")], max_tokens=64
        )
    ]


async def test_streams_text_deltas_and_sends_the_expected_payload() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse("Hola", " mundo"),
        )

    provider = _provider(httpx.MockTransport(handler))
    deltas = await _collect(provider)

    assert deltas == ["Hola", " mundo"]
    assert len(seen) == 1
    payload = json.loads(seen[0].content)
    assert payload == {
        "model": "test-model",
        "messages": [{"role": "user", "content": "pregunta"}],
        "stream": True,
        "max_tokens": 64,
    }
    assert seen[0].url == "https://llm.example/v1/chat/completions"
    assert seen[0].headers["authorization"] == "Bearer test-key"


async def test_retries_rate_limit_before_the_first_delta() -> None:
    calls = 0
    sleeps: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "0.25"})
        return httpx.Response(200, content=_sse("ok"))

    provider = _provider(httpx.MockTransport(handler), calls=sleeps)
    assert await _collect(provider) == ["ok"]
    assert calls == 2
    assert sleeps == [0.25]


async def test_exhausted_retries_surface_provider_error() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    provider = _provider(httpx.MockTransport(handler), max_retries=1)
    with pytest.raises(LLMProviderError):
        await _collect(provider)
    assert calls == 2


async def test_context_400_is_not_retried_and_has_its_own_error() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            400,
            content=b'{"error":{"message":"maximum context length exceeded"}}',
        )

    provider = _provider(httpx.MockTransport(handler))
    with pytest.raises(LLMContextExceeded):
        await _collect(provider)
    assert calls == 1


async def test_non_retryable_auth_error_is_a_provider_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    provider = _provider(httpx.MockTransport(handler))
    with pytest.raises(LLMProviderError):
        await _collect(provider)


class _BrokenStream(httpx.AsyncByteStream):
    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b'data: {"choices":[{"delta":{"content":"parcial"}}]}\n\n'
        raise httpx.ReadError("connection dropped")


async def test_does_not_retry_after_text_was_emitted() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, stream=_BrokenStream())

    provider = _provider(httpx.MockTransport(handler))
    received: list[str] = []
    with pytest.raises(LLMProviderError):
        async for delta in provider.stream(
            [ChatMessage(role="user", content="pregunta")], max_tokens=64
        ):
            received.append(delta)
    assert received == ["parcial"]
    assert calls == 1


async def test_invalid_sse_json_is_a_provider_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"data: not-json\n\n")

    provider = _provider(httpx.MockTransport(handler))
    with pytest.raises(LLMProviderError):
        await _collect(provider)


async def test_from_settings_requires_both_url_key_and_window() -> None:
    with pytest.raises(LLMNotConfigured):
        HttpChatProvider.from_settings(Settings(_env_file=None, **SECRETS))

    settings = Settings(
        _env_file=None,
        **SECRETS,
        llm_base_url="https://llm.example/v1",
        llm_api_key="test-key",
        llm_model_window=8192,
    )
    provider = HttpChatProvider.from_settings(settings)
    assert provider.model == "gpt-4o-mini"
    assert provider.context_window == 8192


def test_settings_reject_partial_provider_and_impossible_budget() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="llm_base_url"):
        Settings(
            _env_file=None,
            **SECRETS,
            llm_base_url="https://llm.example/v1",
        )
    with pytest.raises(ValidationError, match="llm_model_window"):
        Settings(
            _env_file=None,
            **SECRETS,
            llm_base_url="https://llm.example/v1",
            llm_api_key="test-key",
            llm_model_window=1000,
        )


async def test_rate_limited_is_the_typed_error_for_429_after_retries() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    provider = _provider(httpx.MockTransport(handler), max_retries=0)
    with pytest.raises(LLMRateLimited):
        await _collect(provider)


async def test_shared_http_client_is_bounded_reused_and_closed() -> None:
    await close_llm_http_client()
    try:
        first = get_llm_http_client()
        assert first is get_llm_http_client()
        assert llm_module.LLM_HTTP_LIMITS.max_connections == 20
        assert llm_module.LLM_HTTP_LIMITS.max_keepalive_connections == 10

        await close_llm_http_client()
        assert first.is_closed

        second = get_llm_http_client()
        assert second is not first
    finally:
        await close_llm_http_client()

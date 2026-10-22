"""L2.5 QA: bounded concurrent SSE streams per user."""

import asyncio
import uuid
from collections.abc import AsyncGenerator

import pytest

from app.core.errors import ApiError
from app.routers import chat as chat_router


def setup_function() -> None:
    chat_router._active.clear()


def teardown_function() -> None:
    chat_router._active.clear()


def test_a_user_cannot_exceed_the_two_stream_limit() -> None:
    user_id = uuid.uuid4()
    chat_router._acquire_stream(user_id)
    chat_router._acquire_stream(user_id)

    with pytest.raises(ApiError) as raised:
        chat_router._acquire_stream(user_id)

    assert raised.value.status_code == 429
    assert raised.value.code == "rate_limited"
    assert raised.value.message == "Demasiadas conexiones de chat simultáneas."

    chat_router._release_stream(user_id)
    chat_router._acquire_stream(user_id)
    assert chat_router._active[user_id] == 2


async def test_release_happens_when_the_stream_finishes() -> None:
    user_id = uuid.uuid4()
    chat_router._acquire_stream(user_id)

    async def events() -> AsyncGenerator[str, None]:
        yield "one"
        yield "two"

    wrapped = chat_router._release_stream_when_done(events(), user_id)
    assert [event async for event in wrapped] == ["one", "two"]
    assert user_id not in chat_router._active


async def test_release_and_producer_close_happen_on_early_consumer_close() -> None:
    user_id = uuid.uuid4()
    chat_router._acquire_stream(user_id)
    producer_closed = False

    async def events() -> AsyncGenerator[str, None]:
        nonlocal producer_closed
        try:
            yield "one"
            await asyncio.sleep(3600)
        finally:
            producer_closed = True

    wrapped = chat_router._release_stream_when_done(events(), user_id)
    assert await anext(wrapped) == "one"
    await wrapped.aclose()
    assert producer_closed
    assert user_id not in chat_router._active

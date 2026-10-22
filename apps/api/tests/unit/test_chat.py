"""L2.5 QA: stream-slot cleanup on response exit paths."""

import uuid
from collections.abc import AsyncGenerator

import fakeredis.aioredis
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from app.routers import chat as chat_router
from app.services.rate_limiter import acquire_stream, stream_key


async def test_chat_slot_release_on_early_abort() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    user_id = uuid.uuid4()
    await acquire_stream(redis, user_id)
    slot = chat_router._Slot(redis, user_id)

    async def events() -> AsyncGenerator[str, None]:
        yield "never iterated"

    stream = events()
    response = StreamingResponse(
        stream,
        media_type="text/event-stream",
        background=BackgroundTask(slot.release),
    )

    assert await redis.get(stream_key(user_id)) == "1"
    background = response.background
    assert background is not None
    await background()
    assert await redis.get(stream_key(user_id)) is None
    assert slot.released
    await stream.aclose()
    await redis.aclose()


async def test_chat_slot_release_is_idempotent() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    user_id = uuid.uuid4()
    await acquire_stream(redis, user_id)
    await acquire_stream(redis, user_id)
    slot = chat_router._Slot(redis, user_id)

    await slot.release()
    await slot.release()

    assert await redis.get(stream_key(user_id)) == "1"
    await redis.aclose()

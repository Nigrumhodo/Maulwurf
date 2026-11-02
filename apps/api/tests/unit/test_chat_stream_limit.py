"""L2.5 QA: Redis-backed concurrent SSE stream slots."""

import asyncio
import uuid

import fakeredis.aioredis
import pytest
from redis.exceptions import RedisError

from app.core.errors import ApiError
from app.services.rate_limiter import (
    MAX_STREAMS_PER_USER,
    STREAM_SLOT_TTL_SECONDS,
    acquire_stream,
    release_stream,
    stream_key,
)


async def test_chat_redis_stream_limit_is_atomic_and_has_a_crash_ttl() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    user_id = uuid.uuid4()

    results = await asyncio.gather(
        *(acquire_stream(redis, user_id) for _ in range(MAX_STREAMS_PER_USER + 1)),
        return_exceptions=True,
    )

    rejected = [result for result in results if isinstance(result, ApiError)]
    assert len(rejected) == 1
    assert rejected[0].status_code == 429
    assert rejected[0].code == "rate_limited"
    assert await redis.get(stream_key(user_id)) == str(MAX_STREAMS_PER_USER)
    ttl = await redis.ttl(stream_key(user_id))
    assert 0 < ttl <= STREAM_SLOT_TTL_SECONDS

    await release_stream(redis, user_id)
    await release_stream(redis, user_id)
    assert await redis.get(stream_key(user_id)) is None
    await redis.aclose()


async def test_release_floors_at_zero_after_duplicate_cleanup() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    user_id = uuid.uuid4()

    assert await release_stream(redis, user_id) == 0
    await acquire_stream(redis, user_id)
    assert await release_stream(redis, user_id) == 0
    assert await release_stream(redis, user_id) == 0
    assert await redis.get(stream_key(user_id)) is None
    await redis.aclose()


async def test_stream_limit_outage_is_503_with_retry_after() -> None:
    """A4.6: a limiter outage fails closed and tells the client when to retry."""

    class _Down:
        async def eval(self, *args: object) -> int:
            raise RedisError("down")

    with pytest.raises(ApiError) as exc:
        await acquire_stream(_Down(), uuid.uuid4())  # type: ignore[arg-type]

    assert exc.value.status_code == 503
    assert exc.value.code == "capacity_unavailable"
    assert exc.value.headers["Retry-After"] == str(STREAM_SLOT_TTL_SECONDS)

"""FastAPI dependency for Redis-backed request state.

The client is created per request and closed on exit: sharing one asyncio client across
event loops (tests, reloads) is fragile, and the API only needs Redis for short-lived
counters. A bounded socket timeout keeps a degraded Redis from stalling a request; `/readyz`
already reports Redis as a hard readiness dependency.
"""

from collections.abc import AsyncIterator
from typing import Annotated

import redis.asyncio as redis
from fastapi import Depends

from app.core.config import settings

# One Redis budget for the request client, `/readyz`, ARQ `conn_timeout`, and the
# Compose Redis healthcheck (`timeout: 2s`). ARQ's field is whole seconds and
# defaults to 1; leaving it unset makes workers give up while the API is still ready.
REDIS_TIMEOUT_S = 2


async def get_redis() -> AsyncIterator[redis.Redis]:
    client = redis.Redis.from_url(
        str(settings.redis_url),
        decode_responses=True,
        socket_connect_timeout=REDIS_TIMEOUT_S,
        socket_timeout=REDIS_TIMEOUT_S,
    )
    try:
        yield client
    finally:
        await client.aclose()


RedisDep = Annotated[redis.Redis, Depends(get_redis)]

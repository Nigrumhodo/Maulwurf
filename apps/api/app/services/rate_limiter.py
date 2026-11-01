"""Atomic per-user chat stream limiter backed by Redis (L2.5).

The limit is cluster-wide, unlike the old process-local counter: two API replicas sharing a
Redis instance enforce the same two-stream budget. Both operations are single Lua scripts so
a request can never observe a half-applied increment and an over-limit acquire rolls its own
increment back atomically.

Keys contain only the opaque user UUID and expire on their own. They never carry prompts,
answers, connection ids or any other chat content.
"""

import logging
import uuid
from collections.abc import Awaitable
from typing import cast

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.errors import ApiError

logger = logging.getLogger(__name__)

MAX_STREAMS_PER_USER = 2
STREAM_SLOT_TTL_SECONDS = 300
STREAM_KEY_PREFIX = "chat:streams"

# KEYS[1] = per-user counter. ARGV[1] = maximum slots, ARGV[2] = TTL seconds.
# A request first claims its slot; only if the counter is over the limit does it decrement
# and, when that returns the counter to zero, delete the key. The rollback is in the same
# script, so a failed acquire cannot leave a phantom slot behind.
_ACQUIRE_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
redis.call('EXPIRE', KEYS[1], ARGV[2])
if current > tonumber(ARGV[1]) then
    current = redis.call('DECR', KEYS[1])
    if current <= 0 then
        redis.call('DEL', KEYS[1])
    end
    return 0
end
return 1
"""

# A release is also atomic and floors at zero: a duplicate release (generator finally plus
# response background) can never leave a negative counter. When the last slot disappears the
# key is deleted instead of keeping a zero-valued counter alive until its TTL.
_RELEASE_SCRIPT = """
local current = redis.call('GET', KEYS[1])
if not current or tonumber(current) <= 1 then
    redis.call('DEL', KEYS[1])
    return 0
end
return redis.call('DECR', KEYS[1])
"""


def stream_key(user_id: uuid.UUID) -> str:
    """Return the Redis key for one user's live stream slots."""
    return f"{STREAM_KEY_PREFIX}:{user_id}"


async def acquire_stream(
    client: Redis,
    user_id: uuid.UUID,
    *,
    limit: int = MAX_STREAMS_PER_USER,
    ttl_seconds: int = STREAM_SLOT_TTL_SECONDS,
) -> None:
    """Claim one stream slot or raise a typed `429`/`503`.

    A Redis failure fails closed: allowing an unbounded number of SSE streams because the
    limiter is down would turn a dependency outage into an unbounded LLM/embedding bill.
    """
    if limit < 1:
        raise ValueError("limit debe ser >= 1")
    if ttl_seconds < 1:
        raise ValueError("ttl_seconds debe ser >= 1")
    try:
        acquired = await cast(
            Awaitable[int | str | bytes],
            client.eval(
                _ACQUIRE_SCRIPT,
                1,
                stream_key(user_id),
                str(limit),
                str(ttl_seconds),
            ),
        )
    except RedisError as exc:
        logger.warning("chat.stream_limit_unavailable error=%s", type(exc).__name__)
        raise ApiError(
            503,
            "capacity_unavailable",
            "El limitador de chat no está disponible; inténtalo más tarde.",
            {"reason": "chat_stream_limit_unavailable"},
            headers={"Retry-After": str(ttl_seconds)},
        ) from exc

    if int(acquired) != 1:
        raise ApiError(
            429,
            "rate_limited",
            "Demasiadas conexiones de chat simultáneas.",
            {"limit": limit, "window_seconds": ttl_seconds},
            # M7 fixes the budget (two streams) and DISENO §3.1 requires the header on 429.
            headers={"Retry-After": str(ttl_seconds)},
        )


async def release_stream(client: Redis, user_id: uuid.UUID) -> int:
    """Release one slot; return the remaining count (zero when the key is gone).

    Redis errors are propagated so the caller can log them; the slot key's TTL is the final
    cleanup path if a release cannot reach Redis.
    """
    remaining = await cast(
        Awaitable[int | str | bytes], client.eval(_RELEASE_SCRIPT, 1, stream_key(user_id))
    )
    return int(remaining)

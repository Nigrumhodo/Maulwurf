"""Per-user search quota backed by Redis (A2.11).

Every `GET /search` that reaches an active index costs one embeddings call. The quota is a
fixed-window counter checked before the provider is invoked, so one user cannot exhaust the
shared provider budget. Keys carry only the opaque user UUID and expire with the window;
queries and snippets never enter Redis.
"""

import logging
import math
import time
import uuid

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.errors import ApiError

logger = logging.getLogger(__name__)

SEARCH_QUOTA_WINDOW_SECONDS = 60
SEARCH_QUOTA_KEY_PREFIX = "search:quota"


def _now() -> float:
    return time.time()


def _retry_after(now: float) -> int:
    remaining = SEARCH_QUOTA_WINDOW_SECONDS - (now % SEARCH_QUOTA_WINDOW_SECONDS)
    return max(1, math.ceil(remaining))


async def enforce_search_quota(
    client: Redis,
    user_id: uuid.UUID,
    *,
    limit: int | None = None,
) -> None:
    """Raise `429 rate_limited` when the user exceeds the per-minute search budget."""
    maximum = settings.search_rate_limit_per_minute if limit is None else limit
    now = _now()
    window = int(now // SEARCH_QUOTA_WINDOW_SECONDS)
    key = f"{SEARCH_QUOTA_KEY_PREFIX}:{user_id}:{window}"
    try:
        async with client.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, SEARCH_QUOTA_WINDOW_SECONDS + 1)
            count, _ = await pipe.execute()
    except RedisError as exc:
        # Fail closed with a typed error: silently skipping the quota would let a degraded
        # Redis turn into an unbounded provider bill.
        logger.warning("search.quota_unavailable error=%s", type(exc).__name__)
        raise ApiError(
            503,
            "capacity_unavailable",
            "El limitador de búsquedas no está disponible; inténtalo más tarde.",
            {"reason": "search_quota_unavailable"},
        ) from exc
    if int(count) > maximum:
        raise ApiError(
            429,
            "rate_limited",
            "Límite de búsquedas por minuto alcanzado; inténtalo más tarde.",
            {"limit": maximum, "window_seconds": SEARCH_QUOTA_WINDOW_SECONDS},
            headers={"Retry-After": str(_retry_after(now))},
        )

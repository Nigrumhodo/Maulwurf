"""Redis-backed fixed-window limits for the sensitive endpoints (A4.6, M1/M2/M7/M9).

Every limit this module enforces comes from a recorded decision, never a number invented here:

- chat: at most two concurrent SSE streams per user (M7, `services/rate_limiter.py`);
- search: the provisional 30/min quota (M6/D5, `services/search_quota.py`);
- Calendar: one manual refresh per 60 s (M5, J3.8);
- upload: quota/slot per D4, disabled until a limit is published (A2.3/A2.14).

Authentication and the incremental consent have **no** numeric decision yet (D4/D5 are
open), so the fixed-window mechanism ships wired but configuration-driven: an unset value
means "no limit", exactly like the upload quota in A2.3. A deployment that freezes a number
enables it without a code change.

Keys carry only `mw:limits:<scope>:<opaque identity>:<window>` and expire with the window.
The auth identity is a SHA-256 of the ASGI peer address, never the raw IP (ADR-0005 forbids
PII in Redis). A Redis failure fails closed with `503 capacity_unavailable` and
`Retry-After`, because skipping the limit would turn a dependency outage into an open gate.
"""

from __future__ import annotations

import hashlib
import logging
import math
import time
import uuid

from fastapi import Request
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.errors import ApiError

logger = logging.getLogger(__name__)

ENDPOINT_WINDOW_SECONDS = 60
KEY_PREFIX = "mw:limits"
AUTH_SCOPE = "auth"
INTEGRATION_SCOPE = "integrations"


def _now() -> float:
    return time.time()


def retry_after(now: float, window_seconds: int) -> int:
    """Seconds until the current fixed window closes; never zero (a 0 means "retry now")."""
    remaining = window_seconds - (now % window_seconds)
    return max(1, math.ceil(remaining))


def client_identity(request: Request) -> str:
    """Opaque identity for an unauthenticated request: hash of the ASGI peer address.

    The peer address is what the application actually sees. Behind Caddy that is the proxy
    unless the deployment trusts forwarded headers (J4.7); this module never reads
    `X-Forwarded-For`, which a client can forge.
    """
    host = request.client.host if request.client is not None else "unknown"
    return hashlib.sha256(host.encode("utf-8", "surrogatepass")).hexdigest()[:32]


def window_key(scope: str, identity: str, window: int) -> str:
    """Redis key of one fixed window; small, opaque and self-expiring."""
    return f"{KEY_PREFIX}:{scope}:{identity}:{window}"


async def _increment(client: Redis, key: str, window_seconds: int) -> int:
    async with client.pipeline(transaction=True) as pipe:
        pipe.incr(key)
        pipe.expire(key, window_seconds + 1)
        count, _ = await pipe.execute()
    return int(count)


async def enforce_fixed_window(
    client: Redis,
    *,
    scope: str,
    identity: str,
    limit: int,
    window_seconds: int = ENDPOINT_WINDOW_SECONDS,
    now: float | None = None,
) -> None:
    """Raise `429` when `identity` exceeds `limit` inside the current window.

    The counter is incremented before the decision so concurrent requests cannot all pass
    with a stale read. An over-limit request keeps consuming the window; that is the
    standard fixed-window trade-off and the TTL bounds it.
    """
    if limit < 1:
        raise ValueError("limit debe ser >= 1")
    if window_seconds < 1:
        raise ValueError("window_seconds debe ser >= 1")
    clock = _now() if now is None else now
    window = int(clock // window_seconds)
    key = window_key(scope, identity, window)
    try:
        count = await _increment(client, key, window_seconds)
    except RedisError as exc:
        logger.warning("rate_limit.unavailable scope=%s error=%s", scope, type(exc).__name__)
        raise ApiError(
            503,
            "capacity_unavailable",
            "El limitador de peticiones no está disponible; inténtalo más tarde.",
            {"reason": f"{scope}_rate_limit_unavailable"},
            headers={"Retry-After": str(window_seconds)},
        ) from exc
    if count > limit:
        raise ApiError(
            429,
            "rate_limited",
            "Demasiadas solicitudes; inténtalo más tarde.",
            {"limit": limit, "window_seconds": window_seconds, "scope": scope},
            headers={"Retry-After": str(retry_after(clock, window_seconds))},
        )


async def enforce_auth_limit(
    client: Redis, request: Request, *, now: float | None = None
) -> None:
    """Limit the OAuth start/callback endpoints per peer address when one is configured."""
    limit = settings.auth_rate_limit_per_minute
    if limit is None:
        return
    await enforce_fixed_window(
        client,
        scope=AUTH_SCOPE,
        identity=client_identity(request),
        limit=limit,
        now=now,
    )


async def enforce_integration_limit(
    client: Redis, user_id: uuid.UUID, *, now: float | None = None
) -> None:
    """Limit the incremental consent start per authenticated user when configured."""
    limit = settings.integration_rate_limit_per_minute
    if limit is None:
        return
    await enforce_fixed_window(
        client,
        scope=INTEGRATION_SCOPE,
        identity=str(user_id),
        limit=limit,
        now=now,
    )


__all__ = [
    "AUTH_SCOPE",
    "ENDPOINT_WINDOW_SECONDS",
    "INTEGRATION_SCOPE",
    "client_identity",
    "enforce_auth_limit",
    "enforce_fixed_window",
    "enforce_integration_limit",
    "retry_after",
    "window_key",
]

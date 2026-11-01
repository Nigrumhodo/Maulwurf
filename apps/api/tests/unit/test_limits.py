"""A4.6/I-S4-AN-06 support: fixed-window limits without a live Redis.

The module only enforces numbers the project published. Authentication and the incremental
consent have no numeric decision yet (D4/D5 open), so these cases prove the mechanism and
the `None` = "no limit" rule instead of inventing a default.
"""

import uuid
from typing import Any, cast

import fakeredis.aioredis
import pytest
from fastapi import Request
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.errors import ApiError
from app.services import limits
from app.services.limits import (
    ENDPOINT_WINDOW_SECONDS,
    enforce_auth_limit,
    enforce_fixed_window,
    enforce_integration_limit,
    retry_after,
    window_key,
)


def _request(host: str = "203.0.113.9") -> Request:
    return Request({"type": "http", "client": (host, 12345), "headers": []})


async def test_fixed_window_allows_the_limit_and_rejects_the_next_with_retry_after() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    now = 120.0

    for _ in range(2):
        await enforce_fixed_window(
            redis, scope="auth", identity="alice", limit=2, now=now
        )

    with pytest.raises(ApiError) as exc:
        await enforce_fixed_window(
            redis, scope="auth", identity="alice", limit=2, now=now
        )

    assert exc.value.status_code == 429
    assert exc.value.code == "rate_limited"
    assert exc.value.details == {
        "limit": 2,
        "window_seconds": ENDPOINT_WINDOW_SECONDS,
        "scope": "auth",
    }
    assert exc.value.headers["Retry-After"] == str(ENDPOINT_WINDOW_SECONDS)
    await redis.aclose()


async def test_fixed_window_separates_scopes_identities_and_windows() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    await enforce_fixed_window(redis, scope="auth", identity="alice", limit=1, now=120.0)
    with pytest.raises(ApiError):
        await enforce_fixed_window(redis, scope="auth", identity="alice", limit=1, now=120.0)

    # Another identity, another scope and the next window each start from zero.
    await enforce_fixed_window(redis, scope="auth", identity="bob", limit=1, now=120.0)
    await enforce_fixed_window(redis, scope="integrations", identity="alice", limit=1, now=120.0)
    await enforce_fixed_window(redis, scope="auth", identity="alice", limit=1, now=180.0)
    await redis.aclose()


async def test_fixed_window_counts_the_increment_atomically() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    for _ in range(3):
        await enforce_fixed_window(redis, scope="search", identity="alice", limit=3, now=1.0)

    with pytest.raises(ApiError):
        await enforce_fixed_window(redis, scope="search", identity="alice", limit=3, now=1.0)
    assert await redis.get(window_key("search", "alice", 0)) == "4"
    await redis.aclose()


async def test_fixed_window_fails_closed_with_a_typed_503_when_redis_is_down() -> None:
    class _Down:
        def pipeline(self, *, transaction: bool) -> Any:
            raise RedisError("down")

    with pytest.raises(ApiError) as exc:
        await enforce_fixed_window(
            cast(Redis, _Down()), scope="auth", identity="alice", limit=1
        )

    assert exc.value.status_code == 503
    assert exc.value.code == "capacity_unavailable"
    assert exc.value.details == {"reason": "auth_rate_limit_unavailable"}
    assert int(exc.value.headers["Retry-After"]) >= 1


def test_retry_after_is_bounded_to_the_window() -> None:
    assert retry_after(120.0, ENDPOINT_WINDOW_SECONDS) == 60
    assert retry_after(179.5, ENDPOINT_WINDOW_SECONDS) == 1
    assert retry_after(179.999, ENDPOINT_WINDOW_SECONDS) == 1


def test_client_identity_is_an_opaque_hash_and_ignores_the_port() -> None:
    first = limits.client_identity(_request("203.0.113.9"))
    second = limits.client_identity(_request("203.0.113.9"))

    assert first == second
    assert len(first) == 32
    assert "203.0.113.9" not in first
    assert limits.client_identity(_request("198.51.100.7")) != first


async def test_auth_limit_is_disabled_by_default_and_never_touches_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "auth_rate_limit_per_minute", None)

    class _RefusesCommands:
        def pipeline(self, *, transaction: bool) -> Any:
            raise AssertionError("un límite no publicado no debe consultar Redis")

    await enforce_auth_limit(cast(Redis, _RefusesCommands()), _request())


async def test_configured_auth_limit_rejects_with_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "auth_rate_limit_per_minute", 1)
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    await enforce_auth_limit(redis, _request(), now=10.0)
    with pytest.raises(ApiError) as exc:
        await enforce_auth_limit(redis, _request(), now=10.0)

    assert exc.value.status_code == 429
    assert exc.value.details["scope"] == "auth"
    assert int(exc.value.headers["Retry-After"]) >= 1
    await redis.aclose()


async def test_integration_limit_is_per_user_and_disabled_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "integration_rate_limit_per_minute", None)
    alice = uuid.uuid4()
    await enforce_integration_limit(cast(Redis, object()), alice)

    monkeypatch.setattr(settings, "integration_rate_limit_per_minute", 1)
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    await enforce_integration_limit(redis, alice, now=10.0)
    await enforce_integration_limit(redis, uuid.uuid4(), now=10.0)
    with pytest.raises(ApiError) as exc:
        await enforce_integration_limit(redis, alice, now=10.0)

    assert exc.value.details["scope"] == "integrations"
    assert exc.value.details["limit"] == 1
    await redis.aclose()


async def test_a_non_positive_limit_is_a_programming_error() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    with pytest.raises(ValueError):
        await enforce_fixed_window(redis, scope="auth", identity="x", limit=0)
    with pytest.raises(ValueError):
        await enforce_fixed_window(redis, scope="auth", identity="x", limit=1, window_seconds=0)
    await redis.aclose()

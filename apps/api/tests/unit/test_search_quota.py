"""U-S2-JL-06 support: per-user search quota without a live Redis (A2.11)."""

import uuid
from typing import Any, cast

import pytest
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.errors import ApiError
from app.services import search_quota
from app.services.search_quota import (
    SEARCH_QUOTA_WINDOW_SECONDS,
    enforce_search_quota,
)

USER_ID = uuid.uuid4()


class _Pipeline:
    def __init__(self, client: "_FakeRedis") -> None:
        self.client = client
        self.key = ""

    async def __aenter__(self) -> "_Pipeline":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    def incr(self, key: str) -> None:
        self.key = key

    def expire(self, key: str, seconds: int) -> None:
        self.client.expirations[key] = seconds

    async def execute(self) -> list[Any]:
        if self.client.error is not None:
            raise self.client.error
        self.client.counts[self.key] = self.client.counts.get(self.key, 0) + 1
        return [self.client.counts[self.key], True]


class _FakeRedis:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.counts: dict[str, int] = {}
        self.expirations: dict[str, int] = {}
        self.error = error

    def pipeline(self, *, transaction: bool) -> _Pipeline:
        assert transaction is True
        return _Pipeline(self)


def _fake(**kwargs: Any) -> Redis:
    return cast(Redis, _FakeRedis(**kwargs))


async def test_search_quota_rejects_over_limit_with_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(search_quota, "_now", lambda: 120.0)
    client = _fake()

    await enforce_search_quota(client, USER_ID, limit=2)
    await enforce_search_quota(client, USER_ID, limit=2)
    with pytest.raises(ApiError) as exc:
        await enforce_search_quota(client, USER_ID, limit=2)

    assert exc.value.status_code == 429
    assert exc.value.code == "rate_limited"
    assert exc.value.headers["Retry-After"] == str(SEARCH_QUOTA_WINDOW_SECONDS)
    assert exc.value.details == {
        "limit": 2,
        "window_seconds": SEARCH_QUOTA_WINDOW_SECONDS,
    }


async def test_search_quota_is_per_user_and_resets_with_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 120.0
    monkeypatch.setattr(search_quota, "_now", lambda: now)
    client = _fake()
    other_user = uuid.uuid4()

    await enforce_search_quota(client, USER_ID, limit=1)
    await enforce_search_quota(client, other_user, limit=1)

    now = 180.0
    await enforce_search_quota(client, USER_ID, limit=1)


async def test_search_quota_fails_closed_when_redis_is_down() -> None:
    client = _fake(error=RedisError("down"))

    with pytest.raises(ApiError) as exc:
        await enforce_search_quota(client, USER_ID, limit=1)

    assert exc.value.status_code == 503
    assert exc.value.code == "capacity_unavailable"
    assert exc.value.headers["Retry-After"] == str(SEARCH_QUOTA_WINDOW_SECONDS)
    assert exc.value.code == "capacity_unavailable"
    assert exc.value.details == {"reason": "search_quota_unavailable"}

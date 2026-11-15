"""Shared backoff for the embedding and chat HTTP adapters.

One implementation so a `Retry-After` cap or a jitter draw cannot drift between the two
clients. This is not an embedding wrapper: each adapter still owns its request, its status
codes and its typed errors.
"""

import secrets
from collections.abc import Callable

import httpx

BACKOFF_BASE_SECONDS = 0.5
MAX_RETRY_AFTER_SECONDS = 60.0


def unit_interval() -> float:
    return secrets.randbelow(1_000_000) / 1_000_000


def backoff(attempt: int, jitter: Callable[[], float]) -> float:
    base = BACKOFF_BASE_SECONDS * float(1 << (attempt - 1))
    drawn = min(max(jitter(), 0.0), 1.0)
    return base + (drawn * base)


def retry_after_seconds(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        seconds = float(raw.strip())
    except ValueError:
        return None  # HTTP date or non-numeric value: fall back to our own backoff.
    if seconds < 0:
        return None
    return min(seconds, MAX_RETRY_AFTER_SECONDS)

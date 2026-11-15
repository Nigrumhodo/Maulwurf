"""Shared backoff for the embedding, chat, and Google Calendar HTTP adapters.

One implementation so a `Retry-After` cap or a jitter draw cannot drift between clients.
This is not a request wrapper: each adapter still owns its request, its status codes and
its typed errors.
"""

import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

BACKOFF_BASE_SECONDS = 0.5
MAX_RETRY_AFTER_SECONDS = 60.0


def unit_interval() -> float:
    return secrets.randbelow(1_000_000) / 1_000_000


def backoff(attempt: int, jitter: Callable[[], float]) -> float:
    base = BACKOFF_BASE_SECONDS * float(1 << (attempt - 1))
    drawn = min(max(jitter(), 0.0), 1.0)
    return base + (drawn * base)


def retry_after_seconds(
    response: httpx.Response, *, cap: float = MAX_RETRY_AFTER_SECONDS
) -> float | None:
    """Numeric seconds or an HTTP date. A missing or malformed header means use backoff."""
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if when is None:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = max(0.0, (when - datetime.now(UTC)).total_seconds())
    if seconds < 0:
        return None
    return min(seconds, cap)

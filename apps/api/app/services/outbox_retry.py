"""Bounded-jitter backoff for outbox publish retries (J2.1).

Pure function: the RNG and the clock are injectable, so tests fix the seed and do not
depend on real time. The maximum number of attempts lives in `outbox_events.max_attempts`,
not here.
"""

import random
from dataclasses import dataclass, field
from datetime import timedelta

# 2**16 * base already exceeds any reasonable cap; this bounds the exponent
# without changing the policy.
_MAX_EXPONENT = 16


@dataclass(frozen=True)
class RetryPolicy:
    """Wait `min(cap, base * 2**(n-1))` plus uniform jitter of up to `jitter_fraction`.

    The wait never exceeds `cap * (1 + jitter_fraction)`, so the jitter is bounded as well.
    """

    base_seconds: float = 30.0
    cap_seconds: float = 300.0
    jitter_fraction: float = 0.2
    rng: random.Random = field(default_factory=random.Random)

    def delay(self, failed_attempts: int) -> timedelta:
        """Wait after the `failed_attempts`-th failed attempt (counting from 1)."""
        if failed_attempts < 1:
            raise ValueError("failed_attempts must be >= 1")
        exponent = min(failed_attempts - 1, _MAX_EXPONENT)
        backoff = min(self.cap_seconds, self.base_seconds * 2**exponent)
        jitter = self.rng.uniform(0.0, backoff * self.jitter_fraction)
        return timedelta(seconds=backoff + jitter)


DEFAULT_RETRY = RetryPolicy()

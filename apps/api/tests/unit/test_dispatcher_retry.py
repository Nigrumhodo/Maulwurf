"""U-S2-JF-01: bounded-jitter backoff (max. 3 transient failures); non-transient
errors are not retried.

No Redis or DB: the policy receives a seeded RNG and the classifier is a pure function.
"""

import random
from datetime import timedelta

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.services.outbox_retry import RetryPolicy
from app.workers.outbox_errors import ErrorKind, classify_enqueue_error


class _MaxJitter(random.Random):
    """RNG that always returns the upper bound of the jitter."""

    def uniform(self, a: float, b: float) -> float:
        return b


class _NoJitter(random.Random):
    def uniform(self, a: float, b: float) -> float:
        return a


@pytest.mark.parametrize(
    ("failed_attempts", "expected_backoff"),
    [(1, 30.0), (2, 60.0), (3, 120.0), (6, 300.0), (50, 300.0)],
)
def test_backoff_doubles_and_is_capped(failed_attempts: int, expected_backoff: float) -> None:
    policy = RetryPolicy(rng=_NoJitter())

    assert policy.delay(failed_attempts) == timedelta(seconds=expected_backoff)


@pytest.mark.parametrize("failed_attempts", [1, 2, 3, 10])
def test_jitter_stays_within_bounded_fraction(failed_attempts: int) -> None:
    policy = RetryPolicy(rng=_MaxJitter())
    floor = RetryPolicy(rng=_NoJitter()).delay(failed_attempts)

    delay = policy.delay(failed_attempts)

    assert floor <= delay <= floor * (1 + policy.jitter_fraction)
    # The absolute cap includes the jitter: never more than cap * (1 + fraction).
    assert delay <= timedelta(seconds=policy.cap_seconds * (1 + policy.jitter_fraction))


def test_seeded_rng_makes_jitter_reproducible() -> None:
    first = RetryPolicy(rng=random.Random(7)).delay(1)  # noqa: S311 - jitter, not a security value
    second = RetryPolicy(rng=random.Random(7)).delay(1)  # noqa: S311 - jitter, not a security value

    assert first == second


def test_attempt_number_must_be_positive() -> None:
    with pytest.raises(ValueError):
        RetryPolicy(rng=_NoJitter()).delay(0)


@pytest.mark.parametrize(
    "exc",
    [
        RedisConnectionError("refused"),
        RedisTimeoutError("timeout"),
        ConnectionRefusedError(),
        ResponseError("OOM command not allowed when used memory > 'maxmemory'."),
        ResponseError("LOADING Redis is loading the dataset in memory"),
        ResponseError("BUSY Redis is busy running a script"),
    ],
)
def test_infrastructure_errors_do_not_charge_the_event(exc: BaseException) -> None:
    assert classify_enqueue_error(exc) is ErrorKind.INFRA


def test_try_again_is_a_per_event_transient() -> None:
    assert classify_enqueue_error(ResponseError("TRYAGAIN multi-key op")) is ErrorKind.TRANSIENT


@pytest.mark.parametrize(
    "exc",
    [
        TypeError("object is not JSON serializable"),
        ValueError("bad argument"),
        ResponseError("WRONGTYPE Operation against a key holding the wrong kind of value"),
        KeyError("job"),
    ],
)
def test_non_transient_errors_are_never_retried(exc: BaseException) -> None:
    assert classify_enqueue_error(exc) is ErrorKind.PERMANENT

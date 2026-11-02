"""J4.5 pure contract: a sender failure is uncertain unless it proves a non-delivery.

The database settlement is proven by
`tests/integration/test_notification_delivery_unknown.py`. These cases pin the decision that
must not depend on the database or on a provider adapter: only an explicit rejection can
become `failed`; a lost response, a timeout or any unexpected exception may already have
reached Gmail and therefore ends `delivery_unknown`, never a retry.
"""

import pytest

from app.services.notifications import (
    DELIVERY_FAILED,
    DELIVERY_UNKNOWN,
    RESPONSE_LOST,
    NotificationDeliveryAmbiguous,
    NotificationDeliveryRejected,
    delivery_error_outcome,
)


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_code"),
    [
        (NotificationDeliveryRejected(), DELIVERY_FAILED, "provider_rejected"),
        (
            NotificationDeliveryRejected("invalid_grant"),
            DELIVERY_FAILED,
            "invalid_grant",
        ),
        (
            NotificationDeliveryRejected("  INVALID_GRANT  "),
            DELIVERY_FAILED,
            "invalid_grant",
        ),
        (
            NotificationDeliveryRejected("Bob <bob@example.test>"),
            DELIVERY_FAILED,
            "unknown",
        ),
        (
            NotificationDeliveryAmbiguous(),
            DELIVERY_UNKNOWN,
            RESPONSE_LOST,
        ),
        (
            NotificationDeliveryAmbiguous("provider_unavailable"),
            DELIVERY_UNKNOWN,
            "provider_unavailable",
        ),
        (
            NotificationDeliveryAmbiguous("secret transcript text"),
            DELIVERY_UNKNOWN,
            "unknown",
        ),
        (TimeoutError("the read timed out"), DELIVERY_UNKNOWN, RESPONSE_LOST),
        (ConnectionResetError("connection reset by peer"), DELIVERY_UNKNOWN, RESPONSE_LOST),
        (RuntimeError("unexpected adapter bug"), DELIVERY_UNKNOWN, RESPONSE_LOST),
        (ValueError("malformed response"), DELIVERY_UNKNOWN, RESPONSE_LOST),
    ],
)
def test_only_a_proven_rejection_is_a_known_non_delivery(
    error: Exception, expected_status: str, expected_code: str
) -> None:
    assert delivery_error_outcome(error) == (expected_status, expected_code)


def test_the_uncertain_outcome_is_terminal_and_never_the_retryable_pending_state() -> None:
    status, _code = delivery_error_outcome(RuntimeError("anything"))

    assert status == DELIVERY_UNKNOWN
    assert status not in ("pending", "sending")

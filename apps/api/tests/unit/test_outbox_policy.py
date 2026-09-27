"""U-S1-JF-04 (J1.5): el dispatcher publica index/analyze solo con cleanup verificado."""
import pytest

from app.services.outbox import Outcome, decide


@pytest.mark.parametrize("event_type", ["index_requested", "analyze_requested"])
def test_gated_event_publishes_only_with_verified_cleanup(event_type: str) -> None:
    decision = decide(event_type, enabled=True, blocked_reason=None, cleanup_status="verified")

    assert decision.publish
    assert decision.job == {"index_requested": "index", "analyze_requested": "analyze"}[
        event_type
    ]


@pytest.mark.parametrize("cleanup_status", ["pending", "failed", None])
@pytest.mark.parametrize("event_type", ["index_requested", "analyze_requested"])
def test_gated_event_blocked_without_verified_cleanup(
    event_type: str, cleanup_status: str | None
) -> None:
    # `failed` y `None` (sin intento conocido) cuentan igual que pendiente: fail closed.
    decision = decide(
        event_type, enabled=True, blocked_reason=None, cleanup_status=cleanup_status
    )

    assert not decision.publish
    assert decision.outcome is Outcome.CLEANUP_PENDING


@pytest.mark.parametrize(
    ("enabled", "blocked_reason"), [(False, "cleanup_pending"), (True, "cleanup_pending")]
)
def test_gated_event_blocked_while_flagged_even_if_cleanup_verified(
    enabled: bool, blocked_reason: str
) -> None:
    decision = decide(
        "index_requested", enabled=enabled, blocked_reason=blocked_reason,
        cleanup_status="verified",
    )

    assert decision.outcome is Outcome.CLEANUP_PENDING


@pytest.mark.parametrize(
    "event_type",
    ["sync_task_event", "task_cancelled", "notify", "send_digest", "delete_event",
     "account_deleted"],
)
@pytest.mark.parametrize("cleanup_status", ["pending", "failed", None])
def test_non_ingest_events_never_wait_for_audio_cleanup(
    event_type: str, cleanup_status: str | None
) -> None:
    # Calendar, notificaciones y borrados no dependen del cleanup; en S1 no hay consumidor.
    decision = decide(
        event_type, enabled=True, blocked_reason=None, cleanup_status=cleanup_status
    )

    assert decision.outcome is Outcome.NO_CONSUMER


def test_unknown_type_fails_closed() -> None:
    decision = decide(
        "index.requested", enabled=True, blocked_reason=None, cleanup_status="verified"
    )

    assert not decision.publish
    assert decision.outcome is Outcome.UNKNOWN_TYPE

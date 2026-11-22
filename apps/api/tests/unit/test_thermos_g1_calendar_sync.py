"""Thermos G1 regressions for the idempotent Calendar sync engine (J3.4/J3.6).

A transient exhaustion must leave the task `pending` (the job owns the `failed` write once
its retry budget is spent), and an event the user deleted in Google (`status: cancelled`)
must never be adopted as synced.
"""

from app.services.task_event_sync import (
    SyncOutcome,
    build_event_body,
    deterministic_event_id,
    event_content_matches,
)
from tests.unit.test_task_event_sync import Gateway, Store, _engine, _key, _task


def test_a_cancelled_event_never_matches_the_desired_body() -> None:
    task = _task()
    body = build_event_body(task)

    assert event_content_matches(dict(body), body) is True
    assert event_content_matches({**body, "status": "confirmed"}, body) is True
    assert event_content_matches({**body, "status": "cancelled"}, body) is False


async def test_a_deleted_event_with_our_identity_is_not_adopted() -> None:
    task = _task()
    store = Store(task)
    gateway = Gateway()
    # Google keeps a deleted event's ID: the insert collides and the read shows it cancelled.
    gateway.events[_key(task)] = {
        "id": deterministic_event_id(task.task_id),
        **build_event_body(task),
        "status": "cancelled",
        "etag": '"deleted"',
    }

    outcome = await _engine(store, gateway).run(task.task_id, 1)

    assert outcome is SyncOutcome.EXTERNAL_CONFLICT
    assert store.synced == []
    assert store.failed == [(task.task_id, 1)]
    assert gateway.update_calls == []

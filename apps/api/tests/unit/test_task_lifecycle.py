"""J3.6: completing/dismissing cancels future reminders but keeps the Calendar event.

The transition table is a pure decision core. The service only maps the store outcome to
the API errors and to the "event kept" notice; the PostgreSQL adapter (which locks the task
and cancels its pending notifications in the same transaction) is covered by I-S3-JF-04 in
`tests/integration/test_calendar_revision_patch.py`.
"""

import uuid
from datetime import UTC, datetime

import pytest

from app.core.errors import ApiError
from app.services.task_lifecycle import (
    CALENDAR_EVENT_KEPT_NOTICE,
    TASK_STATUSES,
    TASK_TRANSITIONS,
    LifecycleOutcome,
    LifecycleTransition,
    TaskLifecycle,
    TaskLifecycleStore,
    ensure_task_transition,
)

NOW = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)


class Store:
    def __init__(
        self,
        *,
        status: str = "confirmed",
        version: int = 1,
        calendar_event_id: str | None = "maulwurf-event",
        outcome: LifecycleOutcome | None = None,
    ) -> None:
        self.task_id = uuid.uuid4()
        self.user_id = uuid.uuid4()
        self.status = status
        self.version = version
        self.calendar_event_id = calendar_event_id
        self.outcome = outcome
        self.calls: list[tuple[uuid.UUID, uuid.UUID, int, str, datetime]] = []

    async def transition(
        self,
        user_id: uuid.UUID,
        task_id: uuid.UUID,
        *,
        expected_version: int,
        target: str,
        now: datetime,
    ) -> LifecycleTransition:
        self.calls.append((user_id, task_id, expected_version, target, now))
        if self.outcome is not None:
            return LifecycleTransition(
                outcome=self.outcome,
                status=self.status,
                current_version=self.version,
                calendar_event_id=self.calendar_event_id,
            )
        if self.version != expected_version:
            return LifecycleTransition(
                outcome=LifecycleOutcome.VERSION_CONFLICT,
                status=self.status,
                current_version=self.version,
                calendar_event_id=self.calendar_event_id,
            )
        if self.status == target:
            return LifecycleTransition(
                outcome=LifecycleOutcome.ALREADY,
                status=self.status,
                current_version=self.version,
                calendar_event_id=self.calendar_event_id,
                cancelled_reminders=1,
            )
        ensure_task_transition(self.status, target)
        self.status = target
        return LifecycleTransition(
            outcome=LifecycleOutcome.UPDATED,
            status=target,
            current_version=self.version,
            calendar_event_id=self.calendar_event_id,
            cancelled_reminders=3,
        )


def _service(
    store: Store, *, now: datetime = NOW
) -> TaskLifecycle:
    return TaskLifecycle(store, clock=lambda: now)


def test_every_task_status_has_a_transition_row() -> None:
    assert set(TASK_TRANSITIONS) == set(TASK_STATUSES)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("confirmed", "completed"),
        ("confirmed", "dismissed"),
        ("pending", "dismissed"),
        ("needs_review", "dismissed"),
        ("completed", "completed"),
        ("dismissed", "dismissed"),
    ],
)
def test_allowed_commands(current: str, target: str) -> None:
    assert ensure_task_transition(current, target) == target


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("pending", "completed"),
        ("needs_review", "completed"),
        ("dismissed", "completed"),
        ("completed", "dismissed"),
        ("unknown", "completed"),
    ],
)
def test_forbidden_commands_are_a_409(current: str, target: str) -> None:
    with pytest.raises(ApiError) as failure:
        ensure_task_transition(current, target)

    assert failure.value.status_code == 409
    assert failure.value.code == "invalid_transition"
    assert failure.value.details["current_status"] == current


async def test_completing_a_confirmed_task_cancels_reminders_and_keeps_the_event() -> None:
    store = Store(calendar_event_id="maulwurf-event")

    result = await _service(store).complete(store.user_id, store.task_id, expected_version=1)

    assert result.status == "completed"
    assert result.cancelled_reminders == 3
    assert result.calendar_event_kept is True
    assert result.notice == CALENDAR_EVENT_KEPT_NOTICE
    assert store.calls == [(store.user_id, store.task_id, 1, "completed", NOW)]


async def test_dismissing_a_pending_task_without_an_event_has_no_notice() -> None:
    store = Store(status="needs_review", calendar_event_id=None)

    result = await _service(store).dismiss(store.user_id, store.task_id, expected_version=1)

    assert result.status == "dismissed"
    assert result.calendar_event_kept is False
    assert result.notice is None


async def test_repeating_complete_is_idempotent_and_still_cancels_leftovers() -> None:
    store = Store(status="completed")

    result = await _service(store).complete(store.user_id, store.task_id, expected_version=1)

    assert result.status == "completed"
    assert result.cancelled_reminders == 1
    assert result.calendar_event_kept is True


async def test_a_stale_version_raises_version_conflict() -> None:
    store = Store(version=4)

    with pytest.raises(ApiError) as failure:
        await _service(store).complete(store.user_id, store.task_id, expected_version=3)

    assert failure.value.status_code == 409
    assert failure.value.code == "version_conflict"
    assert failure.value.details["current_version"] == 4


async def test_a_missing_task_is_a_404() -> None:
    store = Store(outcome=LifecycleOutcome.NOT_FOUND)

    with pytest.raises(ApiError) as failure:
        await _service(store).dismiss(store.user_id, store.task_id, expected_version=1)

    assert failure.value.status_code == 404


def test_protocol_is_structural() -> None:
    """The adapter only has to implement `transition`; no inheritance required."""
    candidate: TaskLifecycleStore = Store()
    assert callable(candidate.transition)

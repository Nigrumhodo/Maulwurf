"""J3.4: deterministic, timeout-safe and conflict-safe task → Calendar event sync.

These cases pin the idempotency protocol of `TaskEventSync` with in-memory doubles. The
ARQ entry point, the PostgreSQL adapter and the outbox wiring are covered by I-S3-JF-03 in
`tests/integration/test_calendar_sync_job.py`.
"""

import re
import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from app.services.google_calendar import (
    GoogleCalendarConflict,
    GoogleCalendarForbidden,
    GoogleCalendarInvalidGrant,
    GoogleCalendarTransientError,
)
from app.services.task_event_sync import (
    EVENT_ID_PREFIX,
    TASK_ID_PROPERTY,
    USER_ID_PROPERTY,
    CalendarGateway,
    SyncOutcome,
    TaskEventBlocked,
    TaskEventSync,
    TaskEventTransientError,
    TaskSnapshot,
    build_event_body,
    deterministic_event_id,
    event_identity_matches,
)

GOOGLE_ID = re.compile(r"^[a-v0-9]{5,1024}$")


def _task(**overrides: Any) -> TaskSnapshot:
    start = datetime(2026, 11, 2, 14, 0, tzinfo=UTC)
    values: dict[str, Any] = {
        "task_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "version": 1,
        "calendar_sync_status": "pending",
        "calendar_id": "maulwurf@group.calendar.google.com",
        "calendar_event_id": None,
        "title": "Entregar informe",
        "detail": "Informe de laboratorio",
        "timezone": "America/Bogota",
        "all_day": False,
        "event_start_at": start,
        "event_end_at": start + timedelta(hours=1),
        "event_start_date": None,
        "event_end_date_exclusive": None,
    }
    values.update(overrides)
    return TaskSnapshot(**values)


class Store:
    def __init__(self, task: TaskSnapshot | None) -> None:
        self.task = task
        self.synced: list[tuple[uuid.UUID, int, str]] = []
        self.failed: list[tuple[uuid.UUID, int]] = []
        self.blocked: list[tuple[uuid.UUID, int]] = []
        self.accept_updates = True

    async def load(self, task_id: uuid.UUID) -> TaskSnapshot | None:
        if self.task is None or self.task.task_id != task_id:
            return None
        return self.task

    async def mark_synced(
        self, task_id: uuid.UUID, version: int, event_id: str
    ) -> bool:
        self.synced.append((task_id, version, event_id))
        return self.accept_updates

    async def mark_failed(self, task_id: uuid.UUID, version: int) -> bool:
        self.failed.append((task_id, version))
        return True

    async def mark_blocked(self, task_id: uuid.UUID, version: int) -> bool:
        self.blocked.append((task_id, version))
        return True


class Gateway:
    def __init__(self) -> None:
        self.events: dict[tuple[str, str], dict[str, Any]] = {}
        self.insert_calls: list[tuple[str, str, dict[str, Any]]] = []
        self.get_calls: list[tuple[str, str]] = []
        self.errors: list[Exception] = []
        self.lose_next_response = False

    async def insert_event(
        self, calendar_id: str, event_id: str, body: Mapping[str, Any]
    ) -> dict[str, Any]:
        self.insert_calls.append((calendar_id, event_id, dict(body)))
        if self.errors:
            raise self.errors.pop(0)
        key = (calendar_id, event_id)
        if self.lose_next_response:
            self.lose_next_response = False
            self.events[key] = dict(body)
            raise GoogleCalendarTransientError("lost response")
        if key in self.events:
            raise GoogleCalendarConflict("duplicate")
        self.events[key] = dict(body)
        return self.events[key]

    async def get_event(
        self, calendar_id: str, event_id: str
    ) -> dict[str, Any] | None:
        self.get_calls.append((calendar_id, event_id))
        return self.events.get((calendar_id, event_id))


class Sleeper:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def _factory(gateway: Gateway) -> Callable[[TaskSnapshot], Awaitable[CalendarGateway]]:
    async def factory(_snapshot: TaskSnapshot) -> CalendarGateway:
        return gateway

    return factory


def _engine(store: Store, gateway: Gateway, **overrides: Any) -> TaskEventSync:
    return TaskEventSync(
        store, _factory(gateway), sleeper=Sleeper(), **overrides
    )


def _existing(task: TaskSnapshot, *, task_id: uuid.UUID | None = None) -> dict[str, Any]:
    return {
        "id": deterministic_event_id(task.task_id),
        "summary": "previous body",
        "extendedProperties": {
            "private": {
                TASK_ID_PROPERTY: str(task_id or task.task_id),
                USER_ID_PROPERTY: str(task.user_id),
            }
        },
    }


def _key(task: TaskSnapshot) -> tuple[str, str]:
    assert task.calendar_id is not None
    return (task.calendar_id, deterministic_event_id(task.task_id))


def test_event_id_is_stable_base32hex_and_task_specific() -> None:
    task_id = uuid.UUID("8f14e45f-ceea-4678-9c2b-1f0b6f8f0001")

    first = deterministic_event_id(task_id)
    second = deterministic_event_id(uuid.UUID(str(task_id)))

    assert first == second
    assert first.startswith(EVENT_ID_PREFIX)
    assert GOOGLE_ID.match(first)
    assert first != deterministic_event_id(uuid.uuid4())


def test_identity_requires_the_maulwurf_task_property() -> None:
    task = _task()

    assert event_identity_matches(_existing(task), task.task_id)
    assert not event_identity_matches(
        _existing(task, task_id=uuid.uuid4()), task.task_id
    )
    assert not event_identity_matches(
        {"id": deterministic_event_id(task.task_id)}, task.task_id
    )
    assert event_identity_matches(
        {"extendedProperties": {"private": {TASK_ID_PROPERTY: str(task.task_id)}}},
        task.task_id,
    )


def test_body_carries_identity_and_the_timed_block() -> None:
    task = _task()

    body = build_event_body(task)

    assert body["summary"] == "Entregar informe"
    assert body["description"] == "Informe de laboratorio"
    assert body["extendedProperties"]["private"] == {
        TASK_ID_PROPERTY: str(task.task_id),
        USER_ID_PROPERTY: str(task.user_id),
    }
    assert body["start"] == {"dateTime": "2026-11-02T14:00:00Z", "timeZone": "America/Bogota"}
    assert body["end"] == {"dateTime": "2026-11-02T15:00:00Z", "timeZone": "America/Bogota"}


def test_body_uses_exclusive_dates_for_an_all_day_block() -> None:
    task = _task(
        all_day=True,
        event_start_at=None,
        event_end_at=None,
        event_start_date=date(2026, 11, 2),
        event_end_date_exclusive=date(2026, 11, 3),
    )

    body = build_event_body(task)

    assert body["start"] == {"date": "2026-11-02"}
    assert body["end"] == {"date": "2026-11-03"}


async def test_stale_version_is_discarded_without_calling_google() -> None:
    task = _task(version=2)
    store = Store(task)
    gateway = Gateway()

    outcome = await _engine(store, gateway).run(task.task_id, 1)

    assert outcome is SyncOutcome.STALE_VERSION
    assert gateway.insert_calls == [] and gateway.get_calls == []
    assert store.synced == []
    assert store.failed == []
    assert store.blocked == []


async def test_missing_task_is_discarded() -> None:
    store = Store(None)
    gateway = Gateway()

    outcome = await _engine(store, gateway).run(uuid.uuid4(), 1)

    assert outcome is SyncOutcome.TASK_NOT_FOUND
    assert gateway.insert_calls == []


async def test_double_delivery_creates_one_event_and_reconciles() -> None:
    task = _task()
    store = Store(task)
    gateway = Gateway()
    engine = _engine(store, gateway)

    first = await engine.run(task.task_id, 1)
    second = await engine.run(task.task_id, 1)

    assert (first, second) == (SyncOutcome.SYNCED, SyncOutcome.RECONCILED)
    assert len(gateway.events) == 1
    assert len(gateway.insert_calls) == 2
    assert gateway.get_calls == [_key(task)]
    assert store.synced == [
        (task.task_id, 1, deterministic_event_id(task.task_id)),
        (task.task_id, 1, deterministic_event_id(task.task_id)),
    ]


async def test_lost_response_queries_the_deterministic_id_before_retrying() -> None:
    task = _task()
    store = Store(task)
    gateway = Gateway()
    gateway.lose_next_response = True

    outcome = await _engine(store, gateway).run(task.task_id, 1)

    assert outcome is SyncOutcome.RECONCILED
    assert len(gateway.insert_calls) == 1
    assert gateway.get_calls == [_key(task)]
    assert len(gateway.events) == 1


async def test_timeout_without_an_event_retries_the_same_event_id() -> None:
    task = _task()
    store = Store(task)
    gateway = Gateway()
    gateway.errors = [GoogleCalendarTransientError("timeout")]
    engine = _engine(store, gateway)

    outcome = await engine.run(task.task_id, 1)

    assert outcome is SyncOutcome.SYNCED
    assert len(gateway.insert_calls) == 2
    event_ids = {call[1] for call in gateway.insert_calls}
    assert event_ids == {deterministic_event_id(task.task_id)}
    assert len(gateway.get_calls) == 1
    assert len(gateway.events) == 1


async def test_conflict_with_matching_identity_reconciles_without_overwriting() -> None:
    task = _task()
    store = Store(task)
    gateway = Gateway()
    original = _existing(task)
    gateway.events[_key(task)] = original

    outcome = await _engine(store, gateway).run(task.task_id, 1)

    assert outcome is SyncOutcome.RECONCILED
    stored = gateway.events[_key(task)]
    assert stored == original
    assert store.synced == [(task.task_id, 1, deterministic_event_id(task.task_id))]


async def test_conflict_with_foreign_identity_is_not_adopted_or_overwritten() -> None:
    task = _task()
    store = Store(task)
    gateway = Gateway()
    foreign = _existing(task, task_id=uuid.uuid4())
    gateway.events[_key(task)] = foreign

    outcome = await _engine(store, gateway).run(task.task_id, 1)

    assert outcome is SyncOutcome.FAILED
    assert gateway.events[_key(task)] == foreign
    assert store.synced == []
    assert store.failed == [(task.task_id, 1)]


async def test_conflict_without_a_readable_event_fails() -> None:
    task = _task()
    store = Store(task)
    gateway = Gateway()

    async def conflict_then_missing(
        calendar_id: str, event_id: str, body: Mapping[str, Any]
    ) -> dict[str, Any]:
        gateway.insert_calls.append((calendar_id, event_id, dict(body)))
        raise GoogleCalendarConflict("duplicate")

    gateway.insert_event = conflict_then_missing  # type: ignore[method-assign]

    outcome = await _engine(store, gateway).run(task.task_id, 1)

    assert outcome is SyncOutcome.FAILED
    assert store.failed == [(task.task_id, 1)]


async def test_stale_after_insert_does_not_claim_the_version() -> None:
    task = _task()
    store = Store(task)
    store.accept_updates = False

    outcome = await _engine(store, Gateway()).run(task.task_id, 1)

    assert outcome is SyncOutcome.STALE_VERSION
    assert store.synced == [(task.task_id, 1, deterministic_event_id(task.task_id))]


async def test_missing_calendar_blocks_without_calling_google() -> None:
    task = _task(calendar_id=None)
    store = Store(task)
    gateway = Gateway()

    outcome = await _engine(store, gateway).run(task.task_id, 1)

    assert outcome is SyncOutcome.BLOCKED
    assert gateway.insert_calls == [] and gateway.get_calls == []
    assert store.blocked == [(task.task_id, 1)]


async def test_missing_event_block_blocks_without_calling_google() -> None:
    task = _task(event_start_at=None, event_end_at=None)
    store = Store(task)
    gateway = Gateway()

    outcome = await _engine(store, gateway).run(task.task_id, 1)

    assert outcome is SyncOutcome.BLOCKED
    assert gateway.insert_calls == []
    assert store.blocked == [(task.task_id, 1)]


async def test_a_disconnected_integration_blocks_the_task() -> None:
    task = _task()
    store = Store(task)

    async def unavailable(_snapshot: TaskSnapshot) -> CalendarGateway:
        raise TaskEventBlocked("google_not_connected")

    engine = TaskEventSync(store, unavailable, sleeper=Sleeper())

    assert await engine.run(task.task_id, 1) is SyncOutcome.BLOCKED
    assert store.blocked == [(task.task_id, 1)]


async def test_transient_exhaustion_marks_failed_and_raises_for_arq() -> None:
    task = _task()
    store = Store(task)
    gateway = Gateway()
    gateway.errors = [
        GoogleCalendarTransientError("timeout"),
        GoogleCalendarTransientError("timeout"),
    ]
    sleeper = Sleeper()
    engine = TaskEventSync(store, _factory(gateway), sleeper=sleeper)

    with pytest.raises(TaskEventTransientError):
        await engine.run(task.task_id, 1)

    assert len(gateway.insert_calls) == 2
    assert len(gateway.get_calls) == 2
    assert sleeper.delays == [0.25]
    assert store.failed == [(task.task_id, 1)]
    assert store.synced == []


async def test_terminal_google_error_marks_failed_without_retrying() -> None:
    task = _task()
    store = Store(task)
    gateway = Gateway()
    gateway.errors = [GoogleCalendarForbidden("forbidden")]

    outcome = await _engine(store, gateway).run(task.task_id, 1)

    assert outcome is SyncOutcome.FAILED
    assert store.failed == [(task.task_id, 1)]
    assert len(gateway.insert_calls) == 1


async def test_invalid_grant_marks_the_task_blocked() -> None:
    task = _task()
    store = Store(task)
    gateway = Gateway()
    gateway.errors = [GoogleCalendarInvalidGrant("revoked")]

    outcome = await _engine(store, gateway).run(task.task_id, 1)

    assert outcome is SyncOutcome.BLOCKED
    assert store.blocked == [(task.task_id, 1)]
    assert store.failed == []

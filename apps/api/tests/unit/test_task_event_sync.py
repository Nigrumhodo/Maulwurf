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
    GoogleCalendarPreconditionFailed,
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
    event_content_matches,
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


class SequencedStore(Store):
    """Store that answers the next loads from a script (to reproduce interleavings)."""

    def __init__(self, task: TaskSnapshot | None, sequence: list[TaskSnapshot]) -> None:
        super().__init__(task)
        self.sequence = list(sequence)

    async def load(self, task_id: uuid.UUID) -> TaskSnapshot | None:
        if self.sequence:
            return self.sequence.pop(0)
        return await super().load(task_id)


class Gateway:
    def __init__(self) -> None:
        self.events: dict[tuple[str, str], dict[str, Any]] = {}
        self.insert_calls: list[tuple[str, str, dict[str, Any]]] = []
        self.get_calls: list[tuple[str, str]] = []
        self.update_calls: list[tuple[str, str, dict[str, Any], str | None]] = []
        self.errors: list[Exception] = []
        self.lose_next_response = False
        # J3.6: Google rejects a stale `If-Match` and the edit that won is visible afterwards.
        self._etag_sequence = 0
        self.reject_update_at: int | None = None
        self.on_reject_body: dict[str, Any] | None = None

    def _next_etag(self) -> str:
        self._etag_sequence += 1
        return f'"etag-{self._etag_sequence}"'

    def _store(self, key: tuple[str, str], body: Mapping[str, Any]) -> dict[str, Any]:
        stored = dict(body)
        stored["etag"] = self._next_etag()
        self.events[key] = stored
        return dict(stored)

    def apply_external_edit(
        self, key: tuple[str, str], changes: Mapping[str, Any]
    ) -> None:
        stored = dict(self.events[key])
        stored.update(changes)
        stored["etag"] = self._next_etag()
        self.events[key] = stored

    async def insert_event(
        self, calendar_id: str, event_id: str, body: Mapping[str, Any]
    ) -> dict[str, Any]:
        self.insert_calls.append((calendar_id, event_id, dict(body)))
        if self.errors:
            raise self.errors.pop(0)
        key = (calendar_id, event_id)
        if self.lose_next_response:
            self.lose_next_response = False
            self._store(key, {**body, "id": event_id})
            raise GoogleCalendarTransientError("lost response")
        if key in self.events:
            raise GoogleCalendarConflict("duplicate")
        return self._store(key, {**body, "id": event_id})

    async def get_event(
        self, calendar_id: str, event_id: str
    ) -> dict[str, Any] | None:
        self.get_calls.append((calendar_id, event_id))
        event = self.events.get((calendar_id, event_id))
        return dict(event) if event is not None else None

    async def update_event(
        self,
        calendar_id: str,
        event_id: str,
        body: Mapping[str, Any],
        *,
        if_match: str | None = None,
    ) -> dict[str, Any]:
        self.update_calls.append((calendar_id, event_id, dict(body), if_match))
        if self.errors:
            raise self.errors.pop(0)
        key = (calendar_id, event_id)
        if self.reject_update_at == len(self.update_calls):
            self.reject_update_at = None
            if self.on_reject_body is not None:
                self.apply_external_edit(key, self.on_reject_body)
                self.on_reject_body = None
            raise GoogleCalendarPreconditionFailed("stale etag")
        return self._store(key, {**body, "id": event_id})



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


async def test_conflict_with_matching_identity_and_body_does_not_rewrite() -> None:
    task = _task()
    store = Store(task)
    gateway = Gateway()
    original = {"id": deterministic_event_id(task.task_id), **build_event_body(task)}
    gateway.events[_key(task)] = original

    outcome = await _engine(store, gateway).run(task.task_id, 1)

    assert outcome is SyncOutcome.RECONCILED
    assert gateway.events[_key(task)] == original
    assert gateway.update_calls == []
    assert store.synced == [(task.task_id, 1, deterministic_event_id(task.task_id))]


async def test_a_newer_version_repairs_the_body_left_by_the_older_one() -> None:
    """Regression: v1 inserts, v2 wins the CAS, v2's delivery must leave v2's body."""
    first = _task(version=1, title="Entregar informe")
    store = Store(first)
    store.accept_updates = False  # v2 already won the version CAS while v1 inserted
    gateway = Gateway()

    stale = await _engine(store, gateway).run(first.task_id, 1)
    assert stale is SyncOutcome.STALE_VERSION
    assert gateway.events[_key(first)]["summary"] == "Entregar informe"
    assert gateway.update_calls == []

    start = datetime(2026, 11, 2, 16, 0, tzinfo=UTC)
    second = _task(
        task_id=first.task_id,
        version=2,
        title="Entregar informe corregido",
        detail="Informe revisado y firmado",
        event_start_at=start,
        event_end_at=start + timedelta(hours=2),
    )
    store.task = second
    store.accept_updates = True

    repaired = await _engine(store, gateway).run(second.task_id, 2)

    assert repaired is SyncOutcome.RECONCILED
    stored = gateway.events[_key(second)]
    assert stored["summary"] == "Entregar informe corregido"
    assert stored["description"] == "Informe revisado y firmado"
    assert stored["start"] == {
        "dateTime": "2026-11-02T16:00:00Z",
        "timeZone": "America/Bogota",
    }
    assert stored["end"] == {
        "dateTime": "2026-11-02T18:00:00Z",
        "timeZone": "America/Bogota",
    }
    assert len(gateway.update_calls) == 1
    assert store.synced[-1] == (
        second.task_id,
        2,
        deterministic_event_id(second.task_id),
    )


async def test_a_stale_delivery_does_not_overwrite_an_event_settled_by_a_newer_version() -> None:
    """Regression: v2 already synced; v1's 409 must not PATCH its old body over v2."""
    first = _task(version=1, title="Entregar informe")
    start = datetime(2026, 11, 2, 16, 0, tzinfo=UTC)
    second = _task(
        task_id=first.task_id,
        version=2,
        title="Entregar informe corregido",
        detail="Informe revisado y firmado",
        event_start_at=start,
        event_end_at=start + timedelta(hours=2),
    )
    # The row already holds v2 (its delivery won) while this job runs for v1.
    store = Store(second)
    gateway = Gateway()
    gateway.events[_key(second)] = {
        "id": deterministic_event_id(second.task_id),
        **build_event_body(second),
    }

    outcome = await _engine(store, gateway).sync(first, expected_version=1)

    assert outcome is SyncOutcome.STALE_VERSION
    assert gateway.update_calls == []
    assert gateway.events[_key(second)]["summary"] == "Entregar informe corregido"
    assert store.synced == []


async def test_a_version_bump_during_the_patch_repairs_the_event_to_the_newest_body() -> None:
    """The edit can land between the pre-PATCH reload and the write; heal the event."""
    first = _task(version=1, title="Entregar informe")
    start = datetime(2026, 11, 2, 16, 0, tzinfo=UTC)
    second = _task(
        task_id=first.task_id,
        version=2,
        title="Entregar informe corregido",
        detail="Informe revisado y firmado",
        event_start_at=start,
        event_end_at=start + timedelta(hours=2),
    )
    # Pre-PATCH reload still sees v1; the post-PATCH reload sees the winning v2.
    store = SequencedStore(second, [first, second])
    gateway = Gateway()
    gateway.events[_key(first)] = _existing(first)

    outcome = await _engine(store, gateway).sync(first, expected_version=1)

    assert outcome is SyncOutcome.STALE_VERSION
    assert [call[2]["summary"] for call in gateway.update_calls] == [
        "Entregar informe",
        "Entregar informe corregido",
    ]
    stored = gateway.events[_key(first)]
    assert stored["summary"] == "Entregar informe corregido"
    assert stored["description"] == "Informe revisado y firmado"
    assert store.synced == []


async def test_a_changed_detail_is_patched_into_the_event() -> None:
    task = _task(detail="detalle nuevo")
    store = Store(task)
    gateway = Gateway()
    gateway.events[_key(task)] = {
        "id": deterministic_event_id(task.task_id),
        **build_event_body(_task(task_id=task.task_id, detail="detalle anterior")),
    }

    outcome = await _engine(store, gateway).run(task.task_id, 1)

    assert outcome is SyncOutcome.RECONCILED
    assert gateway.update_calls[0][2]["description"] == "detalle nuevo"
    assert gateway.events[_key(task)]["description"] == "detalle nuevo"


def test_content_comparison_normalizes_google_timestamps() -> None:
    task = _task()
    body = build_event_body(task)

    assert event_content_matches(body, body)
    assert event_content_matches(
        {
            "summary": body["summary"],
            "description": body["description"],
            # Google answers with the event's offset and milliseconds for the same instant.
            "start": {"dateTime": "2026-11-02T09:00:00.000-05:00", "timeZone": "America/Bogota"},
            "end": {"dateTime": "2026-11-02T10:00:00.000-05:00", "timeZone": "America/Bogota"},
        },
        body,
    )
    shifted = build_event_body(
        _task(
            event_start_at=datetime(2026, 11, 2, 15, 0, tzinfo=UTC),
            event_end_at=datetime(2026, 11, 2, 16, 0, tzinfo=UTC),
        )
    )
    assert not event_content_matches(
        {
            "summary": body["summary"],
            "description": body["description"],
            "start": {"dateTime": "2026-11-02T09:00:00-05:00", "timeZone": "America/Bogota"},
            "end": {"dateTime": "2026-11-02T10:00:00-05:00", "timeZone": "America/Bogota"},
        },
        # A one-hour shift must be detected even though both sides are valid RFC 3339.
        shifted,
    )


def test_all_day_comparison_uses_the_exclusive_date() -> None:
    task = _task(
        all_day=True,
        event_start_at=None,
        event_end_at=None,
        event_start_date=date(2026, 11, 2),
        event_end_date_exclusive=date(2026, 11, 3),
    )
    body = build_event_body(task)

    assert event_content_matches(
        {
            "summary": body["summary"],
            "description": body["description"],
            "start": {"date": "2026-11-02"},
            "end": {"date": "2026-11-03"},
        },
        body,
    )
    assert not event_content_matches(
        {
            "summary": body["summary"],
            "description": body["description"],
            "start": {"date": "2026-11-02"},
            "end": {"date": "2026-11-02"},
        },
        body,
    )


async def test_conflict_with_foreign_identity_is_not_adopted_or_overwritten() -> None:
    task = _task()
    store = Store(task)
    gateway = Gateway()
    foreign = _existing(task, task_id=uuid.uuid4())
    gateway.events[_key(task)] = foreign

    outcome = await _engine(store, gateway).run(task.task_id, 1)

    assert outcome is SyncOutcome.FAILED
    assert gateway.events[_key(task)] == foreign
    assert gateway.update_calls == []
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


def _revision(first: TaskSnapshot) -> TaskSnapshot:
    start = datetime(2026, 11, 2, 16, 0, tzinfo=UTC)
    return _task(
        task_id=first.task_id,
        version=first.version + 1,
        title="Entregar informe corregido",
        detail="Informe revisado y firmado",
        event_start_at=start,
        event_end_at=start + timedelta(hours=2),
    )


async def test_confirming_a_revision_patches_with_the_events_etag() -> None:
    """J3.6: the confirmed revision updates the linked event under `If-Match`."""
    first = _task()
    revision = _revision(first)
    store = Store(revision)
    gateway = Gateway()
    key = _key(first)
    gateway.events[key] = {**_existing(first), "etag": '"etag-v1"'}

    outcome = await _engine(store, gateway).sync(revision, expected_version=2)

    assert outcome is SyncOutcome.RECONCILED
    assert gateway.update_calls[0][3] == '"etag-v1"'
    assert gateway.events[key]["summary"] == "Entregar informe corregido"
    assert store.synced == [(revision.task_id, 2, deterministic_event_id(first.task_id))]


async def test_an_external_edit_is_not_overwritten_by_the_revision_patch() -> None:
    """J3.6: a `412` means someone edited the event; the task is flagged, never merged."""
    first = _task()
    revision = _revision(first)
    store = Store(revision)
    gateway = Gateway()
    key = _key(first)
    gateway.events[key] = {**_existing(first), "etag": '"etag-v1"'}
    gateway.reject_update_at = 1
    gateway.on_reject_body = {"summary": "Editado directamente en Google"}

    outcome = await _engine(store, gateway).sync(revision, expected_version=2)

    assert outcome is SyncOutcome.EXTERNAL_CONFLICT
    assert store.failed == [(revision.task_id, 2)]
    assert store.synced == []
    assert len(gateway.update_calls) == 1
    assert gateway.events[key]["summary"] == "Editado directamente en Google"


async def test_a_precondition_failure_with_matching_content_is_reconciled() -> None:
    """The same body was written by another delivery: adopting it overwrites nothing."""
    first = _task()
    revision = _revision(first)
    store = Store(revision)
    gateway = Gateway()
    key = _key(first)
    gateway.events[key] = {**_existing(first), "etag": '"etag-v1"'}
    gateway.reject_update_at = 1
    gateway.on_reject_body = build_event_body(revision)

    outcome = await _engine(store, gateway).sync(revision, expected_version=2)

    assert outcome is SyncOutcome.RECONCILED
    assert store.failed == []
    assert store.synced == [(revision.task_id, 2, deterministic_event_id(first.task_id))]
    assert len(gateway.update_calls) == 1


async def test_the_repair_patch_is_conditional_on_the_fresh_etag() -> None:
    """The post-PATCH repair re-reads the event instead of writing an unconditional body."""
    first = _task(version=1, title="Entregar informe")
    second = _revision(first)
    store = SequencedStore(second, [first, second])
    gateway = Gateway()
    key = _key(first)
    gateway.events[key] = _existing(first)

    outcome = await _engine(store, gateway).sync(first, expected_version=1)

    assert outcome is SyncOutcome.STALE_VERSION
    assert len(gateway.update_calls) == 2
    assert gateway.update_calls[0][3] is None
    assert gateway.update_calls[1][3] is not None
    assert gateway.events[key]["summary"] == "Entregar informe corregido"


async def test_a_repair_that_hits_a_precondition_does_not_overwrite_either() -> None:
    first = _task(version=1, title="Entregar informe")
    second = _revision(first)
    store = SequencedStore(second, [first, second])
    gateway = Gateway()
    key = _key(first)
    gateway.events[key] = _existing(first)
    gateway.reject_update_at = 2
    gateway.on_reject_body = {"summary": "Editado durante la reparación"}

    outcome = await _engine(store, gateway).sync(first, expected_version=1)

    assert outcome is SyncOutcome.STALE_VERSION
    assert gateway.events[key]["summary"] == "Editado durante la reparación"
    assert len(gateway.update_calls) == 2


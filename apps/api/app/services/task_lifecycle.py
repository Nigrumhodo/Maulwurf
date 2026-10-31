"""J3.6: task completion/dismissal keeps the Calendar event and cancels reminders.

The task lifecycle is separate from `calendar_sync_status` (ESPECIFICACION.md M5). When a
user completes or dismisses a task:

- its already-created Google event is **kept** (and reported back as a notice) unless the
  user explicitly asks for a remote deletion, which is a different operation
  (`google_remote_operations`, S4);
- every reminder that has not been sent yet is cancelled, so no Gmail message goes out for
  a task the user already closed (`notifications.status='pending'`).

The pure transition table is the single authority for `complete`/`dismiss`; the store
adapter locks the task row (scoped to the authenticated user) and performs both writes in
the same transaction. `version` is the optimistic-locking token of the task contract: a
stale `expected_version` is a `409 version_conflict`, while repeating the same command is an
accepted no-op. A task of another tenant is a `404`, indistinguishable from a missing one.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from app.core.errors import ApiError

# The frozen `tasks.status` CHECK (DISENO §2.8).
TASK_STATUSES = ("pending", "needs_review", "confirmed", "dismissed", "completed")

# Legal lifecycle transitions. `scheduled` is never a task status, and neither
# `completed` nor `dismissed` leaves its final state.
TASK_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"dismissed"}),
    "needs_review": frozenset({"dismissed"}),
    "confirmed": frozenset({"completed", "dismissed"}),
    "dismissed": frozenset(),
    "completed": frozenset(),
}

CALENDAR_EVENT_KEPT_NOTICE = "calendar_event_kept"


class LifecycleOutcome(StrEnum):
    UPDATED = "updated"
    ALREADY = "already"
    NOT_FOUND = "not_found"
    VERSION_CONFLICT = "version_conflict"


@dataclass(frozen=True)
class LifecycleTransition:
    """What the store observed and wrote, so the service can answer without re-reading."""

    outcome: LifecycleOutcome
    status: str | None = None
    current_version: int | None = None
    calendar_event_id: str | None = None
    cancelled_reminders: int = 0


@dataclass(frozen=True)
class TaskLifecycleResult:
    task_id: uuid.UUID
    status: str
    cancelled_reminders: int
    calendar_event_kept: bool
    notice: str | None


class TaskLifecycleStore(Protocol):
    async def transition(
        self,
        user_id: uuid.UUID,
        task_id: uuid.UUID,
        *,
        expected_version: int,
        target: str,
        now: datetime,
    ) -> LifecycleTransition: ...


def _invalid_transition(current: str, target: str) -> ApiError:
    return ApiError(
        409,
        "invalid_transition",
        "La operación no es válida para el estado actual de esta tarea.",
        {"current_status": current, "attempted_status": target},
    )


def ensure_task_transition(current: str, target: str) -> str:
    """Validate `current → target`; an unknown status fails closed instead of widening."""
    if current not in TASK_TRANSITIONS:
        raise _invalid_transition(current, target)
    if target == current:
        return target
    if target not in TASK_TRANSITIONS[current]:
        raise _invalid_transition(current, target)
    return target


class TaskLifecycle:
    """Apply `complete`/`dismiss` atomically; Google is never called from here."""

    def __init__(
        self,
        store: TaskLifecycleStore,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._clock = clock

    async def complete(
        self, user_id: uuid.UUID, task_id: uuid.UUID, *, expected_version: int
    ) -> TaskLifecycleResult:
        return await self._apply(user_id, task_id, expected_version, "completed")

    async def dismiss(
        self, user_id: uuid.UUID, task_id: uuid.UUID, *, expected_version: int
    ) -> TaskLifecycleResult:
        return await self._apply(user_id, task_id, expected_version, "dismissed")

    async def _apply(
        self,
        user_id: uuid.UUID,
        task_id: uuid.UUID,
        expected_version: int,
        target: str,
    ) -> TaskLifecycleResult:
        transition = await self._store.transition(
            user_id,
            task_id,
            expected_version=expected_version,
            target=target,
            now=self._clock(),
        )
        if transition.outcome is LifecycleOutcome.NOT_FOUND:
            raise ApiError(404, "not_found", "La tarea no existe.")
        if transition.outcome is LifecycleOutcome.VERSION_CONFLICT:
            raise ApiError(
                409,
                "version_conflict",
                "La tarea cambió desde que la cargaste; recarga antes de continuar.",
                {
                    "current_version": transition.current_version,
                    "current_status": transition.status,
                },
            )
        event_kept = transition.calendar_event_id is not None
        return TaskLifecycleResult(
            task_id=task_id,
            status=transition.status or target,
            cancelled_reminders=transition.cancelled_reminders,
            calendar_event_kept=event_kept,
            notice=CALENDAR_EVENT_KEPT_NOTICE if event_kept else None,
        )

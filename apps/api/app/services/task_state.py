"""A3.7: one pure state-rule authority for tasks, revisions and Calendar sync.

The active `tasks` row has three independent state columns, each with its own closed
vocabulary:

- `status` is the user-visible task lifecycle (``pending|needs_review|confirmed|dismissed|
  completed``); ``scheduled`` is never a task status.
- `calendar_sync_status` is the remote Calendar delivery (``not_requested|pending|synced|
  failed|blocked``), never the task lifecycle.
- `task_revisions.status` is a proposal (``pending_confirmation|activated|discarded``),
  never an active task write.

This module is the single, I/O-free decision core for those three families, mirroring
``app/services/state_machine.py`` (A2.6). Every command is expressed as an
origin/command/destination row, so callers cannot widen a transition with an ad-hoc string
write:

- ``pending|needs_review → confirmed|dismissed`` and ``confirmed → completed|dismissed`` are
  the task commands; a terminal task only accepts its own idempotent repeat.
- only ``pending_confirmation`` may become ``activated`` or ``discarded``.
- reconnecting the Calendar is the only **same-version** command that moves a blocked
  delivery back to ``pending``. Confirming a new task version starts a fresh obligation and
  is a separate command.

Confirmation also needs one business decision: a resolved date, a valid Calendar block and
reconstructed evidence for extraction (or a corpus-grounded chat proposal). Keeping that
decision here makes the API and the tests share the same rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Final

from app.core.errors import ApiError
from app.models.task import (
    CALENDAR_SYNC_STATUSES,
    REVISION_STATUSES,
    TASK_STATUSES,
)
from app.schemas.tasks import validate_event_block

__all__ = [
    "ACTIVATED_REVISION_STATUS",
    "CALENDAR_SYNC_STATUSES",
    "DISCARDED_REVISION_STATUS",
    "PENDING_REVISION_STATUS",
    "REVISION_COMMAND_TRANSITIONS",
    "REVISION_STATUSES",
    "REVISION_TRANSITIONS",
    "SYNC_COMMAND_TRANSITIONS",
    "SYNC_TRANSITIONS",
    "TASK_COMMAND_TRANSITIONS",
    "TASK_STATUSES",
    "TASK_TRANSITIONS",
    "ConfirmationProblem",
    "RevisionCommand",
    "SyncCommand",
    "TaskCommand",
    "Transition",
    "confirmation_date_problem",
    "confirmation_problem",
    "ensure_revision_command",
    "ensure_sync_command",
    "ensure_task_command",
    "ensure_task_transition",
    "evidence_required",
    "invalid_transition",
    "revision_transition",
    "sync_origins",
    "sync_transition",
    "task_transition",
]

PENDING_REVISION_STATUS: Final = "pending_confirmation"
ACTIVATED_REVISION_STATUS: Final = "activated"
DISCARDED_REVISION_STATUS: Final = "discarded"


class TaskCommand(StrEnum):
    EDIT = "edit"
    CONFIRM = "confirm"
    DISMISS = "dismiss"
    COMPLETE = "complete"


class RevisionCommand(StrEnum):
    ACTIVATE = "activate"
    DISCARD = "discard"


class SyncCommand(StrEnum):
    # A new confirmed task version starts a fresh sync obligation.
    REQUEST = "request"
    BLOCK_NEW = "block_new"
    # The Calendar worker's terminal writes start from a pending delivery.
    BLOCK_WORKER = "block_worker"
    SYNC = "sync"
    FAIL = "fail"
    # The only same-version blocked -> pending command.
    RECONNECT = "reconnect"


class ConfirmationProblem(StrEnum):
    DATE_STATUS = "date_status"
    EVENT_BLOCK = "event_block"
    EVIDENCE = "evidence"


@dataclass(frozen=True, order=True)
class Transition:
    """One origin/command/destination row of a transition table."""

    origin: str
    command: str
    destination: str


TASK_COMMAND_TRANSITIONS: Final[frozenset[Transition]] = frozenset(
    {
        Transition("pending", TaskCommand.EDIT, "pending"),
        Transition("needs_review", TaskCommand.EDIT, "needs_review"),
        Transition("confirmed", TaskCommand.EDIT, "confirmed"),
        Transition("pending", TaskCommand.CONFIRM, "confirmed"),
        Transition("needs_review", TaskCommand.CONFIRM, "confirmed"),
        Transition("confirmed", TaskCommand.CONFIRM, "confirmed"),
        Transition("pending", TaskCommand.DISMISS, "dismissed"),
        Transition("needs_review", TaskCommand.DISMISS, "dismissed"),
        Transition("confirmed", TaskCommand.DISMISS, "dismissed"),
        Transition("dismissed", TaskCommand.DISMISS, "dismissed"),
        Transition("confirmed", TaskCommand.COMPLETE, "completed"),
        Transition("completed", TaskCommand.COMPLETE, "completed"),
    }
)

REVISION_COMMAND_TRANSITIONS: Final[frozenset[Transition]] = frozenset(
    {
        Transition(
            PENDING_REVISION_STATUS, RevisionCommand.ACTIVATE, ACTIVATED_REVISION_STATUS
        ),
        Transition(
            PENDING_REVISION_STATUS, RevisionCommand.DISCARD, DISCARDED_REVISION_STATUS
        ),
    }
)

SYNC_COMMAND_TRANSITIONS: Final[frozenset[Transition]] = frozenset(
    {
        *(Transition(status, SyncCommand.REQUEST, "pending") for status in CALENDAR_SYNC_STATUSES),
        *(
            Transition(status, SyncCommand.BLOCK_NEW, "blocked")
            for status in CALENDAR_SYNC_STATUSES
        ),
        Transition("pending", SyncCommand.BLOCK_WORKER, "blocked"),
        Transition("blocked", SyncCommand.BLOCK_WORKER, "blocked"),
        Transition("pending", SyncCommand.SYNC, "synced"),
        Transition("synced", SyncCommand.SYNC, "synced"),
        Transition("pending", SyncCommand.FAIL, "failed"),
        Transition("failed", SyncCommand.FAIL, "failed"),
        Transition("pending", SyncCommand.RECONNECT, "pending"),
        Transition("blocked", SyncCommand.RECONNECT, "pending"),
    }
)

# Origin -> non-self destinations, kept as the public compatibility shape of the former
# task_lifecycle table. The command table above remains the authority.
TASK_TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    status: frozenset(
        row.destination
        for row in TASK_COMMAND_TRANSITIONS
        if row.origin == status and row.destination != status
    )
    for status in TASK_STATUSES
}

REVISION_TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    status: frozenset(
        row.destination
        for row in REVISION_COMMAND_TRANSITIONS
        if row.origin == status
    )
    for status in REVISION_STATUSES
}

SYNC_TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    status: frozenset(
        row.destination
        for row in SYNC_COMMAND_TRANSITIONS
        if row.origin == status
    )
    for status in CALENDAR_SYNC_STATUSES
}


def invalid_transition(current: str, attempted: str) -> ApiError:
    """The single task-family 409 used by the command and compatibility helpers."""
    return ApiError(
        409,
        "invalid_transition",
        "La operación no es válida para el estado actual de esta tarea.",
        {"current_status": current, "attempted_status": attempted},
    )


def _destination(
    transitions: frozenset[Transition], origin: str, command: str
) -> str | None:
    wanted = str(command)
    for row in transitions:
        if row.origin == origin and row.command == wanted:
            return row.destination
    return None


def task_transition(origin: str, command: str) -> str | None:
    """Destination of one task command, or ``None`` when the move is not in the table."""
    return _destination(TASK_COMMAND_TRANSITIONS, origin, command)


def ensure_task_command(
    current: str,
    command: TaskCommand,
    *,
    attempted_status: str | None = None,
) -> str:
    """Validate ``current + command``; fail closed with `409 invalid_transition`."""
    destination = task_transition(current, command)
    if destination is None:
        raise invalid_transition(current, attempted_status or str(command))
    return destination


def ensure_task_transition(current: str, target: str) -> str:
    """Compatibility helper for complete/dismiss callers; unknown states fail closed."""
    allowed = TASK_TRANSITIONS.get(current)
    if allowed is None:
        raise invalid_transition(current, target)
    if target == current:
        return target
    if target not in allowed:
        raise invalid_transition(current, target)
    return target


def revision_transition(origin: str, command: str) -> str | None:
    return _destination(REVISION_COMMAND_TRANSITIONS, origin, command)


def ensure_revision_command(current: str, command: RevisionCommand) -> str:
    destination = revision_transition(current, command)
    if destination is None:
        raise invalid_transition(current, str(command))
    return destination


def sync_transition(origin: str, command: str) -> str | None:
    return _destination(SYNC_COMMAND_TRANSITIONS, origin, command)


def sync_origins(command: SyncCommand) -> frozenset[str]:
    """Origins from which a sync command is allowed; empty means the command is unknown."""
    return frozenset(
        row.origin for row in SYNC_COMMAND_TRANSITIONS if row.command == str(command)
    )


def ensure_sync_command(current: str, command: SyncCommand) -> str:
    destination = sync_transition(current, command)
    if destination is None:
        raise invalid_transition(current, str(command))
    return destination


def evidence_required(source: str, source_count: int) -> bool:
    """Extracted tasks always need evidence; a chat proposal only when it cites a source.

    Manual tasks and general chat suggestions need none. An unknown source fails closed
    instead of silently relaxing the requirement.
    """
    if source == "manual":
        return False
    if source == "chat":
        return source_count > 0
    return True


def confirmation_date_problem(
    *,
    date_status: str,
    event_start_at: datetime | None,
    event_end_at: datetime | None,
    event_start_date: date | None,
    event_end_date_exclusive: date | None,
) -> ConfirmationProblem | None:
    """The date/Calendar-block half of the confirmation precondition."""
    if date_status != "resolved":
        return ConfirmationProblem.DATE_STATUS
    if all(
        value is None
        for value in (
            event_start_at,
            event_end_at,
            event_start_date,
            event_end_date_exclusive,
        )
    ):
        return ConfirmationProblem.EVENT_BLOCK
    try:
        validate_event_block(
            start_at=event_start_at,
            end_at=event_end_at,
            start_date=event_start_date,
            end_date_exclusive=event_end_date_exclusive,
        )
    except ValueError:
        return ConfirmationProblem.EVENT_BLOCK
    return None


def confirmation_problem(
    *,
    date_status: str,
    event_start_at: datetime | None,
    event_end_at: datetime | None,
    event_start_date: date | None,
    event_end_date_exclusive: date | None,
    source: str,
    source_count: int,
    has_evidence: bool,
) -> ConfirmationProblem | None:
    """First unmet confirmation rule, in the API's documented date → block → evidence order."""
    problem = confirmation_date_problem(
        date_status=date_status,
        event_start_at=event_start_at,
        event_end_at=event_end_at,
        event_start_date=event_start_date,
        event_end_date_exclusive=event_end_date_exclusive,
    )
    if problem is not None:
        return problem
    if evidence_required(source, source_count) and not has_evidence:
        return ConfirmationProblem.EVIDENCE
    return None

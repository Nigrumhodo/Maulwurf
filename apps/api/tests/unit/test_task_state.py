"""A3.7: one origin/command/destination table for tasks, revisions and Calendar sync.

The module under test is pure: no database, provider, Google call, audio or secret. The
integration suite (I-S3-AN-03) proves the same rules through the real routes and tables.
"""

from datetime import UTC, date, datetime

import pytest

from app.core.errors import ApiError
from app.models.task import CALENDAR_SYNC_STATUSES, REVISION_STATUSES, TASK_STATUSES
from app.services import task_lifecycle
from app.services.task_state import (
    REVISION_COMMAND_TRANSITIONS,
    SYNC_COMMAND_TRANSITIONS,
    TASK_COMMAND_TRANSITIONS,
    ConfirmationProblem,
    RevisionCommand,
    SyncCommand,
    TaskCommand,
    Transition,
    confirmation_problem,
    ensure_revision_command,
    ensure_sync_command,
    ensure_task_command,
    ensure_task_transition,
    evidence_required,
    revision_transition,
    sync_origins,
    sync_transition,
    task_transition,
)

DUE_DAY = date(2026, 11, 9)
DUE_AT = datetime(2026, 11, 9, 15, 0, tzinfo=UTC)

EXPECTED_TASK_ROWS = frozenset(
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

EXPECTED_REVISION_ROWS = frozenset(
    {
        Transition("pending_confirmation", RevisionCommand.ACTIVATE, "activated"),
        Transition("pending_confirmation", RevisionCommand.DISCARD, "discarded"),
    }
)


# --- Vocabulary drift -------------------------------------------------------------------


def test_task_statuses_are_the_frozen_model_vocabulary() -> None:
    assert tuple(TASK_STATUSES) == (
        "pending",
        "needs_review",
        "confirmed",
        "dismissed",
        "completed",
    )
    assert "scheduled" not in TASK_STATUSES
    assert "synced" not in TASK_STATUSES


def test_command_tables_only_use_closed_vocabularies() -> None:
    assert {row.origin for row in TASK_COMMAND_TRANSITIONS} <= set(TASK_STATUSES)
    assert {row.destination for row in TASK_COMMAND_TRANSITIONS} <= set(TASK_STATUSES)
    assert {row.origin for row in REVISION_COMMAND_TRANSITIONS} <= set(REVISION_STATUSES)
    assert {row.destination for row in REVISION_COMMAND_TRANSITIONS} <= set(
        REVISION_STATUSES
    )
    assert {row.origin for row in SYNC_COMMAND_TRANSITIONS} <= set(
        CALENDAR_SYNC_STATUSES
    )
    assert {row.destination for row in SYNC_COMMAND_TRANSITIONS} <= set(
        CALENDAR_SYNC_STATUSES
    )


# --- Task commands ----------------------------------------------------------------------


def test_the_task_command_table_is_frozen() -> None:
    assert TASK_COMMAND_TRANSITIONS == EXPECTED_TASK_ROWS


def test_every_task_command_row_resolves_to_its_destination() -> None:
    for row in TASK_COMMAND_TRANSITIONS:
        assert task_transition(row.origin, row.command) == row.destination
        assert ensure_task_command(row.origin, TaskCommand(row.command)) == row.destination


@pytest.mark.parametrize(
    ("origin", "command"),
    [
        ("pending", TaskCommand.COMPLETE),
        ("needs_review", TaskCommand.COMPLETE),
        ("dismissed", TaskCommand.COMPLETE),
        ("dismissed", TaskCommand.CONFIRM),
        ("dismissed", TaskCommand.EDIT),
        ("completed", TaskCommand.CONFIRM),
        ("completed", TaskCommand.DISMISS),
        ("completed", TaskCommand.EDIT),
        ("unknown", TaskCommand.EDIT),
    ],
)
def test_forbidden_task_commands_are_a_409(origin: str, command: TaskCommand) -> None:
    with pytest.raises(ApiError) as failure:
        ensure_task_command(origin, command)

    assert failure.value.status_code == 409
    assert failure.value.code == "invalid_transition"
    assert failure.value.details["current_status"] == origin


def test_terminal_tasks_only_accept_their_own_repeat() -> None:
    assert ensure_task_command("completed", TaskCommand.COMPLETE) == "completed"
    assert ensure_task_command("dismissed", TaskCommand.DISMISS) == "dismissed"
    assert ensure_task_command("confirmed", TaskCommand.CONFIRM) == "confirmed"


def test_compatibility_exports_match_the_command_table() -> None:
    assert task_lifecycle.TASK_STATUSES == TASK_STATUSES
    assert task_lifecycle.TASK_TRANSITIONS == {
        "pending": frozenset({"confirmed", "dismissed"}),
        "needs_review": frozenset({"confirmed", "dismissed"}),
        "confirmed": frozenset({"completed", "dismissed"}),
        "dismissed": frozenset(),
        "completed": frozenset(),
    }
    for origin, destinations in task_lifecycle.TASK_TRANSITIONS.items():
        for destination in destinations:
            assert ensure_task_transition(origin, destination) == destination
    assert ensure_task_transition("completed", "completed") == "completed"


# --- Revisions --------------------------------------------------------------------------


def test_the_revision_command_table_is_frozen() -> None:
    assert REVISION_COMMAND_TRANSITIONS == EXPECTED_REVISION_ROWS


@pytest.mark.parametrize(
    ("origin", "command"),
    [
        ("pending_confirmation", RevisionCommand.ACTIVATE),
        ("pending_confirmation", RevisionCommand.DISCARD),
    ],
)
def test_pending_revisions_can_be_promoted_or_discarded(
    origin: str, command: RevisionCommand
) -> None:
    assert revision_transition(origin, command) is not None
    assert ensure_revision_command(origin, command) in {"activated", "discarded"}


@pytest.mark.parametrize("origin", ["activated", "discarded", "unknown"])
def test_terminal_revisions_have_no_outgoing_command(origin: str) -> None:
    for command in RevisionCommand:
        assert revision_transition(origin, command) is None
        with pytest.raises(ApiError) as failure:
            ensure_revision_command(origin, command)
        assert failure.value.code == "invalid_transition"


# --- Calendar sync ----------------------------------------------------------------------


def test_reconnect_is_the_only_same_version_blocked_resume() -> None:
    assert sync_transition("blocked", SyncCommand.RECONNECT) == "pending"
    assert sync_transition("pending", SyncCommand.RECONNECT) == "pending"
    # A *new* version uses request/block_new; those are explicit new obligations, not a
    # resume, and the reconnect row is the only command named for the same version.
    assert sync_transition("blocked", SyncCommand.REQUEST) == "pending"
    assert sync_transition("blocked", SyncCommand.BLOCK_NEW) == "blocked"
    assert sync_origins(SyncCommand.RECONNECT) == frozenset({"pending", "blocked"})


def test_worker_commands_only_start_from_their_legal_origins() -> None:
    assert sync_origins(SyncCommand.SYNC) == frozenset({"pending", "synced"})
    assert sync_origins(SyncCommand.FAIL) == frozenset({"pending", "failed"})
    assert sync_origins(SyncCommand.BLOCK_WORKER) == frozenset({"pending", "blocked"})
    assert sync_origins(SyncCommand.REQUEST) == frozenset(CALENDAR_SYNC_STATUSES)
    assert sync_origins(SyncCommand.BLOCK_NEW) == frozenset(CALENDAR_SYNC_STATUSES)


def test_terminal_sync_self_repeats_are_idempotent() -> None:
    assert ensure_sync_command("synced", SyncCommand.SYNC) == "synced"
    assert ensure_sync_command("failed", SyncCommand.FAIL) == "failed"
    assert ensure_sync_command("blocked", SyncCommand.BLOCK_WORKER) == "blocked"


@pytest.mark.parametrize(
    ("origin", "command"),
    [
        ("failed", SyncCommand.SYNC),
        ("blocked", SyncCommand.SYNC),
        ("synced", SyncCommand.FAIL),
        ("blocked", SyncCommand.FAIL),
        ("synced", SyncCommand.BLOCK_WORKER),
        ("failed", SyncCommand.BLOCK_WORKER),
        ("not_requested", SyncCommand.SYNC),
        ("not_requested", SyncCommand.RECONNECT),
        ("unknown", SyncCommand.REQUEST),
    ],
)
def test_sync_commands_outside_the_table_fail_closed(
    origin: str, command: SyncCommand
) -> None:
    with pytest.raises(ApiError) as failure:
        ensure_sync_command(origin, command)
    assert failure.value.status_code == 409
    assert failure.value.code == "invalid_transition"


# --- Confirmation preconditions ---------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "source_count", "required"),
    [
        ("extraction", 0, True),
        ("extraction", 3, True),
        ("chat", 0, False),
        ("chat", 1, True),
        ("manual", 0, False),
        ("manual", 2, False),
        ("unknown", 0, True),
    ],
)
def test_evidence_requirement_by_source(
    source: str, source_count: int, required: bool
) -> None:
    assert evidence_required(source, source_count) is required


def _confirmation(
    *,
    date_status: str = "resolved",
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    start_date: date | None = DUE_DAY,
    end_date_exclusive: date | None = None,
    source: str = "manual",
    source_count: int = 0,
    has_evidence: bool = False,
) -> ConfirmationProblem | None:
    if end_date_exclusive is None and start_date is not None:
        end_date_exclusive = start_date.replace(day=start_date.day + 1)
    return confirmation_problem(
        date_status=date_status,
        event_start_at=start_at,
        event_end_at=end_at,
        event_start_date=start_date,
        event_end_date_exclusive=end_date_exclusive,
        source=source,
        source_count=source_count,
        has_evidence=has_evidence,
    )


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"date_status": "ambiguous"}, ConfirmationProblem.DATE_STATUS),
        ({"date_status": "missing"}, ConfirmationProblem.DATE_STATUS),
        (
            {"start_date": None, "end_date_exclusive": None},
            ConfirmationProblem.EVENT_BLOCK,
        ),
        (
            {"start_date": None, "end_date_exclusive": DUE_DAY},
            ConfirmationProblem.EVENT_BLOCK,
        ),
        (
            {
                "start_at": DUE_AT,
                "end_at": DUE_AT,
                "start_date": None,
                "end_date_exclusive": None,
            },
            ConfirmationProblem.EVENT_BLOCK,
        ),
        (
            {
                "start_at": DUE_AT,
                "start_date": DUE_DAY,
                "end_date_exclusive": DUE_DAY,
            },
            ConfirmationProblem.EVENT_BLOCK,
        ),
        (
            {"source": "extraction", "source_count": 1, "has_evidence": False},
            ConfirmationProblem.EVIDENCE,
        ),
        (
            {"source": "chat", "source_count": 1, "has_evidence": False},
            ConfirmationProblem.EVIDENCE,
        ),
        ({"source": "chat", "source_count": 0, "has_evidence": False}, None),
        ({"source": "manual", "has_evidence": False}, None),
        (
            {"source": "extraction", "source_count": 1, "has_evidence": True},
            None,
        ),
        (
            {
                "start_at": DUE_AT,
                "end_at": DUE_AT.replace(hour=16),
                "start_date": None,
                "end_date_exclusive": None,
                "source": "chat",
                "source_count": 1,
                "has_evidence": True,
            },
            None,
        ),
    ],
)
def test_confirmation_problem_order_and_rules(
    kwargs: dict[str, object], expected: ConfirmationProblem | None
) -> None:
    assert _confirmation(**kwargs) is expected  # type: ignore[arg-type]


def test_date_and_block_answer_before_evidence() -> None:
    assert (
        _confirmation(
            date_status="missing",
            start_date=None,
            end_date_exclusive=None,
            source="extraction",
            source_count=1,
            has_evidence=False,
        )
        is ConfirmationProblem.DATE_STATUS
    )
    assert (
        _confirmation(
            start_date=None,
            end_date_exclusive=None,
            source="extraction",
            source_count=1,
            has_evidence=False,
        )
        is ConfirmationProblem.EVENT_BLOCK
    )

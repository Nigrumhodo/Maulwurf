"""Per-stage state machine and honest aggregation (A2.6, S2.md §A2.6 on ESPECIFICACION.md M2).

Pure decision core: no I/O, no DB, no router imports (only `ApiError`). Every attempt
transition and every typed terminal destination of the contract goes through here, so A2.7
(cancel/tombstone), A2.8 (retry/reupload) and A2.10 (atomic commit) share ONE transition
authority instead of ad-hoc string writes:

- ASR attempt path: ``awaiting_upload → receiving → transcribing →
  transcript_committed_cleanup_pending → succeeded``; active states may also end in the
  typed finals ``requires_reupload | rejected | cancelled`` (ESPECIFICACION.md M2).
- ``transcript_committed_cleanup_pending`` is never exposed as success and blocks ONLY
  index/analysis (the outbox ``cleanup_gate``); a cancel after the commit is a conflict and
  never destroys the transcript.
- Every invalid transition raises ``409 invalid_transition`` (DISENO §3.5); final states are
  absorbing (self-transition is an accepted no-op).
- Stage states ``not_requested|pending|running|succeeded|failed|cancelled`` are derived from
  ``processing_runs``; ``not_requested`` NEVER counts as success. The aggregate
  ``processing|ready|partial|failed`` follows the honesty rules of the spec: no usable
  transcript ⇒ ``failed``; work still pending/running ⇒ ``processing``.

The state tuples are imported from ``app.models.ingestion`` (the DB CHECK literals live in
migration ``0002_s2_schema``); ``tests/unit/test_state_machine.py`` fails if this module and
those CHECKs ever drift apart.
"""

from collections.abc import Mapping, Sequence
from typing import Protocol

from app.core.errors import ApiError
from app.models.ingestion import ACTIVE_INGESTION_STATES

# Stages tracked per class; `analyze` is F2 and stays `not_requested` for F1 classes.
STAGES = ("index", "analyze")
# Derived per-stage states exposed by the contract (M2); superset of the run-row states.
STAGE_STATES = ("not_requested", "pending", "running", "succeeded", "failed", "cancelled")
# The CHECK on `processing_runs.status` (drift-tested against the model).
RUN_STAGE_STATES = ("pending", "running", "succeeded", "failed", "cancelled")
TERMINAL_STAGE_STATES = ("succeeded", "failed", "cancelled")
ACTIVE_STAGE_STATES = ("pending", "running")
AGGREGATE_STATES = ("processing", "ready", "partial", "failed")

# Absorbing finals of `ingestion_attempts.status`.
TERMINAL_ATTEMPT_STATES = ("succeeded", "requires_reupload", "rejected", "cancelled")

# Legal next states per current status. Self-transitions are handled as no-ops and are not
# listed. Anything not listed here is a contract violation → 409 invalid_transition.
ATTEMPT_TRANSITIONS: dict[str, frozenset[str]] = {
    "awaiting_upload": frozenset({"receiving", "rejected", "requires_reupload", "cancelled"}),
    "receiving": frozenset({"transcribing", "rejected", "requires_reupload", "cancelled"}),
    "transcribing": frozenset(
        {"transcript_committed_cleanup_pending", "rejected", "requires_reupload", "cancelled"}
    ),
    # The commit is the point of no return: from here only verified cleanup reaches success.
    "transcript_committed_cleanup_pending": frozenset({"succeeded"}),
    "succeeded": frozenset(),
    "requires_reupload": frozenset(),
    "rejected": frozenset(),
    "cancelled": frozenset(),
}

# Typed loss destinations (M2): the REASON decides the final state and its persisted
# `error_code`; there is no free-form final write. `explicit_cancel` carries no error code.
LOSS_REASONS: dict[str, tuple[str, str | None]] = {
    "upload_expired": ("cancelled", "upload_expired"),
    "unsupported_format": ("rejected", "unsupported_format"),
    "no_usable_text": ("rejected", "no_usable_text"),
    "technical_loss": ("requires_reupload", "technical_loss"),
    "explicit_cancel": ("cancelled", None),
}


class StageRun(Protocol):
    """Minimal view of a `processing_runs` row consumed by the derivation."""

    status: str
    transcript_version: int


class AttemptRow(Protocol):
    """Minimal view of an `ingestion_attempts` row; `transition` writes only these."""

    status: str
    error_code: str | None


def invalid_transition(current: str, target: str) -> ApiError:
    """The single 409 of the state machine (DISENO §3.5 `invalid_transition`)."""
    return ApiError(
        409,
        "invalid_transition",
        "La operación no es válida para el estado actual de esta clase.",
        {"current_status": current, "attempted_status": target},
    )


def ensure_transition(current: str, target: str) -> str:
    """Validate `current → target`; return `target` or raise `409 invalid_transition`.

    A self-transition is an accepted no-op (idempotent callers). An unknown current status
    fails closed: it is either corrupt data or a state this machine does not know, and must
    never be silently widened.
    """
    allowed = ATTEMPT_TRANSITIONS.get(current)
    if allowed is None:
        raise invalid_transition(current, target)
    if target == current:
        return target
    if target not in allowed:
        raise invalid_transition(current, target)
    return target


def transition(attempt: AttemptRow, target: str, *, error_code: str | None = None) -> str:
    """Validate and write `status` (and optionally `error_code`) on the attempt object.

    Only these two attributes are touched; the caller owns the surrounding transaction.
    """
    ensure_transition(attempt.status, target)
    attempt.status = target
    if error_code is not None:
        attempt.error_code = error_code
    return target


def loss_destination(reason: str) -> tuple[str, str | None]:
    """Typed destination of a loss reason; an unknown reason is a programming error."""
    try:
        return LOSS_REASONS[reason]
    except KeyError:
        raise ValueError(f"unknown loss reason: {reason!r}") from None


def transition_for_loss(attempt: AttemptRow, reason: str) -> str:
    """End an attempt via a typed loss reason (final state + persisted `error_code`)."""
    target, code = loss_destination(reason)
    return transition(attempt, target, error_code=code)


def blocks_derived_stages(status: str) -> bool:
    """True while index/analyze must NOT be dispatched: cleanup is not verified yet.

    The cleanup gate (M2/DISENO §3.6) is released only by `succeeded` (commit +
    `cleanup_status='verified'`); every active status — including
    `transcript_committed_cleanup_pending` — keeps it shut. Final states are governed by the
    "usable transcript" rules of the aggregate/`retry` matrix (A2.8), not by cleanup, so
    they do not report a cleanup block.
    """
    return status in ACTIVE_INGESTION_STATES


def stage_status(
    runs: Sequence[StageRun],
    *,
    required: bool,
    active_transcript_version: int | None = None,
) -> str:
    """Derive one stage's state from its `processing_runs` rows (M2 honesty rules).

    `runs` must be ordered oldest → newest (`ORDER BY created_at`); rows of a different
    `transcript_version` are ignored when `active_transcript_version` is given. Without
    runs a required stage reads `pending` (it is owed work) and a non-required one reads
    `not_requested`, which never counts as success. Outstanding work wins over terminal
    rows; otherwise the latest terminal row decides.
    """
    relevant = [
        run
        for run in runs
        if run.status in RUN_STAGE_STATES
        and (
            active_transcript_version is None
            or run.transcript_version == active_transcript_version
        )
    ]
    if not relevant:
        return "pending" if required else "not_requested"
    if any(run.status == "running" for run in relevant):
        return "running"
    if any(run.status == "pending" for run in relevant):
        return "pending"
    for run in reversed(relevant):
        if run.status in TERMINAL_STAGE_STATES:
            return run.status
    return "pending" if required else "not_requested"  # unreachable; keeps mypy honest


def aggregate_status(
    *,
    attempt_status: str | None,
    has_transcript: bool,
    required_stages: Sequence[str],
    stages: Mapping[str, str],
) -> str:
    """Derive `processing|ready|partial|failed` (M2: `not_requested` ≠ success).

    Precedence: an active attempt (including `transcript_committed_cleanup_pending`) is
    always `processing` — ingestion has not declared success; without a usable transcript
    the class is `failed`; then, while any REQUIRED stage is pending/running, still
    `processing` (work ongoing, even if another required stage already failed); all
    required succeeded → `ready`; otherwise `partial`. Non-required stages (e.g. F1 with
    `analyze=not_requested`) never turn `ready` into `partial`.
    """
    if attempt_status in ACTIVE_INGESTION_STATES:
        return "processing"
    if not has_transcript or attempt_status != "succeeded":
        return "failed"
    # A required stage with unknown OR not_requested status is owed work: it must never
    # read as success, and it is not a failure either (nothing has failed yet).
    required = [stages.get(stage, "pending") for stage in required_stages]
    if any(state in ACTIVE_STAGE_STATES or state == "not_requested" for state in required):
        return "processing"
    if all(state == "succeeded" for state in required):
        return "ready"
    return "partial"

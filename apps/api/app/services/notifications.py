"""M8 notification lifecycle: transitions, delivery lease and persistent dedupe (A4.1).

Pure decision core (no I/O, no sessions, no router imports) so the scheduler, the Gmail
adapter and the preference endpoints all share one authority instead of writing status
words directly:

- ``pending → sending`` is a **claim**: the occurrence is due and a bounded lease is set.
  The claim increments ``attempts``; there is no second delivery attempt for the same row.
- ``sending → sent | failed | delivery_unknown`` is the only way out of a claim. ``sent``
  records the delivery time and the provider message id; ``failed`` records a redacted
  error code for a delivery that is known not to have happened; ``delivery_unknown`` covers
  a lost response or an expired lease.
- ``pending → cancelled`` removes an obsolete future occurrence (A4.3) and ``pending →
  failed`` records a definitive local failure before any provider call. A4.3's cancellation
  core below decides *which* pending rows are obsolete: an older ``due_version`` of the
  task, or a reminder/digest type no longer enabled by the preferences. ``sending`` (it may
  already be leaving through Gmail) and every terminal state stay untouched, and a stale
  occurrence whose instant already passed is cancelled instead of being delivered late.
- ``sent``, ``failed``, ``delivery_unknown`` and ``cancelled`` are terminal for delivery.
  In particular ``delivery_unknown`` never returns to ``pending`` or ``sending``: Gmail does
  not offer exactly-once delivery, so the system never retries it automatically. A manual
  resend is a new audited occurrence (A4.2/J4.5). A4.3's cancellation of a **scheduled**
  occurrence is the one reversible case at the scheduling layer: J4.3 may re-arm that same
  row when the threshold is enabled again and the task generation is confirmed, because the
  row was never claimed or delivered. The delivery helpers never perform that transition and
  a manual resend of a cancelled row stays rejected.
- A ``sending`` row without a lease cannot prove a live owner, so it fails closed as
  ``delivery_unknown`` (uncertain), never as a retryable state.

``dedupe_key`` is the canonical text rendering of
``(user_id, type, task_id, due_version, occurrence)``; the database holds both its UNIQUE
constraint and the frozen ``UNIQUE NULLS NOT DISTINCT`` tuple from DISENO §2.9. The builder
is the only place that formats the identity, so callers cannot invent a second spelling.
A4.2 adds ``occurrence_seq``: scheduled occurrences keep sequence 0 and the five-component
key, while a manual test send or a warned resend appends ``|n:<seq>`` and is stored as a new
audited row. ``ensure_resendable`` is the endpoint's pure gate: only a failed or uncertain
delivery can be repeated.

Every persisted error is a token from ``NOTIFICATION_ERROR_CODES``; any other value is
mapped to ``unknown`` before it reaches the database, whose CHECK also rejects free text.
"""

import uuid
from collections.abc import Iterable
from datetime import date, datetime
from typing import Final, Protocol

from app.models.notification import (
    DIGEST_TYPES,
    NOTIFICATION_STATUSES,
    NOTIFICATION_TYPES,
    REMINDER_TYPES,
)

# Legal next states per current status. Self-transitions are accepted as idempotent
# no-ops and are not listed. Anything absent here is a contract violation.
TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"sending", "failed", "cancelled"}),
    "sending": frozenset({"sent", "failed", "delivery_unknown"}),
    # Absorbing finals: no automatic retry, no resurrection of an uncertain delivery.
    "sent": frozenset(),
    "failed": frozenset(),
    "delivery_unknown": frozenset(),
    "cancelled": frozenset(),
}

# Redacted provider/local error vocabulary. The database only enforces the token shape;
# this tuple is the closed set the service is willing to persist.
NOTIFICATION_ERROR_CODES: Final = (
    "lease_expired",
    "response_lost",
    "provider_unavailable",
    "provider_rejected",
    "rate_limited",
    "invalid_grant",
    "gmail_not_opted_in",
    "recipient_unverified",
    "template_invalid",
    "unknown",
)

LEASE_EXPIRED = "lease_expired"
RESPONSE_LOST = "response_lost"

# A4.3: the frozen reminder vocabulary maps one type to one threshold. The preferences are
# JSON arrays on `settings` (A4.1); only these offsets have a schedulable type, so a value
# without a frozen type enables nothing instead of inventing a new `reminder_t*` spelling.
TIMED_REMINDER_OFFSETS: Final[dict[str, int]] = {
    "reminder_t48": 48,
    "reminder_t24": 24,
    "reminder_t2": 2,
}
ALLDAY_REMINDER_OFFSETS: Final[dict[str, int]] = {
    "reminder_t3d": 3,
    "reminder_t1d": 1,
}
# Types a preference change can disable. An explicit `test` send is an audited one-shot and
# is deliberately absent: changing preferences must not cancel it.
PREFERENCE_NOTIFICATION_TYPES: Final[frozenset[str]] = frozenset(
    (
        *TIMED_REMINDER_OFFSETS,
        *ALLDAY_REMINDER_OFFSETS,
        *DIGEST_TYPES,
    )
)


class NotificationRow(Protocol):
    """Minimal view of a `notifications` row; the helpers write only these fields."""

    status: str
    type: str
    task_id: uuid.UUID | None
    due_version: int | None
    scheduled_at: datetime
    attempts: int
    lease_expires_at: datetime | None
    sent_at: datetime | None
    provider_message_id: str | None
    last_error_code: str | None


class NotificationTransitionError(ValueError):
    """A transition the M8 notification state machine does not allow."""

    def __init__(self, current: str, target: str) -> None:
        super().__init__(f"invalid notification transition: {current} -> {target}")
        self.current = current
        self.target = target


class NotificationNotDue(ValueError):
    """A claim was attempted before the occurrence was due."""


class NotificationLeaseInvalid(ValueError):
    """A claim carried a lease that is not in the future."""


class NotificationUnlinked(ValueError):
    """A reminder has no task link (stale/detached row) and must not be delivered."""


def ensure_transition(current: str, target: str) -> str:
    """Validate ``current → target``; return the target or raise.

    Unknown current statuses fail closed: they are corrupt data or a state this machine
    does not know, and must never be silently widened. A self-transition is an accepted
    no-op for idempotent callers.
    """
    allowed = TRANSITIONS.get(current)
    if allowed is None or target not in NOTIFICATION_STATUSES:
        raise NotificationTransitionError(current, target)
    if target == current:
        return target
    if target not in allowed:
        raise NotificationTransitionError(current, target)
    return target


def _set_status(notification: NotificationRow, target: str) -> str:
    """Validate and write only the status; only the semantic helpers below call it.

    It is deliberately private: a bare status write could produce a `sending` row without a
    lease or a `sent` row without its delivery proof. `claim`, `mark_sent`, `mark_failed`,
    `mark_delivery_unknown` and `cancel` own the fields that make each target honest.
    """
    ensure_transition(notification.status, target)
    if notification.status == target:
        return target
    notification.status = target
    return target


def is_deliverable(notification: NotificationRow) -> bool:
    """A reminder is deliverable only while its task link still exists.

    The FK cascades when a task is deleted, so a persisted reminder always has its task. The
    guard is the fail-closed in-memory half of that invariant: a detached/stale reminder is
    never delivered, because the scheduler must not send a threshold whose task is gone.
    Digests and test sends have no task by contract and remain deliverable.
    """
    return notification.type not in REMINDER_TYPES or notification.task_id is not None


def is_due(notification: NotificationRow, *, now: datetime) -> bool:
    """A pending occurrence is due once its scheduled instant has been reached."""
    return notification.status == "pending" and notification.scheduled_at <= now


def lease_expired(notification: NotificationRow, *, now: datetime) -> bool:
    """True for a live ``sending`` row whose lease cannot prove an owner any more.

    A missing lease on a ``sending`` row is corrupt state (the DB CHECK forbids it), and it
    fails closed: delivery is uncertain, not retryable.
    """
    if notification.status != "sending":
        return False
    if notification.lease_expires_at is None:
        return True
    return notification.lease_expires_at <= now


def claim(
    notification: NotificationRow,
    *,
    now: datetime,
    lease_expires_at: datetime,
) -> str:
    """Claim a due occurrence for sending under a bounded lease.

    The caller computes ``lease_expires_at`` (the scheduler owns the operational budget);
    this core only rejects a lease that does not cover the claim instant and a row that is
    not due yet. The attempt counter grows here, once per claimed occurrence.
    """
    if notification.status != "pending":
        raise NotificationTransitionError(notification.status, "sending")
    if not is_deliverable(notification):
        raise NotificationUnlinked(
            "the occurrence lost its task link; it must not be delivered"
        )
    if notification.scheduled_at > now:
        raise NotificationNotDue(
            f"occurrence scheduled at {notification.scheduled_at.isoformat()} is not due "
            f"at {now.isoformat()}"
        )
    if lease_expires_at <= now:
        raise NotificationLeaseInvalid("the delivery lease must end after the claim instant")
    _set_status(notification, "sending")
    notification.lease_expires_at = lease_expires_at
    notification.attempts += 1
    return "sending"


def mark_sent(
    notification: NotificationRow,
    *,
    now: datetime,
    provider_message_id: str,
) -> str:
    """Record a confirmed delivery; a sent row must identify the provider message."""
    if notification.status != "sending":
        raise NotificationTransitionError(notification.status, "sent")
    if not provider_message_id.strip():
        raise ValueError("a sent notification requires a non-blank provider_message_id")
    _set_status(notification, "sent")
    notification.sent_at = now
    notification.provider_message_id = provider_message_id
    return "sent"


def mark_failed(
    notification: NotificationRow,
    *,
    error_code: str,
) -> str:
    """Record a known non-delivery (local or definitive provider failure).

    The stored value is always a redacted code: any unknown input, including a provider
    message with an address or a body, becomes ``unknown``.
    """
    if notification.status not in ("pending", "sending"):
        raise NotificationTransitionError(notification.status, "failed")
    _set_status(notification, "failed")
    notification.last_error_code = redact_error_code(error_code)
    return "failed"


def mark_delivery_unknown(
    notification: NotificationRow,
    *,
    error_code: str = RESPONSE_LOST,
) -> str:
    """Record an uncertain delivery; the row is terminal and must never auto-retry."""
    if notification.status != "sending":
        raise NotificationTransitionError(notification.status, "delivery_unknown")
    _set_status(notification, "delivery_unknown")
    notification.last_error_code = redact_error_code(error_code) or "unknown"
    return "delivery_unknown"


def expire_lease(notification: NotificationRow, *, now: datetime) -> bool:
    """Rewrite an expired ``sending`` lease to ``delivery_unknown``; return ``True`` if so.

    This is the only expiry rule: the row is never returned to ``pending`` and never left in
    ``sending`` for another worker to pick up, because the previous send may have reached
    Gmail.
    """
    if not lease_expired(notification, now=now):
        return False
    mark_delivery_unknown(notification, error_code=LEASE_EXPIRED)
    return True


def cancel(notification: NotificationRow) -> str:
    """Cancel an obsolete future occurrence; a sent or in-flight delivery is not cancelled."""
    if notification.status != "pending":
        raise NotificationTransitionError(notification.status, "cancelled")
    return _set_status(notification, "cancelled")


def _offset_set(values: Iterable[int], *, field: str) -> set[int]:
    """Validate one preference array; a malformed value must not mean "cancel all"."""
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{field} must be an iterable of integers")
    try:
        items = list(values)
    except TypeError as error:
        raise ValueError(f"{field} must be an iterable of integers") from error
    offsets: set[int] = set()
    for value in items:
        # bool is an int subclass; a JSON `true` is not a threshold.
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{field} must contain non-negative integers")
        offsets.add(value)
    return offsets


def scheduled_reminder_types(
    *,
    reminder_offsets_hours: Iterable[int],
    reminder_allday_offsets_days: Iterable[int],
) -> tuple[str, ...]:
    """Reminder types the thresholds enable, earliest threshold first.

    This is the scheduling half of :func:`enabled_preference_types`: J4.3 needs the ordered
    types to create one occurrence per threshold, while A4.3 only needs the set to cancel
    what a preference change disabled. Both share this mapping so a threshold that enables
    a reminder can never disagree with the one that keeps it alive. An offset without a
    frozen type (for example ``12`` hours) enables nothing.
    """
    hours = _offset_set(reminder_offsets_hours, field="reminder_offsets_hours")
    days = _offset_set(reminder_allday_offsets_days, field="reminder_allday_offsets_days")
    timed = tuple(
        type_ for type_, offset in TIMED_REMINDER_OFFSETS.items() if offset in hours
    )
    allday = tuple(
        type_ for type_, offset in ALLDAY_REMINDER_OFFSETS.items() if offset in days
    )
    return timed + allday


def enabled_preference_types(
    *,
    reminder_offsets_hours: Iterable[int],
    reminder_allday_offsets_days: Iterable[int],
    digest_daily_enabled: bool,
    digest_weekly_enabled: bool,
) -> frozenset[str]:
    """Return the occurrence types the current preferences would schedule today.

    The result is the complete set a preference-driven cancellation compares against: it
    includes both reminder families (timed and all-day) and both digest types, so a caller
    never has to decide which family a stored row belongs to. An offset without a frozen
    type (for example ``12`` hours) enables nothing; it does not invent a new type. The Gmail
    opt-in is a delivery gate owned by J4.1/J4.6, not a cancellation rule: opting out pauses
    delivery without irreversibly cancelling a still-valid occurrence.
    """
    enabled = set(
        scheduled_reminder_types(
            reminder_offsets_hours=reminder_offsets_hours,
            reminder_allday_offsets_days=reminder_allday_offsets_days,
        )
    )
    if digest_daily_enabled:
        enabled.add("digest_daily")
    if digest_weekly_enabled:
        enabled.add("digest_weekly")
    return frozenset(enabled)


def obsolete_pending[RowT: NotificationRow](
    notifications: Iterable[RowT],
    *,
    task_id: uuid.UUID | None = None,
    due_version: int | None = None,
    enabled_types: frozenset[str] | None = None,
) -> tuple[RowT, ...]:
    """Select the pending occurrences a due-date or preference change made obsolete.

    ``due_version`` is the task's current generation and requires ``task_id``: only the
    reminders of that task in another generation are superseded. ``enabled_types`` is the
    complete set returned by :func:`enabled_preference_types`; a pending reminder or digest
    whose type is absent is obsolete. A ``task_id`` scopes the due-date rule only: digests
    are user-level, so a combined call still evaluates them against the preferences. An
    explicit ``test`` send is not preference-driven and is never selected.

    Only ``pending`` rows are selected: ``sending`` may already be leaving through Gmail and
    every terminal state is delivery history. A pending row is selected even when its
    instant already passed, because delivering it late would be the retrospective burst M8
    forbids. At least one rule is required so an empty call cannot cancel a whole user.
    """
    if due_version is None and enabled_types is None:
        raise ValueError("at least one cancellation rule is required")
    if due_version is not None:
        if task_id is None:
            raise ValueError("a due_version rule must be scoped to its task")
        if due_version < 1:
            raise ValueError("due_version must be positive")
    selected: list[RowT] = []
    for notification in notifications:
        if notification.status != "pending":
            continue
        if notification.type in DIGEST_TYPES:
            # Digests are user-level rows: a task scope belongs to the due-date rule and
            # must not hide them from a preference change evaluated in the same call.
            if enabled_types is not None and notification.type not in enabled_types:
                selected.append(notification)
            continue
        if notification.type not in REMINDER_TYPES:
            continue
        if task_id is not None and notification.task_id != task_id:
            continue
        superseded = (
            due_version is not None and notification.due_version != due_version
        )
        disabled = enabled_types is not None and notification.type not in enabled_types
        if superseded or disabled:
            selected.append(notification)
    return tuple(selected)


def cancel_obsolete[RowT: NotificationRow](
    notifications: Iterable[RowT],
    *,
    task_id: uuid.UUID | None = None,
    due_version: int | None = None,
    enabled_types: frozenset[str] | None = None,
) -> tuple[RowT, ...]:
    """Cancel and return the obsolete pending occurrences (A4.3).

    The decision lives in :func:`obsolete_pending`; this helper only applies the frozen
    ``pending → cancelled`` transition to the selection. It is idempotent: a second call
    selects nothing because the rows are already terminal.
    """
    obsolete = obsolete_pending(
        notifications,
        task_id=task_id,
        due_version=due_version,
        enabled_types=enabled_types,
    )
    for notification in obsolete:
        cancel(notification)
    return obsolete


def redact_error_code(value: str | None) -> str | None:
    """Map a free-form value to the closed error vocabulary, or ``None``.

    The comparison is case- and surrounding-whitespace-insensitive so a caller passing
    ``" response_lost "`` still stores the known token; everything else becomes ``unknown``.
    This is the service-level half of the redaction guarantee, backed by the database CHECK
    on the token shape.
    """
    if value is None:
        return None
    candidate = value.strip().lower()
    return candidate if candidate in NOTIFICATION_ERROR_CODES else "unknown"


# Only an occurrence that did not provably reach the provider can be manually repeated:
# `failed` is a known non-delivery and `delivery_unknown` may not have been delivered.
# `pending` is already queued, `sending` is in flight, `sent` is a confirmed duplicate and
# `cancelled` is an obsolete occurrence that must not be revived by a manual resend. J4.3's
# scheduler may re-arm the same scheduled occurrence through its explicit upsert; that is a
# scheduling decision, not this endpoint's repetition.
RESENDABLE_STATUSES: Final = frozenset({"failed", "delivery_unknown"})


class NotificationNotResendable(ValueError):
    """A manual resend was requested for a row the M8 contract cannot repeat."""

    def __init__(self, status: str) -> None:
        super().__init__(f"notification in status {status!r} cannot be resent")
        self.status = status


def ensure_resendable(notification: NotificationRow) -> str:
    """Validate that a manual resend may create a new audited occurrence."""
    if notification.status not in RESENDABLE_STATUSES:
        raise NotificationNotResendable(notification.status)
    return notification.status


def _validate_occurrence_seq(value: int) -> int:
    # `bool` is an `int` subclass; it is never a valid sequence.
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("occurrence_seq must be a non-negative integer")
    return value


def next_occurrence_sequence(current_max: int | None) -> int:
    """Next manual sequence inside one frozen identity; ``0`` is the scheduled row."""
    if current_max is None:
        return 1
    return _validate_occurrence_seq(current_max) + 1


def dedupe_identity(
    *,
    user_id: uuid.UUID,
    type: str,
    task_id: uuid.UUID | None,
    due_version: int | None,
    occurrence: date | None,
) -> tuple[str, str, str | None, int | None, date | None]:
    """Validate and return the frozen dedupe tuple of DISENO §2.9.

    Reminders carry a task, its due generation and a target local date. Digests carry only
    the local date. A test send carries its local date and no task; the occurrence sequence
    of ``dedupe_key`` distinguishes repeated audited sends of the same day.
    """
    if type not in NOTIFICATION_TYPES:
        raise ValueError(f"unknown notification type: {type!r}")
    if due_version is not None and due_version < 1:
        raise ValueError("due_version must be positive")
    if type in REMINDER_TYPES:
        if task_id is None or due_version is None or occurrence is None:
            raise ValueError(
                f"{type} requires task_id, due_version and occurrence"
            )
    elif type in DIGEST_TYPES:
        if task_id is not None or due_version is not None:
            raise ValueError(f"{type} does not belong to a task")
        if occurrence is None:
            raise ValueError(f"{type} requires its local occurrence date")
    else:  # "test"
        if task_id is not None or due_version is not None:
            raise ValueError("a test send does not belong to a task")
        if occurrence is None:
            raise ValueError("a test send requires its local occurrence date")
    return (str(user_id), type, None if task_id is None else str(task_id), due_version, occurrence)


def dedupe_key(
    *,
    user_id: uuid.UUID,
    type: str,
    task_id: uuid.UUID | None,
    due_version: int | None,
    occurrence: date | None,
    occurrence_seq: int = 0,
) -> str:
    """Canonical text rendering of the persistent dedupe identity.

    UUIDs are canonical lowercase strings and NULL components render as ``-``, which a UUID
    or a date can never be, so the key is unambiguous and stable across processes. A
    scheduled occurrence keeps the five-component A4.1 key; a manual repetition appends
    ``|n:<seq>`` so it is auditable without disturbing automatic dedupe.
    """
    identity = dedupe_identity(
        user_id=user_id,
        type=type,
        task_id=task_id,
        due_version=due_version,
        occurrence=occurrence,
    )
    rendered = [
        identity[0],
        identity[1],
        identity[2] if identity[2] is not None else "-",
        str(identity[3]) if identity[3] is not None else "-",
        identity[4].isoformat() if identity[4] is not None else "-",
    ]
    sequence = _validate_occurrence_seq(occurrence_seq)
    if sequence:
        rendered.append(f"n:{sequence}")
    return "|".join(rendered)

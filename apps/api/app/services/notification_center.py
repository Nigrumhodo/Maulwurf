"""M8 notification endpoint support (A4.2): preferences, listing, test and resend.

The pure state machine stays in `app.services.notifications`; this module owns the database
work of the four M8 endpoints:

- the single per-user `settings` row (A4.1) is read without writing and partially updated;
- the deliveries listing is a tenant-filtered keyset page over `scheduled_at DESC, id DESC`
  with an opaque cursor that grants nothing;
- a test send and a manual resend are **new audited occurrences**. Neither mutates an
  existing row, and neither can collide with the automatic occurrence: `occurrence_seq` is
  0 for the scheduled row and the next positive number for each manual repetition, taken
  under a per-identity advisory lock.

Gmail is deliberately absent: J4.1 owns the provider adapter and J4.2 the scheduler. A4.2
records the audited occurrence and returns the contract's `202`; the delivery pipeline later
claims it through the A4.1 lease. No message body, recipient address or provider payload is
read or written here.
"""

import base64
import binascii
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import validation_failed
from app.models import CalendarSettings, Notification, User
from app.services.notifications import (
    dedupe_key,
    ensure_resendable,
    next_occurrence_sequence,
)
from app.services.tenant import get_owned

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100
MAX_CURSOR_LENGTH = 512
CURSOR_VERSION = 1

_CURSOR_INVALID = "cursor inválido o malformado"


@dataclass(frozen=True)
class NotificationCursor:
    """Keyset position of the last row of a delivered page."""

    scheduled_at: datetime
    notification_id: uuid.UUID


# --- Preferences -----------------------------------------------------------------------


async def get_preferences(
    db: AsyncSession, user_id: uuid.UUID
) -> CalendarSettings | None:
    """Read the user's single settings row; `None` means the M8 defaults apply."""
    return await db.get(CalendarSettings, user_id)


async def apply_preferences(
    db: AsyncSession,
    user_id: uuid.UUID,
    changes: Mapping[str, Any],
) -> CalendarSettings:
    """Apply an already validated partial update, creating the row on first write."""
    row = await db.get(CalendarSettings, user_id)
    if row is None:
        row = CalendarSettings(user_id=user_id)
        db.add(row)
    for field, value in changes.items():
        setattr(row, field, value)
    await db.flush()
    return row


# --- Listing cursor --------------------------------------------------------------------


def encode_cursor(cursor: NotificationCursor) -> str:
    """Opaque base64url cursor; the query still filters by tenant on every page."""
    if cursor.scheduled_at.tzinfo is None:
        raise ValueError("the cursor instant must be timezone-aware")
    payload = json.dumps(
        {
            "v": CURSOR_VERSION,
            "t": cursor.scheduled_at.astimezone(UTC).isoformat(),
            "i": str(cursor.notification_id),
        },
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def decode_cursor(value: str) -> NotificationCursor:
    """Validate the opaque keyset cursor; anything malformed is a typed 422."""
    if not value or len(value) > MAX_CURSOR_LENGTH:
        raise validation_failed({"cursor": _CURSOR_INVALID})
    try:
        padded = value + "=" * (-len(value) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        data = json.loads(raw)
    except (ValueError, TypeError, binascii.Error, UnicodeError) as exc:
        raise validation_failed({"cursor": _CURSOR_INVALID}) from exc
    if not isinstance(data, dict) or data.get("v") != CURSOR_VERSION:
        raise validation_failed({"cursor": _CURSOR_INVALID})
    try:
        scheduled_at = datetime.fromisoformat(str(data["t"]))
        notification_id = uuid.UUID(str(data["i"]))
    except (ValueError, TypeError, KeyError) as exc:
        raise validation_failed({"cursor": _CURSOR_INVALID}) from exc
    if scheduled_at.tzinfo is None:
        raise validation_failed({"cursor": _CURSOR_INVALID})
    return NotificationCursor(
        scheduled_at=scheduled_at.astimezone(UTC),
        notification_id=notification_id,
    )


def validate_window(start: datetime | None, end: datetime | None) -> None:
    """The listing window is half-open (`from <= scheduled_at < to`) and tz-aware."""
    if start is not None and start.tzinfo is None:
        raise validation_failed({"from": "la fecha debe incluir zona horaria"})
    if end is not None and end.tzinfo is None:
        raise validation_failed({"to": "la fecha debe incluir zona horaria"})
    if start is not None and end is not None and start >= end:
        raise validation_failed({"to": "el fin debe ser posterior al inicio"})


async def page_notifications(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    statuses: Sequence[str] | None,
    start: datetime | None,
    end: datetime | None,
    limit: int,
    cursor: str | None,
) -> tuple[list[Notification], str | None]:
    """One tenant-scoped page, newest scheduled occurrence first."""
    query = select(Notification).where(Notification.user_id == user_id)
    if statuses:
        query = query.where(Notification.status.in_(statuses))
    if start is not None:
        query = query.where(Notification.scheduled_at >= start)
    if end is not None:
        query = query.where(Notification.scheduled_at < end)
    if cursor is not None:
        position = decode_cursor(cursor)
        query = query.where(
            or_(
                Notification.scheduled_at < position.scheduled_at,
                and_(
                    Notification.scheduled_at == position.scheduled_at,
                    Notification.id < position.notification_id,
                ),
            )
        )

    rows = list(
        (
            await db.scalars(
                query.order_by(
                    Notification.scheduled_at.desc(), Notification.id.desc()
                ).limit(limit + 1)
            )
        ).all()
    )
    next_cursor: str | None = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        next_cursor = encode_cursor(
            NotificationCursor(scheduled_at=last.scheduled_at, notification_id=last.id)
        )
    return rows, next_cursor


# --- Manual occurrences ----------------------------------------------------------------


def local_occurrence_date(now: datetime, timezone: str) -> date:
    """The user's local date, which is part of every task-less dedupe identity."""
    if now.tzinfo is None:
        raise ValueError("the occurrence instant must be timezone-aware")
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        # A corrupt/legacy zone must not 500 a test send; UTC keeps the audit honest.
        return now.astimezone(UTC).date()
    return now.astimezone(zone).date()


async def _lock_identity(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    type_: str,
    task_id: uuid.UUID | None,
    due_version: int | None,
    occurrence: date | None,
) -> None:
    """Serialize manual repetitions of the same frozen identity.

    Two concurrent test sends or resends would otherwise read the same `max(seq)` and race
    the unique constraint. The lock key includes the tenant, so unrelated users never block
    each other; it follows the A2.14/J3.3 advisory-lock convention.
    """
    identity = "|".join(
        [
            str(user_id),
            type_,
            str(task_id) if task_id is not None else "-",
            str(due_version) if due_version is not None else "-",
            occurrence.isoformat() if occurrence is not None else "-",
        ]
    )
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"notification-occurrence:{identity}"},
    )


async def _next_sequence(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    type_: str,
    task_id: uuid.UUID | None,
    due_version: int | None,
    occurrence: date | None,
) -> int:
    query = select(func.max(Notification.occurrence_seq)).where(
        Notification.user_id == user_id,
        Notification.type == type_,
        Notification.task_id.is_(None) if task_id is None else Notification.task_id == task_id,
        Notification.due_version.is_(None)
        if due_version is None
        else Notification.due_version == due_version,
        Notification.occurrence.is_(None)
        if occurrence is None
        else Notification.occurrence == occurrence,
    )
    current = await db.scalar(query)
    return next_occurrence_sequence(None if current is None else int(current))


async def create_test_occurrence(
    db: AsyncSession,
    user: User,
    *,
    now: datetime | None = None,
) -> Notification:
    """Record the audited `test` occurrence for the user's local date.

    The endpoint checks the opt-in and the verified recipient first; this function only
    owns the persistent row. J4.1/J4.2 will claim it through the same lease as any other
    occurrence.
    """
    instant = now or datetime.now(UTC)
    occurrence = local_occurrence_date(instant, user.timezone)
    await _lock_identity(
        db,
        user_id=user.id,
        type_="test",
        task_id=None,
        due_version=None,
        occurrence=occurrence,
    )
    sequence = await _next_sequence(
        db,
        user_id=user.id,
        type_="test",
        task_id=None,
        due_version=None,
        occurrence=occurrence,
    )
    row = Notification(
        user_id=user.id,
        task_id=None,
        type="test",
        occurrence=occurrence,
        due_version=None,
        scheduled_at=instant,
        dedupe_key=dedupe_key(
            user_id=user.id,
            type="test",
            task_id=None,
            due_version=None,
            occurrence=occurrence,
            occurrence_seq=sequence,
        ),
        occurrence_seq=sequence,
        status="pending",
    )
    db.add(row)
    await db.flush()
    return row


async def resend_notification(
    db: AsyncSession,
    user_id: uuid.UUID,
    notification_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> Notification | None:
    """Create a new pending occurrence for a failed or uncertain delivery.

    The source row is locked but never written: `delivery_unknown` stays `delivery_unknown`
    and `sent` stays `sent`. `None` means the row is absent or belongs to another tenant.
    """
    source = await get_owned(
        db, Notification, user_id, notification_id, lock="update"
    )
    if source is None:
        return None
    ensure_resendable(source)
    instant = now or datetime.now(UTC)
    await _lock_identity(
        db,
        user_id=user_id,
        type_=source.type,
        task_id=source.task_id,
        due_version=source.due_version,
        occurrence=source.occurrence,
    )
    sequence = await _next_sequence(
        db,
        user_id=user_id,
        type_=source.type,
        task_id=source.task_id,
        due_version=source.due_version,
        occurrence=source.occurrence,
    )
    row = Notification(
        user_id=user_id,
        task_id=source.task_id,
        type=source.type,
        occurrence=source.occurrence,
        due_version=source.due_version,
        scheduled_at=instant,
        dedupe_key=dedupe_key(
            user_id=user_id,
            type=source.type,
            task_id=source.task_id,
            due_version=source.due_version,
            occurrence=source.occurrence,
            occurrence_seq=sequence,
        ),
        occurrence_seq=sequence,
        status="pending",
    )
    db.add(row)
    await db.flush()
    return row

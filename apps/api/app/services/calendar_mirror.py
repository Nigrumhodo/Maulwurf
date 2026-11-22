"""Atomic publication of one calendar window into the local mirror (J3.7).

The caller owns the outer transaction. A failure rolls the savepoint back and leaves
the previous mirror untouched.
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol, cast

from sqlalchemy import CursorResult, Table, and_, delete, func, or_, select, text, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CalendarEvent, CalendarSettings
from app.services.calendar_remote import RemoteCalendarEvent, RemoteCancelledEvent
from app.services.calendar_window import (
    CANCELLED,
    MISSING,
    CalendarSyncBusy,
    CalendarWindow,
    _user_lock_key,
)

# The spec refreshes the mirror on demand or hourly; two hours is a conservative staleness
# signal for the dashboard, not a scheduled job of its own.
STALE_AFTER = timedelta(hours=2)
# Each manual refresh fans out to Google (`MAX_PAGES` per selected calendar). A per-user
# cooldown keeps a double click or an authenticated script from spending the project quota;
# the stamp is written before the provider call, so a failed attempt also waits.
MANUAL_SYNC_COOLDOWN_SECONDS = 60
# The `FOR UPDATE` transactions only cover the cooldown decision and the token renewal. A
# longer wait is a transient contention signal: failing fast beats pinning a pooled
# connection (and the user's credential) behind Google latency.
LOCK_TIMEOUT_MS = 2_000
LOCK_RETRY_AFTER_SECONDS = 2
_UPSERT_BATCH_SIZE = 1000
# Advisory-lock convention (A2.14, `services/audios.py`): the two-int form namespaces our
# locks instead of a bare bigint. The namespace is the same 0x4D57 ("MW") value that module
# uses and the per-user key uses the same derivation; a key collision only serializes two
# unrelated transactions for a moment and can never admit more work than the limits allow.
_ADVISORY_LOCK_NAMESPACE = 0x4D57

@dataclass(frozen=True)
class PublicationResult:
    """Rows written (`inserted` counts inserts and in-place updates) and rows deleted.

    `removed` includes both stale window rows and pruned pre-window rows.
    """

    inserted: int
    removed: int

class CalendarMirror(Protocol):
    async def replace_window(
        self,
        *,
        user_id: uuid.UUID,
        window: CalendarWindow,
        calendars: tuple[str, ...],
        events: Iterable[RemoteCalendarEvent],
        cancelled: Iterable[RemoteCancelledEvent] = (),
        synced_at: datetime,
    ) -> PublicationResult: ...


def _overlap_filter(window: CalendarWindow) -> Any:
    next_date = window.end_date + timedelta(days=1)
    timed = and_(
        CalendarEvent.start_at.is_not(None),
        CalendarEvent.start_at < window.end,
        CalendarEvent.end_at > window.start,
    )
    all_day = and_(
        CalendarEvent.start_date.is_not(None),
        CalendarEvent.start_date < next_date,
        CalendarEvent.end_date_exclusive > window.start_date,
    )
    return or_(timed, all_day)


def _before_window_filter(window: CalendarWindow) -> Any:
    """Rows that ended before the window and can no longer be observed by the read."""
    timed = and_(
        CalendarEvent.start_at.is_not(None),
        CalendarEvent.end_at <= window.start,
    )
    all_day = and_(
        CalendarEvent.start_date.is_not(None),
        CalendarEvent.end_date_exclusive <= window.start_date,
    )
    return or_(timed, all_day)


class SqlCalendarMirror:
    """PostgreSQL publication of one window; the caller owns the outer transaction."""

    # `Base.__table__` is typed as `FromClause` by mypy; DML needs the concrete `Table`.
    _EVENTS = cast("Table", CalendarEvent.__table__)
    _SETTINGS = cast("Table", CalendarSettings.__table__)

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def replace_window(
        self,
        *,
        user_id: uuid.UUID,
        window: CalendarWindow,
        calendars: tuple[str, ...],
        events: Iterable[RemoteCalendarEvent],
        cancelled: Iterable[RemoteCancelledEvent] = (),
        synced_at: datetime,
    ) -> PublicationResult:
        rows = [
            {
                "user_id": user_id,
                "calendar_id": event.calendar_id,
                "event_id": event.event_id,
                "source": event.source,
                "title": event.title,
                "start_at": event.start_at,
                "end_at": event.end_at,
                "start_date": event.start_date,
                "end_date_exclusive": event.end_date_exclusive,
                "timezone": event.timezone,
                "etag": event.etag,
                "remote_status": event.remote_status,
                "last_synced_at": synced_at,
            }
            for event in events
        ]
        cancelled_keys = [(event.calendar_id, event.event_id) for event in cancelled]
        async with self._db.begin_nested():
            # Two concurrent refreshes for one user must not interleave delete/upsert.
            # The wait is bounded too: a manual request behind a slow or hung publication
            # answers the documented transient 503 instead of pinning a pooled connection.
            try:
                await self._db.execute(
                    text("SELECT set_config('lock_timeout', :timeout, true)"),
                    {"timeout": f"{LOCK_TIMEOUT_MS}ms"},
                )
                await self._db.execute(
                    select(
                        func.pg_advisory_xact_lock(
                            _ADVISORY_LOCK_NAMESPACE, _user_lock_key(user_id)
                        )
                    )
                )
            except DBAPIError as exc:
                if _is_lock_timeout(exc):
                    raise CalendarSyncBusy("publication_lock_timeout") from exc
                raise
            previous_coverage = await self._db.scalar(
                select(CalendarSettings.calendar_coverage_calendar_ids).where(
                    CalendarSettings.user_id == user_id
                )
            )
            # A calendar removed from the selection is no longer read, so none of its rows
            # can be refreshed or observed again by this mirror. Deleting them here (rather
            # than waiting for the window rules, which only look at republished calendars)
            # stops the mirror from growing forever after a deselection. Task-linked rows
            # are spared: the write path owns that link (J3.4/A3.8).
            deselected = sorted(set(previous_coverage or []) - set(calendars))
            deselected_pruned = 0
            if deselected:
                result = await self._db.execute(
                    delete(CalendarEvent).where(
                        CalendarEvent.user_id == user_id,
                        CalendarEvent.calendar_id.in_(deselected),
                        CalendarEvent.task_id.is_(None),
                    )
                )
                deselected_pruned = cast("CursorResult[Any]", result).rowcount or 0
            # Google is queried from `window.start`, so a row that ended before the window
            # will never come back: keeping it would grow the mirror without bound. Rows
            # linked to a task belong to the write path (J3.4/A3.8) and are never pruned.
            pruned = await self._db.execute(
                delete(CalendarEvent).where(
                    CalendarEvent.user_id == user_id,
                    CalendarEvent.calendar_id.in_(list(calendars)),
                    CalendarEvent.task_id.is_(None),
                    _before_window_filter(window),
                )
            )
            # The same rule protects linked rows inside the window. Deleting one here would
            # be followed by the upsert below, which re-inserts the row without `task_id`
            # (the inserted values never carry it) and silently frees the partial unique
            # index `calendar_events_one_link`. If a linked event disappears from Google or
            # moves out of the window, its row is deliberately kept: the read mirror never
            # deletes nor unlinks those rows, and reconciling them is the job of the write
            # path (J3.4/A3.8). A stale linked row is safer than a lost task link.
            removed = await self._db.execute(
                delete(CalendarEvent).where(
                    CalendarEvent.user_id == user_id,
                    CalendarEvent.calendar_id.in_(list(calendars)),
                    CalendarEvent.task_id.is_(None),
                    _overlap_filter(window),
                )
            )
            # A linked row the read no longer returns must not keep presenting old values as
            # current. It is marked `missing` (never deleted nor unlinked) before the upsert,
            # so every event that did arrive rewrites its own status right after; a row
            # already known to be `cancelled` keeps that stronger fact until Google returns
            # the event again. Rows outside the window are not touched: the read never
            # observed them, and an event moved outside the window still exists.
            await self._db.execute(
                update(CalendarEvent)
                .where(
                    CalendarEvent.user_id == user_id,
                    CalendarEvent.calendar_id.in_(list(calendars)),
                    CalendarEvent.task_id.is_not(None),
                    CalendarEvent.remote_status != CANCELLED,
                    _overlap_filter(window),
                )
                .values(
                    remote_status=MISSING,
                    last_synced_at=synced_at,
                    updated_at=func.now(),
                )
            )
            if rows:
                # Upsert on the natural key: an event already mirrored by an earlier
                # publication (for example one rescheduled into this window) updates in
                # place instead of violating the unique key and failing every sync.
                # `source` and `task_id` are deliberately kept: they record who created the
                # row and its task link, not where the current remote values came from.
                # Batched so a large window stays under asyncpg's 32767 bind-parameter cap.
                for offset in range(0, len(rows), _UPSERT_BATCH_SIZE):
                    statement = pg_insert(self._EVENTS).values(
                        rows[offset : offset + _UPSERT_BATCH_SIZE]
                    )
                    await self._db.execute(
                        statement.on_conflict_do_update(
                            index_elements=["user_id", "calendar_id", "event_id"],
                            set_={
                                "title": statement.excluded.title,
                                "start_at": statement.excluded.start_at,
                                "end_at": statement.excluded.end_at,
                                "start_date": statement.excluded.start_date,
                                "end_date_exclusive": statement.excluded.end_date_exclusive,
                                "timezone": statement.excluded.timezone,
                                "etag": statement.excluded.etag,
                                "remote_status": statement.excluded.remote_status,
                                "last_synced_at": statement.excluded.last_synced_at,
                                "updated_at": func.now(),
                            },
                        )
                    )
            if cancelled_keys:
                # Google reported these instances as cancelled. A linked row is kept (deleting
                # it would free `calendar_events_one_link`) but its old values must never be
                # presented as current again; a later read that returns the event rewrites the
                # status from the upsert above.
                await self._db.execute(
                    update(CalendarEvent)
                    .where(
                        CalendarEvent.user_id == user_id,
                        CalendarEvent.calendar_id.in_(list(calendars)),
                        tuple_(
                            CalendarEvent.calendar_id, CalendarEvent.event_id
                        ).in_(cancelled_keys),
                    )
                    .values(
                        remote_status=CANCELLED,
                        last_synced_at=synced_at,
                        updated_at=func.now(),
                    )
                )
            await self._db.execute(
                pg_insert(self._SETTINGS)
                .values(
                    user_id=user_id,
                    calendar_last_synced_at=synced_at,
                    calendar_coverage_from=window.start,
                    calendar_coverage_to=window.end,
                    calendar_coverage_calendar_ids=list(calendars),
                )
                .on_conflict_do_update(
                    index_elements=["user_id"],
                    set_={
                        "calendar_last_synced_at": synced_at,
                        "calendar_coverage_from": window.start,
                        "calendar_coverage_to": window.end,
                        "calendar_coverage_calendar_ids": list(calendars),
                        "updated_at": func.now(),
                    },
                )
            )
            await self._db.flush()
        removed_count = (
            (cast("CursorResult[Any]", pruned).rowcount or 0)
            + (cast("CursorResult[Any]", removed).rowcount or 0)
            + deselected_pruned
        )
        return PublicationResult(inserted=len(rows), removed=removed_count)


def _dedupe[RemoteEvent: (RemoteCalendarEvent, RemoteCancelledEvent)](
    events: Iterable[RemoteEvent],
) -> list[RemoteEvent]:
    """Keep one row per `(calendar, event)`; the last occurrence in read order wins."""
    unique: dict[tuple[str, str], RemoteEvent] = {}
    for event in events:
        unique[(event.calendar_id, event.event_id)] = event
    return list(unique.values())


def _is_lock_timeout(exc: DBAPIError) -> bool:
    """PostgreSQL `lock_not_available` (55P03) raised by the bounded `FOR UPDATE`."""
    return getattr(exc.orig, "sqlstate", None) == "55P03"

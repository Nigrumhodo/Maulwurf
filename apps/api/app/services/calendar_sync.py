"""Manual calendar refresh: credential, cooldown and the Google read (J3.7/J3.8).

Window math, Google page parsing, mirror SQL and local reads live in the sibling modules.
This module claims the cooldown, renews the access token outside a long transaction, then
publishes one complete window.
"""

import math
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CalendarSettings, GoogleCredential, User
from app.services.calendar_mirror import (
    CalendarMirror,
    SqlCalendarMirror,
    _dedupe,
    _is_lock_timeout,
)
from app.services.calendar_read import _normalize_calendars
from app.services.calendar_remote import (
    CalendarEventSource,
    GoogleCalendarEventSource,
    RemoteCalendarEvent,
    RemoteCancelledEvent,
)
from app.services.calendar_window import (
    LOCK_TIMEOUT_MS,
    MANUAL_SYNC_COOLDOWN_SECONDS,
    CalendarSyncBusy,
    CalendarSyncCooldown,
    CalendarSyncUnavailable,
    CalendarWindow,
)
from app.services.google_calendar import (
    GoogleCalendarInvalidGrant,
    GoogleCalendarService,
    GoogleCalendarUnauthorized,
    disconnect_google_credential,
)
from app.services.google_tokens import (
    GoogleTokenRejected,
    GoogleTokenUnavailable,
    fresh_access_token,
)
from app.services.oauth_google import missing_calendar_scopes


@dataclass(frozen=True)
class CalendarSyncResult:
    window: CalendarWindow
    calendars: tuple[str, ...]
    inserted: int
    removed: int
    last_synced_at: datetime

async def sync_calendar_window(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    window: CalendarWindow,
    calendars: Iterable[str],
    source: CalendarEventSource,
    mirror: CalendarMirror | None = None,
    now: datetime | None = None,
) -> CalendarSyncResult:
    """Read every selected calendar, then publish the complete window atomically."""
    selected = _normalize_calendars(calendars)
    synced_at = (now or datetime.now(UTC)).astimezone(UTC)
    if not selected:
        return CalendarSyncResult(
            window=window,
            calendars=(),
            inserted=0,
            removed=0,
            last_synced_at=synced_at,
        )
    # No database work happens until every page of every calendar was read: a failure
    # leaves the previous mirror and its coverage untouched.
    fetched: list[RemoteCalendarEvent] = []
    cancelled: list[RemoteCancelledEvent] = []
    for calendar_id in selected:
        read = await source.list_events(
            calendar_id, time_min=window.start, time_max=window.end
        )
        fetched.extend(read.events)
        cancelled.extend(read.cancelled)
    publication = await (mirror or SqlCalendarMirror(db)).replace_window(
        user_id=user_id,
        window=window,
        calendars=selected,
        events=_dedupe(fetched),
        cancelled=_dedupe(cancelled),
        synced_at=synced_at,
    )
    return CalendarSyncResult(
        window=window,
        calendars=selected,
        inserted=publication.inserted,
        removed=publication.removed,
        last_synced_at=synced_at,
    )


def google_event_source(access_token: str) -> CalendarEventSource:
    """Build the Google reader; tests replace this factory to inject a deterministic read."""
    return GoogleCalendarEventSource(GoogleCalendarService(access_token=access_token))


def manual_sync_cooldown_remaining(
    last_manual_sync_at: datetime | None, *, now: datetime
) -> int:
    """Seconds the user must wait before another manual refresh (0 = allowed)."""
    if last_manual_sync_at is None:
        return 0
    if last_manual_sync_at.tzinfo is None:
        # The column is `timestamptz`, but a fixture or a future caller may pass a naive
        # value; UTC keeps the decision deterministic instead of raising.
        last_manual_sync_at = last_manual_sync_at.replace(tzinfo=UTC)
    elapsed = (
        now.astimezone(UTC) - last_manual_sync_at.astimezone(UTC)
    ).total_seconds()
    remaining = MANUAL_SYNC_COOLDOWN_SECONDS - elapsed
    # `ceil` keeps `Retry-After` at least 1 s while any part of the window is still open;
    # the cap keeps a future stamp (clock skew) from locking the user out beyond the window.
    return min(MANUAL_SYNC_COOLDOWN_SECONDS, math.ceil(remaining)) if remaining > 0 else 0


async def _begin_lock_window(db: AsyncSession) -> None:
    """Start a transaction whose row-lock wait is bounded (`SET LOCAL` is transactional)."""
    await db.execute(
        text("SELECT set_config('lock_timeout', :timeout, true)"),
        {"timeout": f"{LOCK_TIMEOUT_MS}ms"},
    )


async def _read_credential(
    db: AsyncSession, user_id: uuid.UUID
) -> GoogleCredential | None:
    """Fresh credential read; `populate_existing` avoids trusting a stale identity map."""
    return (
        await db.execute(
            select(GoogleCredential)
            .where(GoogleCredential.user_id == user_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


def _require_credential(
    credential: GoogleCredential | None,
) -> GoogleCredential:
    if credential is None or credential.status != "connected":
        raise CalendarSyncUnavailable("google_not_connected")
    if missing_calendar_scopes(credential.scopes or []):
        raise CalendarSyncUnavailable("missing_calendar_scopes")
    return credential


async def _claim_manual_sync(
    db: AsyncSession, *, user_id: uuid.UUID, now: datetime
) -> tuple[tuple[str, ...], str]:
    """Short transaction: validate the state, enforce the cooldown and stamp the attempt.

    Returns the read selection and the user timezone. It commits before returning, so the
    `settings` row lock is released before any Google call. The stamp is written before the
    provider call on purpose: a failed attempt still consumed the quota it protects.
    """
    try:
        await _begin_lock_window(db)
        settings_row = (
            await db.execute(
                select(CalendarSettings)
                .where(CalendarSettings.user_id == user_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if settings_row is None:
            raise CalendarSyncUnavailable("no_read_calendars")
        read_calendars = _normalize_calendars(
            settings_row.calendar_read_calendar_ids
        )
        if not read_calendars:
            raise CalendarSyncUnavailable("no_read_calendars")
        _require_credential(await _read_credential(db, user_id))
        remaining = manual_sync_cooldown_remaining(
            settings_row.calendar_last_manual_sync_at, now=now
        )
        if remaining > 0:
            raise CalendarSyncCooldown(remaining)
        settings_row.calendar_last_manual_sync_at = now
        user = await db.get(User, user_id)
        timezone = user.timezone if user is not None and user.timezone else "UTC"
        await db.flush()
        await db.commit()
    except (CalendarSyncCooldown, CalendarSyncUnavailable):
        # Rejected before any provider call: end the transaction now so the settings row
        # lock is released instead of waiting for the request-scoped teardown.
        await db.rollback()
        raise
    except DBAPIError as exc:
        if _is_lock_timeout(exc):
            raise CalendarSyncBusy("lock_timeout") from exc
        raise
    return read_calendars, timezone


async def _renew_access_token(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    now: datetime,
    force: bool = False,
) -> str:
    """Short locked transaction: validate the grant and renew the token when needed.

    The token POST runs after this read transaction ends. `fresh_access_token` stores the
    new blobs in its own short lock. Calendar pagination never shares that transaction.
    """
    try:
        await _begin_lock_window(db)
        credential = (
            await db.execute(
                select(GoogleCredential)
                .where(GoogleCredential.user_id == user_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        credential = _require_credential(credential)
        try:
            access_token = await fresh_access_token(
                db, credential, now=now, force=force
            )
        except GoogleTokenRejected as exc:
            # `fresh_access_token` already marked the credential disconnected and flushed
            # it; the router commits that state before answering 409.
            raise CalendarSyncUnavailable("google_invalid_grant") from exc
        except GoogleTokenUnavailable as exc:
            if exc.code == "refresh_token_missing":
                raise CalendarSyncUnavailable("google_not_connected") from exc
            raise
        await db.commit()
    except DBAPIError as exc:
        if _is_lock_timeout(exc):
            raise CalendarSyncBusy("lock_timeout") from exc
        raise
    return access_token


async def refresh_calendar_mirror(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    now: datetime | None = None,
    source: CalendarEventSource | None = None,
) -> CalendarSyncResult:
    """Force one full-window refresh for the authenticated user (J3.8).

    This is the manual `POST /calendar/sync` path. Two short `FOR UPDATE` transactions
    validate the state and renew the access token, and each commits before returning; the
    Google reads then run with no row lock and no pooled connection held, so the hourly
    worker or another request for the same user is never blocked behind pagination. The
    token endpoint is the only provider call inside a lock (its 10 s timeout bounds it).
    A third transaction publishes the window atomically via `sync_calendar_window` and
    bounds its per-user advisory lock with the same `LOCK_TIMEOUT_MS`; the caller commits
    it when this returns.

    Every manual call also checks and stamps `calendar_last_manual_sync_at`, so a double
    click or a script answers `CalendarSyncCooldown` (429 `Retry-After`) before reaching
    Google. `CalendarSyncBusy` maps the bounded lock wait to a transient 503.

    `CalendarSyncUnavailable` carries a closed reason for the 409 response. A revoked grant
    also marks the credential `disconnected` here; the router commits that state before
    answering, so the user is told to reconnect instead of retrying forever.
    """
    reference = (now or datetime.now(UTC)).astimezone(UTC)
    read_calendars, timezone = await _claim_manual_sync(
        db, user_id=user_id, now=reference
    )
    access_token = await _renew_access_token(db, user_id=user_id, now=reference)

    window = CalendarWindow.default(now=reference, timezone=timezone)
    reader = source or google_event_source(access_token)
    try:
        return await sync_calendar_window(
            db,
            user_id=user_id,
            window=window,
            calendars=read_calendars,
            source=reader,
            now=reference,
        )
    except GoogleCalendarUnauthorized:
        # Google can invalidate an access token before its advertised expiry. Retry once
        # with a forced renewal; a test-injected reader owns its own auth and is not
        # rebuilt behind its back.
        if source is not None:
            raise
        access_token = await _renew_access_token(
            db, user_id=user_id, now=reference, force=True
        )
        try:
            return await sync_calendar_window(
                db,
                user_id=user_id,
                window=window,
                calendars=read_calendars,
                source=google_event_source(access_token),
                now=reference,
            )
        except (GoogleCalendarUnauthorized, GoogleCalendarInvalidGrant) as exc:
            # The forced renewal already ran, so a second 401 is an auth problem, not a
            # calendar read failure: disconnect and send the user through reconnect.
            await disconnect_google_credential(db, user_id)
            raise CalendarSyncUnavailable("google_invalid_grant") from exc
    except GoogleCalendarInvalidGrant as exc:
        # The Calendar call itself rejected the grant. Keep the disconnect even though the
        # refresh is lost: the next attempt must start from J3.3's reconnect.
        await disconnect_google_credential(db, user_id)
        raise CalendarSyncUnavailable("google_invalid_grant") from exc


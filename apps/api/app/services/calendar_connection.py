"""J3.3: incremental Calendar grants and the single "Maulwurf" secondary calendar.

Contract (ESPECIFICACION M5, DISENO §3.3/§3.8):

- `connect|reconnect` only ask for the Calendar capability scopes; the login scopes travel
  through `include_granted_scopes` and are never re-requested. `openid` is added solely to
  bind the grant to the signed-in Google account (the callback compares the ID token `sub`).
- One "Maulwurf" secondary calendar per user is created the first time Calendar is
  connected. Its remote ID is stored in `settings.calendar_write_calendar_id`; the primary
  calendar is never used.
- **Only an owned secondary calendar is adopted.** The calendar list exposes `accessRole`;
  a calendar merely named/marked "Maulwurf" that the account does not own (a shared copy, a
  subscribed feed) is read-only or someone else's agenda and would fail writes with `403`
  or write into a foreign calendar. Identity therefore requires `accessRole='owner'` and
  `primary` not true, and the stored ID is revalidated through its `CalendarListEntry`
  (the plain `Calendar` resource does not carry `accessRole`).
- **One binding per user, serialized.** A transaction-scoped advisory lock keyed by the user
  serializes `connect_calendar`, so two concurrent callbacks cannot both list an empty
  calendar list and both `POST /calendars`. Without it the `ON CONFLICT` upsert merely makes
  the stored ID converge; the second remote calendar would still exist.
- **Reconciliation before retrying.** A create whose response was lost may still have
  created the calendar. A create failure whose outcome is ambiguous (`transient`,
  unparseable response or `409`) re-lists the calendar list by identity (summary +
  description marker) before failing, so a retry never produces a second "Maulwurf".
- Duplicates already present from an earlier ambiguous creation are resolved by identity
  too: the code pins one deterministic ID (lexicographically smallest) and never creates
  another. It never deletes a remote calendar: destructive remote operations need the
  explicit human confirmation tracked by `google_remote_operations` (S4), so reconciliation
  only stops the duplication.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CalendarSettings, User
from app.services.google_calendar import (
    GoogleCalendarConflict,
    GoogleCalendarNotFound,
    GoogleCalendarResponseError,
    GoogleCalendarService,
    GoogleCalendarTransientError,
)
from app.services.oauth_google import CALENDAR_SCOPES, missing_calendar_scopes, scope_granted

logger = logging.getLogger(__name__)

MAULWURF_CALENDAR_SUMMARY = "Maulwurf"
MAULWURF_CALENDAR_DESCRIPTION = "Calendario gestionado por Maulwurf; no editar a mano."
DisconnectCallback = Callable[[], Awaitable[None]]


@dataclass(frozen=True)
class CalendarBinding:
    """Result of one connect: the remote calendar to write to and how it was obtained."""

    calendar_id: str
    reused: bool
    # True when a create failed ambiguously and the calendar was found by identity instead.
    reconciled: bool


def calendar_service(
    access_token: str, on_invalid_grant: DisconnectCallback
) -> GoogleCalendarService:
    """Build the client; tests replace this factory to inject a deterministic transport."""
    return GoogleCalendarService(access_token=access_token, on_invalid_grant=on_invalid_grant)


def is_owned_calendar(entry: dict[str, object]) -> bool:
    """Whether the entry is a secondary calendar this account owns and may write to.

    `accessRole` comes from the calendar list entry. A shared or subscribed copy can carry
    the same summary/description marker while being read-only (`reader`) or owned by another
    account; writing there would fail with `403` or pollute a foreign agenda, so a missing
    or non-owner role fails closed.
    """
    return entry.get("accessRole") == "owner" and entry.get("primary") is not True


def is_maulwurf_calendar(entry: dict[str, object]) -> bool:
    """Identity of the app-created calendar: marker + owner role, never primary.

    The description is a stable marker written at creation, so a user calendar that merely
    shares the name is never adopted. `created_time` is not exposed by the Calendar API, so
    the marker plus a deterministic ID order is the strongest available identity.
    """
    return (
        entry.get("summary") == MAULWURF_CALENDAR_SUMMARY
        and entry.get("description") == MAULWURF_CALENDAR_DESCRIPTION
        and is_owned_calendar(entry)
    )


async def _find_maulwurf(service: GoogleCalendarService) -> list[str]:
    entries = await service.list_calendars()
    matches = [
        entry
        for entry in entries
        if is_maulwurf_calendar(entry) and isinstance(entry.get("id"), str)
    ]
    if len(matches) > 1:
        # Never delete: the user may already have events there. Pinning one ID stops the
        # code from creating more calendars without an unconfirmed destructive operation.
        logger.warning("calendar.multiple_matches count=%d", len(matches))
    return sorted({str(entry["id"]) for entry in matches})


async def binding_from_google(
    service: GoogleCalendarService, *, stored_calendar_id: str | None, timezone: str
) -> CalendarBinding:
    """Resolve the single writable "Maulwurf" calendar, reconciling ambiguous creations.

    The stored ID wins when it still exists (the user may have renamed the calendar). A
    stored ID that Google no longer knows is dropped and reconciled from scratch. No remote
    call other than Calendar list/get/create is made.
    """
    if stored_calendar_id:
        try:
            entry = await service.get_calendar_list_entry(stored_calendar_id)
        except GoogleCalendarNotFound:
            logger.warning("calendar.binding_missing")
        else:
            if is_owned_calendar(entry):
                return CalendarBinding(
                    calendar_id=stored_calendar_id, reused=True, reconciled=False
                )
            # The stored ID now points to a calendar this account does not own (shared copy,
            # or a stale ID from before the ownership rule). Never write there: reconcile.
            logger.warning("calendar.binding_not_owned")
    matches = await _find_maulwurf(service)
    if matches:
        return CalendarBinding(calendar_id=matches[0], reused=True, reconciled=False)
    try:
        created = await service.create_calendar(
            summary=MAULWURF_CALENDAR_SUMMARY,
            description=MAULWURF_CALENDAR_DESCRIPTION,
            timezone=timezone,
        )
    except (
        GoogleCalendarConflict,
        GoogleCalendarResponseError,
        GoogleCalendarTransientError,
    ) as exc:
        # Ambiguous outcome: Google may have created the calendar before the response was
        # lost. Re-list by identity BEFORE surfacing the error (or retrying).
        logger.warning("calendar.create_ambiguous error=%s", exc.code)
        matches = await _find_maulwurf(service)
        if matches:
            return CalendarBinding(
                calendar_id=matches[0], reused=True, reconciled=True
            )
        raise
    return CalendarBinding(
        calendar_id=str(created["id"]), reused=False, reconciled=False
    )


async def _lock_calendar_binding(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Serialize every connect of one user for the rest of the transaction.

    `binding_from_google` is list-then-create; without a lock two callbacks can both list an
    empty calendar list and both create a remote "Maulwurf". The lock is transaction-scoped
    (`pg_advisory_xact_lock`), so it releases on commit or rollback with no cleanup path, and
    it is keyed by the user through the 64-bit `hashtextextended` hash. Collisions only
    serialize two unrelated users; they cannot leak data. `connect_calendar` is the only
    caller, so no other lock order exists to deadlock with.
    """
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(CAST(:key AS text), 0))"),
        {"key": f"calendar_binding:{user_id}"},
    )


async def connect_calendar(
    db: AsyncSession,
    *,
    user: User,
    access_token: str,
    on_invalid_grant: DisconnectCallback,
) -> CalendarBinding:
    """Reconcile the calendar and persist its ID in the user's `settings` row.

    The caller owns the transaction: tokens, settings and the reconnect resumption commit
    together, so a Calendar failure never leaves a half-connected integration behind. The
    advisory lock makes concurrent callbacks for the same user take turns, so the invariant
    "exactly one Maulwurf calendar" holds across concurrent connects, not just across
    sequential retries.
    """
    await _lock_calendar_binding(db, user.id)
    service = calendar_service(access_token, on_invalid_grant)
    row = await db.get(CalendarSettings, user.id)
    binding = await binding_from_google(
        service,
        stored_calendar_id=row.calendar_write_calendar_id if row is not None else None,
        timezone=user.timezone,
    )
    # Upsert: two concurrent callbacks for the same user must converge on the last
    # reconciled ID instead of racing the primary key. `updated_at` is set explicitly
    # because the raw statement bypasses the ORM's onupdate.
    await db.execute(
        pg_insert(CalendarSettings)
        .values(user_id=user.id, calendar_write_calendar_id=binding.calendar_id)
        .on_conflict_do_update(
            index_elements=["user_id"],
            set_={
                "calendar_write_calendar_id": binding.calendar_id,
                "updated_at": func.now(),
            },
        )
    )
    await db.flush()
    return binding


def requested_scopes(granted: list[str] | tuple[str, ...], *, reconnect: bool) -> list[str]:
    """Scopes the authorization request must ask for.

    `reconnect` asks for the whole Calendar set: after `invalid_grant` the previous grant
    cannot be trusted, so "only new scopes" relative to login is the right set. `connect`
    asks only for what the recorded grant does not already contain; if everything is
    granted it still asks for the Calendar set to renew the offline token, which Google
    treats as an already-granted permission (no new consent is asked).
    """
    if reconnect:
        return list(CALENDAR_SCOPES)
    missing = missing_calendar_scopes(granted)
    return missing or list(CALENDAR_SCOPES)


def grant_covers_calendar(tokens: dict[str, object]) -> bool:
    """Whether the token response proves every Calendar capability was granted."""
    raw = tokens.get("scope")
    if not isinstance(raw, str):
        return False
    granted = raw.split()
    return all(scope_granted(granted, scope) for scope in CALENDAR_SCOPES)


__all__ = [
    "CALENDAR_SCOPES",
    "CalendarBinding",
    "MAULWURF_CALENDAR_DESCRIPTION",
    "MAULWURF_CALENDAR_SUMMARY",
    "binding_from_google",
    "calendar_service",
    "connect_calendar",
    "grant_covers_calendar",
    "is_maulwurf_calendar",
    "is_owned_calendar",
    "requested_scopes",
]

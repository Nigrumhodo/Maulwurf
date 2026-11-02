"""Calendar endpoints: mirror read, conflicts and manual refresh (J3.7/J3.8).

`GET /calendar/events` and `GET /calendar/conflicts` read only the local
`calendar_events` mirror; neither calls Google. The requested range defaults to -7/+60
local days and is answered with the mirror's freshness and a closed-set coverage notice,
so a partial or stale cache is visible instead of being presented as the calendar.

`GET /calendar/conflicts` keeps M5's distinction: `time_conflicts` pairs timed events
whose intervals actually overlap, while `deadline_clashes` groups two or more active task
deadlines on the same user-local day. All-day events and deadlines never become time
conflicts.

`POST /calendar/sync` forces a complete refresh of the same window. It validates the
Google connection, the Calendar scopes and the read selection first (409 with a closed
reason otherwise), then reads every calendar and replaces the mirror atomically through
`services.calendar_sync.refresh_calendar_mirror`. A per-user cooldown answers `429
rate_limited` with `Retry-After` before any provider call, the credential row lock is
released before the Google reads, and a bounded lock wait answers a transient `503`. The
frozen contract answers `202`; the body carries the covered window and the publication
counts, and the client re-reads the mirror through `GET /calendar/events`.
"""

from datetime import date
from typing import Annotated, NoReturn

from fastapi import APIRouter, Query, Response

from app.core.deps import CurrentSession, DbSession, MutationSession
from app.core.errors import (
    calendar_sync_cooldown,
    calendar_sync_temporarily_unavailable,
    calendar_sync_unavailable,
    google_integration_unavailable,
    validation_failed,
)
from app.schemas.calendar import (
    CalendarConflictsOut,
    CalendarEventOut,
    CalendarEventsOut,
    CalendarSyncOut,
    ConflictEventOut,
    DeadlineClashOut,
    DeadlineItemOut,
    TimeConflictOut,
)
from app.services.calendar_sync import (
    LOCK_RETRY_AFTER_SECONDS,
    CalendarService,
    CalendarSyncBusy,
    CalendarSyncCooldown,
    CalendarSyncError,
    CalendarSyncUnavailable,
    CalendarWindowInvalid,
    refresh_calendar_mirror,
)
from app.services.conflicts import CalendarConflicts, ConflictService
from app.services.google_calendar import (
    GoogleCalendarError,
    GoogleCalendarInvalidGrant,
    GoogleCalendarTransientError,
    disconnect_google_credential,
)
from app.services.google_tokens import GoogleTokenTransient, GoogleTokenUnavailable

router = APIRouter(tags=["calendar"])


def _raise_window_invalid(exc: CalendarWindowInvalid) -> NoReturn:
    field = exc.field or "from"
    message = (
        "`to` no puede ser anterior a `from`"
        if exc.code == "window_inverted"
        else "rango de fechas fuera del límite permitido"
    )
    raise validation_failed({field: message}) from exc


@router.get(
    "/calendar/events",
    responses={
        200: {
            "description": (
                "Local mirror events for the range, with `last_synced_at`, `coverage_ok` "
                "and a coverage notice"
            )
        },
        401: {"description": "Sin sesión (auth_required)"},
        422: {"description": "validation_failed: from/to inválidos o rango invertido"},
    },
)
async def calendar_events(
    response: Response,
    session: CurrentSession,
    db: DbSession,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
) -> CalendarEventsOut:
    """Tenant-scoped read of the mirror for an inclusive local-date range.

    A foreign or unknown calendar is irrelevant here: every row is already tenant-owned and
    the query is filtered by the authenticated session, so a guessed id cannot widen it.
    """
    response.headers["Cache-Control"] = "no-store"
    service = CalendarService(db)
    try:
        page = await service.page(
            user_id=session.user_id, date_from=date_from, date_to=date_to
        )
    except CalendarWindowInvalid as exc:
        _raise_window_invalid(exc)

    return CalendarEventsOut(
        events=[
            CalendarEventOut(
                id=str(event.id),
                calendar_id=event.calendar_id,
                event_id=event.event_id,
                title=event.title,
                source=event.source,
                start_at=event.start_at,
                end_at=event.end_at,
                start_date=event.start_date,
                end_date_exclusive=event.end_date_exclusive,
                timezone=event.timezone,
                remote_status=event.remote_status,
                last_synced_at=event.last_synced_at,
            )
            for event in page.events
        ],
        date_from=page.date_from,
        date_to=page.date_to,
        last_synced_at=page.last_synced_at,
        coverage_ok=page.coverage_ok,
        coverage_notice=page.coverage_notice,
        stale=page.stale,
    )


@router.get(
    "/calendar/conflicts",
    responses={
        200: {
            "description": (
                "Timed overlaps (`time_conflicts`) and same-day deadline clusters "
                "(`deadline_clashes`) for the range, with the mirror's freshness"
            )
        },
        401: {"description": "Sin sesión (auth_required)"},
        422: {"description": "validation_failed: from/to inválidos o rango invertido"},
    },
)
async def calendar_conflicts(
    response: Response,
    session: CurrentSession,
    db: DbSession,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
) -> CalendarConflictsOut:
    """M5 conflicts for one tenant over the same window as the event read.

    Only the mirror and the user's own task deadlines are read. A guessed calendar ID,
    event ID or task ID cannot widen the result because every query starts from the
    authenticated `user_id`.
    """
    response.headers["Cache-Control"] = "no-store"
    try:
        conflicts: CalendarConflicts = await ConflictService(db).conflicts(
            user_id=session.user_id, date_from=date_from, date_to=date_to
        )
    except CalendarWindowInvalid as exc:
        _raise_window_invalid(exc)

    return CalendarConflictsOut(
        time_conflicts=[
            TimeConflictOut(
                a=ConflictEventOut(
                    event_id=pair.a.event_id,
                    calendar_id=pair.a.calendar_id,
                    title=pair.a.title,
                    start_at=pair.a.start_at,
                    end_at=pair.a.end_at,
                ),
                b=ConflictEventOut(
                    event_id=pair.b.event_id,
                    calendar_id=pair.b.calendar_id,
                    title=pair.b.title,
                    start_at=pair.b.start_at,
                    end_at=pair.b.end_at,
                ),
            )
            for pair in conflicts.report.time_conflicts
        ],
        deadline_clashes=[
            DeadlineClashOut(
                date=clash.date,
                items=[
                    DeadlineItemOut(task_id=str(item.task_id), tipo=item.tipo)
                    for item in clash.items
                ],
            )
            for clash in conflicts.report.deadline_clashes
        ],
        date_from=conflicts.date_from,
        date_to=conflicts.date_to,
        last_synced_at=conflicts.last_synced_at,
        coverage_ok=conflicts.coverage_ok,
        coverage_notice=conflicts.coverage_notice,
        stale=conflicts.stale,
    )


@router.post(
    "/calendar/sync",
    status_code=202,
    responses={
        202: {"description": "Full window refreshed and published atomically"},
        401: {"description": "Sin sesión (auth_required)"},
        403: {"description": "csrf_invalid"},
        409: {
            "description": (
                "calendar_sync_unavailable: no read calendars, no connected grant or a "
                "grant that must be renewed"
            )
        },
        429: {
            "description": (
                "rate_limited: manual refresh requested inside the per-user cooldown, "
                "with Retry-After"
            )
        },
        503: {"description": "calendar_sync_temporarily_unavailable"},
    },
)
async def calendar_sync(
    response: Response, session: MutationSession, db: DbSession
) -> CalendarSyncOut:
    """Force a full refresh of the -7/+60 window for the authenticated user.

    The frozen contract answers `202`; the refresh itself is bounded by the same read and
    atomic publication as the hourly path, so returning after it completes is honest: the
    body states the covered window and the counts, and the mirror is already readable.
    """
    response.headers["Cache-Control"] = "no-store"
    try:
        result = await refresh_calendar_mirror(db, user_id=session.user_id)
    except CalendarSyncCooldown as exc:
        # The cooldown is decided before any provider call; nothing was written past the
        # attempt stamp, which the service already committed.
        raise calendar_sync_cooldown(exc.retry_after_seconds) from exc
    except CalendarSyncBusy as exc:
        # The bounded row-lock wait expired: transient contention, not a state error.
        raise calendar_sync_temporarily_unavailable(
            retry_after_seconds=LOCK_RETRY_AFTER_SECONDS
        ) from exc
    except CalendarSyncUnavailable as exc:
        if exc.code == "google_invalid_grant":
            # `refresh_calendar_mirror` already disconnected the credential; commit that
            # state so the next attempt sends the user through J3.3's reconnect.
            await db.commit()
        # Any other state conflict wrote nothing; the request-scoped session rolls back.
        raise calendar_sync_unavailable(exc.code) from exc
    except GoogleCalendarInvalidGrant as exc:
        # Defensive: the service converts this path, but never leave a revoked grant
        # looking connected if a future caller changes that contract.
        await disconnect_google_credential(db, session.user_id)
        await db.commit()
        raise calendar_sync_unavailable("google_invalid_grant") from exc
    except (GoogleCalendarTransientError, GoogleTokenTransient) as exc:
        raise calendar_sync_temporarily_unavailable() from exc
    except GoogleTokenUnavailable as exc:
        if exc.code in {"refresh_token_missing"}:
            raise calendar_sync_unavailable("google_not_connected") from exc
        raise google_integration_unavailable() from exc
    except (CalendarSyncError, GoogleCalendarError) as exc:
        # A malformed provider payload or a Calendar permission/not-found answer: the
        # mirror is untouched and the user cannot fix it by retrying the same state.
        raise calendar_sync_unavailable("calendar_read_failed") from exc
    await db.commit()
    return CalendarSyncOut(
        date_from=result.window.start_date,
        date_to=result.window.end_date,
        calendars=list(result.calendars),
        inserted=result.inserted,
        removed=result.removed,
        last_synced_at=result.last_synced_at,
    )

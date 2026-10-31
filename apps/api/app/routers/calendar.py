"""`GET /calendar/events` (J3.7, S3.md §1.3 and DISENO §3.8).

Reads only the local `calendar_events` mirror; it never calls Google. The requested range
defaults to -7/+60 local days and is answered with the mirror's freshness and a closed-set
coverage notice, so a partial or stale cache is visible instead of being presented as the
calendar. The SQL and the coverage decision live in `services.calendar_sync`.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query, Response

from app.core.deps import CurrentSession, DbSession
from app.core.errors import validation_failed
from app.schemas.calendar import CalendarEventOut, CalendarEventsOut
from app.services.calendar_sync import CalendarService, CalendarWindowInvalid

router = APIRouter(tags=["calendar"])


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
        field = exc.field or "from"
        message = (
            "`to` no puede ser anterior a `from`"
            if exc.code == "window_inverted"
            else "rango de fechas fuera del límite permitido"
        )
        raise validation_failed({field: message}) from exc

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

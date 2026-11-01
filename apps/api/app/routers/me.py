"""Perfil del usuario (A1.4/A1.8): `GET /me` con bootstrap CSRF y `PATCH /me`.

`GET /me` nunca es cacheable. `PATCH /me` solo cambia la zona horaria IANA del perfil, que
afecta a digests futuros y no a las fechas de clases ya registradas (snapshot en `audios`).
Settings de Calendar llegan en S3 y preferencias Gmail/digests en S4.
"""
from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.deps import COOKIE_NAME, CurrentSession, DbSession, MutationSession
from app.core.errors import auth_required
from app.core.validation import is_iana_timezone
from app.models import CalendarSettings, User
from app.models import Session as SessionRow
from app.schemas.notifications import (
    NotificationPreferencesOut,
    NotificationPreferencesPatch,
    format_digest_time,
)
from app.services import notification_center
from app.services.sessions import csrf_token_for

router = APIRouter(tags=["me"])


class MeResponse(BaseModel):
    id: str
    email: str
    display_name: str | None
    timezone: str
    csrf_token: str


class MeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: str = Field(min_length=1, max_length=64)

    @field_validator("timezone")
    @classmethod
    def _iana(cls, value: str) -> str:
        if not is_iana_timezone(value):
            raise ValueError("zona horaria IANA desconocida")
        return value


async def _profile(
    request: Request, response: Response, session: SessionRow, db: DbSession
) -> MeResponse:
    response.headers["Cache-Control"] = "no-store"
    user = await db.get(User, session.user_id)
    if user is None:  # current_session ya exige usuario activo
        raise auth_required()
    return MeResponse(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        timezone=user.timezone,
        csrf_token=csrf_token_for(request.cookies[COOKIE_NAME]),
    )


@router.get("/me")
async def get_me(
    request: Request, response: Response, session: CurrentSession, db: DbSession
) -> MeResponse:
    return await _profile(request, response, session, db)


@router.patch("/me")
async def update_me(
    body: MeUpdate,
    request: Request,
    response: Response,
    session: MutationSession,
    db: DbSession,
) -> MeResponse:
    user = await db.get(User, session.user_id)
    if user is None:
        raise auth_required()
    user.timezone = body.timezone
    profile = await _profile(request, response, session, db)
    await db.commit()
    return profile


def _preferences_out(row: CalendarSettings | None) -> NotificationPreferencesOut:
    """Complete M8 preference set; an absent row means the opted-out defaults."""
    if row is None:
        return NotificationPreferencesOut(
            gmail_opt_in=False,
            digest_daily_enabled=False,
            digest_daily_time="07:00",
            digest_weekly_enabled=False,
            digest_weekday=0,
            reminder_offsets_hours=[48, 24, 2],
            reminder_allday_offsets_days=[3, 1],
            updated_at=None,
        )
    return NotificationPreferencesOut(
        gmail_opt_in=row.gmail_opt_in,
        digest_daily_enabled=row.digest_daily_enabled,
        digest_daily_time=format_digest_time(row.digest_daily_time),
        digest_weekly_enabled=row.digest_weekly_enabled,
        digest_weekday=row.digest_weekday,
        reminder_offsets_hours=list(row.reminder_offsets_hours),
        reminder_allday_offsets_days=list(row.reminder_allday_offsets_days),
        updated_at=row.updated_at,
    )


@router.get(
    "/me/notifications",
    responses={
        200: {"description": "Preferencias M8 completas del usuario"},
        401: {"description": "Sin sesión (auth_required)"},
    },
)
async def get_notification_preferences(
    response: Response, session: CurrentSession, db: DbSession
) -> NotificationPreferencesOut:
    """Read the current preferences without creating the settings row."""
    response.headers["Cache-Control"] = "no-store"
    row = await notification_center.get_preferences(db, session.user_id)
    return _preferences_out(row)


@router.patch(
    "/me/notifications",
    responses={
        200: {"description": "Preferencias M8 completas tras la actualización parcial"},
        401: {"description": "Sin sesión (auth_required)"},
        403: {"description": "Origin/CSRF inválidos (csrf_invalid)"},
        422: {"description": "validation_failed: preferencia inválida"},
    },
)
async def update_notification_preferences(
    body: NotificationPreferencesPatch,
    response: Response,
    session: MutationSession,
    db: DbSession,
) -> NotificationPreferencesOut:
    """Apply only the sent fields; Gmail remains opt-in and no scope is granted here."""
    response.headers["Cache-Control"] = "no-store"
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        row = await notification_center.get_preferences(db, session.user_id)
        return _preferences_out(row)
    row = await notification_center.apply_preferences(db, session.user_id, changes)
    out = _preferences_out(row)
    await db.commit()
    return out

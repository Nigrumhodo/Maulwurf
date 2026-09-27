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
from app.models import Session as SessionRow
from app.models import User
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

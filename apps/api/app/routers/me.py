"""`GET /me` (A1.4): perfil mínimo y bootstrap del token CSRF, nunca cacheable.

`PATCH /me` (zona horaria) y el resto del perfil llegan en A1.8.
"""
from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from app.core.deps import COOKIE_NAME, CurrentSession, DbSession
from app.core.errors import auth_required
from app.models import User
from app.services.sessions import csrf_token_for

router = APIRouter(tags=["me"])


class MeResponse(BaseModel):
    id: str
    email: str
    display_name: str | None
    timezone: str
    csrf_token: str


@router.get("/me")
async def get_me(
    request: Request, response: Response, session: CurrentSession, db: DbSession
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

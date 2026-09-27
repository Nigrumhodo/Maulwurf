"""Dependencias de autenticación, CSRF y Origin para routers (A1.4).

Uso: `Depends(current_session)` en lecturas autenticadas y `Depends(require_mutation)` en
toda ruta `POST/PUT/PATCH/DELETE`, incluido el `PUT` binario de ingesta.
"""
import hmac
from typing import Annotated

from fastapi import Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session
from app.core.errors import auth_required, csrf_invalid
from app.models import Session
from app.services import sessions

COOKIE_NAME = "mw_session"
CSRF_HEADER = "X-CSRF-Token"
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

DbSession = Annotated[AsyncSession, Depends(get_session)]


def set_session_cookie(response: Response, cookie_token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        cookie_token,
        max_age=settings.session_ttl_hours * 3600,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/", secure=True, httponly=True, samesite="lax")


async def current_session(request: Request, db: DbSession) -> Session:
    session = await sessions.resolve_session(db, request.cookies.get(COOKIE_NAME))
    if session is None:
        raise auth_required()
    return session


CurrentSession = Annotated[Session, Depends(current_session)]


def _origin_allowed(request: Request) -> bool:
    origin = request.headers.get("origin")
    # Sin Origin se rechaza: los navegadores lo envían en toda mutación same-origin.
    return origin is not None and hmac.compare_digest(origin, settings.allowed_origin)


async def require_mutation(request: Request, session: CurrentSession) -> Session:
    """Sesión válida + Origin same-origin + `X-CSRF-Token` de esa sesión; si no, 401/403."""
    if request.method not in MUTATING_METHODS:
        return session
    cookie_token = request.cookies.get(COOKIE_NAME, "")
    if not _origin_allowed(request) or not sessions.csrf_matches(
        session, cookie_token, request.headers.get(CSRF_HEADER)
    ):
        raise csrf_invalid()
    return session


MutationSession = Annotated[Session, Depends(require_mutation)]

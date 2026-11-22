"""Integraciones de Google: estado (A1.8) y conexión incremental de Calendar (J3.3).

`GET /integrations/status` solo expone metadatos: estado, scopes concedidos, calendario
destino y frescura. Nunca tokens ni su forma cifrada.

`POST /integrations/google/connect|reconnect` inicia el consentimiento incremental y
responde `302` a Google. El callback (`/auth/google/callback`) distingue el modo con el
`state` firmado, intercambia el código y reconcilia el calendario «Maulwurf»; los tokens
solo se actualizan si Google confirma los scopes de Calendar y el ID token pertenece al
mismo `sub` de la sesión (ver `services/calendar_connection.py`).

D3.7 añade las variantes `GET` de connect/reconnect para que el navegador pueda navegar de
verdad: un `fetch` `POST` no puede seguir el `302` cross-origin a Google ni mostrar el
consentimiento, así que la UI enlaza a estas rutas. El `state` firmado sigue ligado al
`user_id` de la sesión y el callback exige consentimiento explícito y una sesión viva del
mismo usuario. La cookie es `mw_oauth_calendar` o `mw_oauth_gmail`, no la del login. Un
`Sec-Fetch-Site: cross-site` se rechaza con 403; `same-origin`, `same-site`, `none` y la
ausencia de cabecera (clientes no navegador) pueden iniciar el flujo. Un fallo de arranque
vuelve a `/settings?calendar=error&reason=<razón cerrada>`.
"""

import time
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.core.config import settings
from app.core.deps import (
    CurrentSession,
    DbSession,
    MutationSession,
    OAuthFlow,
    resolve_authenticated_session,
    set_oauth_state_cookie,
)
from app.core.errors import (
    ApiError,
    auth_required,
    google_integration_unavailable,
    google_not_connected,
)
from app.core.redis import RedisDep
from app.core.security import sign_state
from app.models import CalendarSettings, GoogleCredential, User
from app.models import Session as SessionRow
from app.services import calendar_connection, oauth_google
from app.services.limits import enforce_integration_limit
from app.services.oauth_google import OAUTH_STATE_TTL_SECONDS

router = APIRouter(tags=["integrations"])

CALENDAR_CONNECT = "connect"
CALENDAR_RECONNECT = "reconnect"
GMAIL_CONNECT = "gmail_connect"
SETTINGS_PATH = "/settings"

# Closed vocabulary the Settings page renders (D3.7). Unknown codes fall back to `error`.
_NAVIGATION_REASONS = {
    "google_integration_unavailable": "not_configured",
    "google_not_connected": "not_connected",
    "rate_limited": "rate_limited",
    "capacity_unavailable": "capacity_unavailable",
    "csrf_invalid": "csrf_invalid",
}


def _calendar_error_redirect(reason: str) -> RedirectResponse:
    redirect = RedirectResponse(
        f"{SETTINGS_PATH}?calendar=error&reason={reason}", status_code=302
    )
    redirect.headers["Cache-Control"] = "no-store"
    return redirect


def _gmail_error_redirect(reason: str) -> RedirectResponse:
    redirect = RedirectResponse(
        f"{SETTINGS_PATH}?gmail=error&reason={reason}", status_code=302
    )
    redirect.headers["Cache-Control"] = "no-store"
    return redirect


def _login_redirect() -> RedirectResponse:
    redirect = RedirectResponse("/login?error=session_expired", status_code=302)
    redirect.headers["Cache-Control"] = "no-store"
    return redirect


async def _navigation_session(request: Request, db: DbSession) -> SessionRow | None:
    """Session for a GET navigation route, or `None` when it is missing or expired.

    A browser navigating here must land on the login page, not on a JSON `401` body.
    """
    try:
        return await resolve_authenticated_session(db, request)
    except ApiError as exc:
        if exc.code == "auth_required":
            return None
        raise


NavigationSession = Annotated[SessionRow | None, Depends(_navigation_session)]


async def _integration_rate_limit(session: MutationSession, redis: RedisDep) -> None:
    """A4.6: incremental-consent abuse guard per authenticated user (unset = no limit)."""
    await enforce_integration_limit(redis, session.user_id)


IntegrationRateLimit = Annotated[None, Depends(_integration_rate_limit)]


class GoogleStatus(BaseModel):
    status: Literal["connected", "disconnected"]
    scopes: list[str]
    # Remote ID of the app-created secondary calendar (M5); NULL until it is reconciled.
    calendar_id: str | None
    # A3.4: the exact condition the confirm endpoint uses to choose pending vs blocked.
    calendar_write_ready: bool
    token_expires_at: datetime | None
    last_refresh_at: datetime | None


class IntegrationsStatus(BaseModel):
    google: GoogleStatus


@router.get("/integrations/status")
async def integrations_status(
    response: Response, session: CurrentSession, db: DbSession
) -> IntegrationsStatus:
    # Estado de conexión y scopes cambian con el tiempo: nunca servirlos desde caché.
    response.headers["Cache-Control"] = "no-store"
    credential = await db.get(GoogleCredential, session.user_id)
    calendar = await db.get(CalendarSettings, session.user_id)
    calendar_id = calendar.calendar_write_calendar_id if calendar is not None else None
    if credential is None:
        google = GoogleStatus(
            status="disconnected",
            scopes=[],
            calendar_id=calendar_id,
            calendar_write_ready=False,
            token_expires_at=None,
            last_refresh_at=None,
        )
    else:
        scopes = list(credential.scopes or [])
        google = GoogleStatus(
            status="connected" if credential.status == "connected" else "disconnected",
            scopes=scopes,
            calendar_id=calendar_id,
            calendar_write_ready=bool(
                credential.status == "connected"
                and calendar_id is not None
                and not oauth_google.missing_calendar_scopes(scopes)
            ),
            token_expires_at=credential.token_expires_at,
            last_refresh_at=credential.last_refresh_at,
        )
    return IntegrationsStatus(google=google)


def _build_incremental_redirect(
    user: User, *, scopes: Sequence[str], mode: str
) -> RedirectResponse:
    auth_req = oauth_google.build_incremental_authorization_request(
        scopes=scopes, login_hint=user.email
    )
    token = sign_state(
        {
            "state": auth_req.state,
            "nonce": auth_req.nonce,
            "code_verifier": auth_req.code_verifier,
            # Bind the grant to the tenant that started it: a callback answered by another
            # session (or another browser) must not attach the tokens to this user.
            "mode": mode,
            "user_id": str(user.id),
            "exp": int(time.time()) + OAUTH_STATE_TTL_SECONDS,
        }
    )
    redirect = RedirectResponse(auth_req.url, status_code=302)
    redirect.headers["Cache-Control"] = "no-store"
    flow: OAuthFlow = "gmail" if mode == GMAIL_CONNECT else "calendar"
    set_oauth_state_cookie(redirect, token, OAUTH_STATE_TTL_SECONDS, flow=flow)
    return redirect


async def _start_calendar_flow(
    db: DbSession, session: SessionRow, *, reconnect: bool
) -> RedirectResponse:
    """Build the incremental authorization URL and seal its state in the OAuth cookie."""
    if settings.google_client_id is None or settings.google_client_secret is None:
        raise google_integration_unavailable()
    user = await db.get(User, session.user_id)
    if user is None:
        raise auth_required()
    credential = await db.get(GoogleCredential, session.user_id)
    if reconnect and credential is None:
        raise google_not_connected()
    scopes = calendar_connection.requested_scopes(
        (credential.scopes or []) if credential is not None else [],
        reconnect=reconnect,
    )
    mode = CALENDAR_RECONNECT if reconnect else CALENDAR_CONNECT
    return _build_incremental_redirect(user, scopes=scopes, mode=mode)


async def _start_gmail_flow(db: DbSession, session: SessionRow) -> RedirectResponse:
    """Build the incremental OAuth URL requesting only `gmail.send`."""
    if settings.google_client_id is None or settings.google_client_secret is None:
        raise google_integration_unavailable()
    user = await db.get(User, session.user_id)
    if user is None:
        raise auth_required()
    return _build_incremental_redirect(
        user, scopes=(oauth_google.GMAIL_SEND_SCOPE,), mode=GMAIL_CONNECT
    )


# Browsers send one of these. A missing header is a non-browser client or a test.
_ALLOWED_FETCH_SITES = frozenset({"same-origin", "same-site", "none"})


def _cross_site_navigation(request: Request) -> bool:
    """True only when the browser says this navigation came from another site."""
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site is None or not fetch_site.strip():
        return False
    return fetch_site.strip().lower() not in _ALLOWED_FETCH_SITES


async def _start_oauth_navigation(
    request: Request,
    session: SessionRow,
    redis: RedisDep,
    starter: Callable[[], Awaitable[RedirectResponse]],
    error_redirect: Callable[[str], RedirectResponse],
) -> RedirectResponse:
    """Shared GET OAuth navigation guard for browser-initiated incremental consent."""
    # 403, not the settings redirect: a cross-site navigation must not start consent.
    if _cross_site_navigation(request):
        raise ApiError(403, "csrf_invalid", "Navegación de origen cruzado no permitida.")
    try:
        await enforce_integration_limit(redis, session.user_id)
        return await starter()
    except ApiError as exc:
        if exc.code == "auth_required":
            return _login_redirect()
        return error_redirect(_NAVIGATION_REASONS.get(exc.code, "error"))


async def _start_calendar_navigation(
    request: Request,
    db: DbSession,
    session: SessionRow,
    redis: RedisDep,
    *,
    reconnect: bool,
) -> RedirectResponse:
    """GET entry point for a real browser navigation (D3.7)."""
    return await _start_oauth_navigation(
        request,
        session,
        redis,
        lambda: _start_calendar_flow(db, session, reconnect=reconnect),
        _calendar_error_redirect,
    )


@router.get(
    "/integrations/google/connect",
    status_code=302,
    response_model=None,
    responses={
        302: {"description": "Browser navigation: Google consent or /settings on failure"},
    },
)
async def google_connect_navigation(
    request: Request, db: DbSession, session: NavigationSession, redis: RedisDep
) -> RedirectResponse:
    if session is None:
        return _login_redirect()
    return await _start_calendar_navigation(request, db, session, redis, reconnect=False)


@router.get(
    "/integrations/google/reconnect",
    status_code=302,
    response_model=None,
    responses={
        302: {"description": "Browser navigation: Google consent or /settings on failure"},
    },
)
async def google_reconnect_navigation(
    request: Request, db: DbSession, session: NavigationSession, redis: RedisDep
) -> RedirectResponse:
    if session is None:
        return _login_redirect()
    return await _start_calendar_navigation(request, db, session, redis, reconnect=True)


@router.post(
    "/integrations/google/connect",
    status_code=302,
    response_model=None,
    responses={
        429: {"description": "rate_limited when the configured consent limit is exceeded"},
        503: {"description": "capacity_unavailable: el limitador no está disponible"},
    },
)
async def google_connect(
    db: DbSession, session: MutationSession, _limit: IntegrationRateLimit
) -> RedirectResponse:
    return await _start_calendar_flow(db, session, reconnect=False)


@router.post(
    "/integrations/google/reconnect",
    status_code=302,
    response_model=None,
    responses={
        429: {"description": "rate_limited when the configured consent limit is exceeded"},
        503: {"description": "capacity_unavailable: el limitador no está disponible"},
    },
)
async def google_reconnect(
    db: DbSession, session: MutationSession, _limit: IntegrationRateLimit
) -> RedirectResponse:
    return await _start_calendar_flow(db, session, reconnect=True)


@router.get(
    "/integrations/google/gmail/connect",
    status_code=302,
    response_model=None,
    responses={
        302: {
            "description": (
                "Browser navigation: Google gmail.send consent or /settings on failure"
            )
        },
    },
)
async def google_gmail_connect_navigation(
    request: Request, db: DbSession, session: NavigationSession, redis: RedisDep
) -> RedirectResponse:
    if session is None:
        return _login_redirect()
    return await _start_oauth_navigation(
        request,
        session,
        redis,
        lambda: _start_gmail_flow(db, session),
        _gmail_error_redirect,
    )


@router.post(
    "/integrations/google/gmail/connect",
    status_code=302,
    response_model=None,
    responses={
        429: {"description": "rate_limited when the configured consent limit is exceeded"},
        503: {"description": "capacity_unavailable: el limitador no está disponible"},
    },
)
async def google_gmail_connect(
    db: DbSession, session: MutationSession, _limit: IntegrationRateLimit
) -> RedirectResponse:
    return await _start_gmail_flow(db, session)

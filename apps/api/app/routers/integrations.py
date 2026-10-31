"""Integraciones de Google: estado (A1.8) y conexión incremental de Calendar (J3.3).

`GET /integrations/status` solo expone metadatos: estado, scopes concedidos, calendario
destino y frescura. Nunca tokens ni su forma cifrada.

`POST /integrations/google/connect|reconnect` inicia el consentimiento incremental y
responde `302` a Google. El callback (`/auth/google/callback`) distingue el modo con el
`state` firmado, intercambia el código y reconcilia el calendario «Maulwurf»; los tokens
solo se actualizan si Google confirma los scopes de Calendar y el ID token pertenece al
mismo `sub` de la sesión (ver `services/calendar_connection.py`).
"""

import time
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.core.config import settings
from app.core.deps import (
    CurrentSession,
    DbSession,
    MutationSession,
    set_oauth_state_cookie,
)
from app.core.errors import auth_required, google_integration_unavailable, google_not_connected
from app.core.security import sign_state
from app.models import CalendarSettings, GoogleCredential, User
from app.services import calendar_connection, oauth_google
from app.services.oauth_google import OAUTH_STATE_TTL_SECONDS

router = APIRouter(tags=["integrations"])

CALENDAR_CONNECT = "connect"
CALENDAR_RECONNECT = "reconnect"


class GoogleStatus(BaseModel):
    status: Literal["connected", "disconnected"]
    scopes: list[str]
    # Remote ID of the app-created secondary calendar (M5); NULL until it is reconciled.
    calendar_id: str | None
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
            token_expires_at=None,
            last_refresh_at=None,
        )
    else:
        google = GoogleStatus(
            status="connected" if credential.status == "connected" else "disconnected",
            scopes=list(credential.scopes),
            calendar_id=calendar_id,
            token_expires_at=credential.token_expires_at,
            last_refresh_at=credential.last_refresh_at,
        )
    return IntegrationsStatus(google=google)


async def _start_calendar_flow(
    db: DbSession, session: MutationSession, *, reconnect: bool
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
        credential.scopes if credential is not None else [], reconnect=reconnect
    )
    request = oauth_google.build_incremental_authorization_request(
        scopes=scopes, login_hint=user.email
    )
    token = sign_state(
        {
            "state": request.state,
            "nonce": request.nonce,
            "code_verifier": request.code_verifier,
            "mode": CALENDAR_RECONNECT if reconnect else CALENDAR_CONNECT,
            # Bind the grant to the tenant that started it: a callback answered by another
            # session (or another browser) must not attach the tokens to this user.
            "user_id": str(user.id),
            "exp": int(time.time()) + OAUTH_STATE_TTL_SECONDS,
        }
    )
    redirect = RedirectResponse(request.url, status_code=302)
    redirect.headers["Cache-Control"] = "no-store"
    set_oauth_state_cookie(redirect, token, OAUTH_STATE_TTL_SECONDS)
    return redirect


@router.post("/integrations/google/connect", response_model=None)
async def google_connect(db: DbSession, session: MutationSession) -> RedirectResponse:
    return await _start_calendar_flow(db, session, reconnect=False)


@router.post("/integrations/google/reconnect", response_model=None)
async def google_reconnect(db: DbSession, session: MutationSession) -> RedirectResponse:
    return await _start_calendar_flow(db, session, reconnect=True)

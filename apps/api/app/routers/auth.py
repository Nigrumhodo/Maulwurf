"""Rutas de autenticación: login Google (A1.3), logout (A1.8) y conexión de Calendar (J3.3).

El login se resuelve en dos saltos:
1. `GET /auth/google/start` arma la URL de Google y firma el material efímero
   (`state`, `nonce`, `code_verifier`) en la cookie `mw_oauth`.
2. `GET /auth/google/callback` verifica el `state`, intercambia el código, valida el ID
   token y emite la sesión opaca `mw_session`, luego redirige al dashboard (`/`).

J3.3 reutiliza el mismo callback para el consentimiento incremental de Calendar. El modo
(`connect`/`reconnect`) y el `user_id` que inició el flujo viajan dentro del `state`
firmado; el callback exige una sesión viva del MISMO usuario, comprueba que el `sub` del ID
token sea el de la credencial, y solo entonces actualiza los tokens, reconcilia el
calendario «Maulwurf» y reanuda las tareas bloqueadas. Un fallo nunca deja tokens a medias.

Cualquier fallo de login redirige a `/login?error=<razón>`; los fallos de Calendar vuelven a
`/settings?calendar=error&reason=<razón>`. Nunca se devuelven tokens al navegador. Las
cookies se fijan sobre la `RedirectResponse` devuelta (no sobre el `Response` inyectado),
que es el objeto que FastAPI envía de vuelta.

Login y Calendar comparten la cookie `mw_oauth`: iniciar cualquiera de los dos flujos
invalida el material efímero del otro en el mismo navegador, así que solo uno puede estar en
vuelo a la vez.
"""
import hmac
import logging
import time
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import RedirectResponse

from app.core.deps import (
    COOKIE_NAME,
    OAUTH_COOKIE_NAME,
    AnyMutationSession,
    DbSession,
    clear_oauth_state_cookie,
    clear_session_cookie,
    set_oauth_state_cookie,
    set_session_cookie,
)
from app.core.security import sign_state, verify_state
from app.models import GoogleCredential, User
from app.services import calendar_connection, calendar_resume, oauth_google, sessions
from app.services.google_calendar import (
    GoogleCalendarError,
    GoogleCalendarInvalidGrant,
    disconnect_google_credential,
)
from app.services.oauth_google import OAUTH_STATE_TTL_SECONDS, OAuthError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])

CALENDAR_CONNECT = "connect"
CALENDAR_RECONNECT = "reconnect"
CALENDAR_MODES = frozenset({CALENDAR_CONNECT, CALENDAR_RECONNECT})
SETTINGS_PATH = "/settings"


def _constant_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(
        a.encode("utf-8", "surrogatepass"), b.encode("utf-8", "surrogatepass")
    )


def _login_error(reason: str) -> RedirectResponse:
    redirect = RedirectResponse(f"/login?error={reason}", status_code=302)
    clear_oauth_state_cookie(redirect)
    return redirect


def _calendar_result(result: str, *, reason: str | None = None) -> RedirectResponse:
    query = f"calendar={result}"
    if reason is not None:
        query = f"{query}&reason={reason}"
    redirect = RedirectResponse(f"{SETTINGS_PATH}?{query}", status_code=302)
    redirect.headers["Cache-Control"] = "no-store"
    clear_oauth_state_cookie(redirect)
    return redirect


@router.post("/auth/logout", status_code=204, response_model=None)
async def logout(response: Response, session: AnyMutationSession, db: DbSession) -> Response:
    # Revocación inmediata: la cookie deja de valer en la siguiente petición. Un segundo
    # logout con esa cookie ya no tiene sesión y recibe 401 (fail closed). A4.4: the
    # restricted deletion session may use this same logout to end itself.
    await sessions.revoke_session(db, session)
    await db.commit()
    response.status_code = 204
    clear_session_cookie(response)
    return response


@router.get("/auth/google/start", response_model=None)
async def google_start() -> RedirectResponse:
    try:
        request = oauth_google.build_authorization_request()
    except OAuthError:
        return _login_error("oauth_not_configured")

    token = sign_state(
        {
            "state": request.state,
            "nonce": request.nonce,
            "code_verifier": request.code_verifier,
            "exp": int(time.time()) + OAUTH_STATE_TTL_SECONDS,
        }
    )
    redirect = RedirectResponse(request.url, status_code=302)
    set_oauth_state_cookie(redirect, token, OAUTH_STATE_TTL_SECONDS)
    return redirect


async def _calendar_callback(
    request: Request,
    db: DbSession,
    payload: dict[str, object],
    *,
    error: str | None,
    code: str | None,
    state: str | None,
) -> RedirectResponse:
    """Complete an incremental Calendar grant started by `POST /integrations/google/*`."""
    expected_state = payload.get("state")
    nonce = payload.get("nonce")
    code_verifier = payload.get("code_verifier")
    bound_user = payload.get("user_id")
    mode = payload.get("mode")
    # Validate the signed state before honoring even an error callback: an unsigned bounce
    # must not drive the Calendar redirect or the displayed reason.
    if (
        not isinstance(expected_state, str)
        or not isinstance(nonce, str)
        or not isinstance(code_verifier, str)
        or not isinstance(bound_user, str)
        or not isinstance(state, str)
        or mode not in CALENDAR_MODES
        or not _constant_eq(state, expected_state)
    ):
        return _calendar_result("error", reason="invalid_state")
    if error is not None:
        return _calendar_result("error", reason="access_denied")
    if code is None:
        return _calendar_result("error", reason="missing_params")

    session = await sessions.resolve_session(db, request.cookies.get(COOKIE_NAME))
    if session is None:
        return _login_error("session_expired")
    # Flat copy: a rollback below expires ORM attributes and reading them outside an await
    # would raise MissingGreenlet.
    user_id = session.user_id
    if bound_user != str(user_id):
        # The consent was started by another user/session: never attach its grant here.
        await db.rollback()
        return _calendar_result("error", reason="state_mismatch")

    try:
        tokens = await oauth_google.exchange_code(code, code_verifier)
        id_token = tokens.get("id_token")
        if not isinstance(id_token, str):
            raise OAuthError("token_exchange_incomplete")
        claims = await oauth_google.verify_id_token(id_token, nonce)
    except OAuthError:
        await db.rollback()
        return _calendar_result("error", reason="oauth_failed")

    user = await db.get(User, user_id)
    if user is None or user.status != "active":
        await db.rollback()
        return _login_error("session_expired")
    if claims.get("sub") != user.google_sub:
        # Incremental consent for another Google account must not be linked to this user.
        await db.rollback()
        return _calendar_result("error", reason="wrong_account")
    if not calendar_connection.grant_covers_calendar(tokens):
        # The user declined (or Google withheld) part of the Calendar scopes: reject cleanly
        # and persist nothing. Library, upload and chat keep working without Calendar.
        await db.rollback()
        return _calendar_result("error", reason="scope_required")
    access_token = tokens.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        await db.rollback()
        return _calendar_result("error", reason="oauth_failed")

    credential = await db.get(GoogleCredential, user_id)
    requested = calendar_connection.requested_scopes(
        (credential.scopes or []) if credential is not None else [],
        reconnect=mode == CALENDAR_RECONNECT,
    )

    async def disconnect() -> None:
        await disconnect_google_credential(db, user_id)

    try:
        await oauth_google.apply_incremental_grant(
            db, user, tokens, requested_scopes=requested
        )
        await calendar_connection.connect_calendar(
            db,
            user=user,
            access_token=access_token,
            on_invalid_grant=disconnect,
        )
        if mode == CALENDAR_RECONNECT:
            # Only a reconnect reactivates waiting work (DISENO §3.8/A3.7): current-version
            # blocked tasks go back to pending with one outbox event each.
            await calendar_resume.resume_blocked_tasks(db, user_id=user_id)
    except GoogleCalendarInvalidGrant:
        # The grant Google just issued cannot be used: keep the integration disconnected
        # in its own transaction instead of rolling the revocation back with the rest.
        await db.rollback()
        await disconnect_google_credential(db, user_id)
        await db.commit()
        return _calendar_result("error", reason="invalid_grant")
    except GoogleCalendarError as exc:
        # Type-only logging: no calendar IDs, payloads or tokens.
        logger.warning(
            "calendar.connect_failed code=%s transient=%s", exc.code, exc.transient
        )
        await db.rollback()
        reason = "calendar_unavailable" if exc.transient else "calendar_error"
        return _calendar_result("error", reason=reason)

    await db.commit()
    return _calendar_result("connected")


@router.get("/auth/google/callback", response_model=None)
async def google_callback(
    request: Request,
    db: DbSession,
    error: Annotated[str | None, Query()] = None,
    code: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
) -> RedirectResponse:
    cookie = request.cookies.get(OAUTH_COOKIE_NAME)
    payload = verify_state(cookie, OAUTH_STATE_TTL_SECONDS) if cookie else None
    mode = payload.get("mode") if payload is not None else None
    if mode in CALENDAR_MODES:
        assert payload is not None  # noqa: S101 - `mode` can only come from the payload
        return await _calendar_callback(
            request, db, payload, error=error, code=code, state=state
        )
    if error is not None:
        return _login_error("access_denied")
    if code is None or state is None:
        return _login_error("missing_params")

    expected_state = payload.get("state") if payload is not None else None
    nonce = payload.get("nonce") if payload is not None else None
    code_verifier = payload.get("code_verifier") if payload is not None else None
    if not isinstance(expected_state, str) or not _constant_eq(state, expected_state):
        return _login_error("invalid_state")
    if not isinstance(nonce, str) or not isinstance(code_verifier, str):
        return _login_error("invalid_state")

    try:
        tokens = await oauth_google.exchange_code(code, code_verifier)
        id_token = tokens.get("id_token")
        if not isinstance(id_token, str):
            raise OAuthError("token_exchange_incomplete")
        claims = await oauth_google.verify_id_token(id_token, nonce)
        # Session fixation: a session the browser already carried never survives a login.
        # It is revoked before the identity upsert so the old tenant is still the one bound
        # by RLS during the revocation.
        previous = await sessions.resolve_session(db, request.cookies.get(COOKIE_NAME))
        if previous is not None:
            await sessions.revoke_session(db, previous)
        user = await oauth_google.upsert_google_identity(db, claims, tokens)
        issued = await sessions.create_session(db, user.id)
    except OAuthError:
        await db.rollback()
        return _login_error("oauth_failed")

    await db.commit()
    redirect = RedirectResponse("/", status_code=302)
    set_session_cookie(redirect, issued.cookie_token)
    clear_oauth_state_cookie(redirect)
    return redirect

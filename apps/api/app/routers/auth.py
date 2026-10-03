"""Rutas de autenticación: login Google (A1.3) y logout (A1.8).

El login se resuelve en dos saltos:
1. `GET /auth/google/start` arma la URL de Google y firma el material efímero
   (`state`, `nonce`, `code_verifier`) en la cookie `mw_oauth`.
2. `GET /auth/google/callback` verifica el `state`, intercambia el código, valida el ID
   token y emite la sesión opaca `mw_session`, luego redirige al dashboard (`/`).

Cualquier fallo redirige a `/login?error=<razón>`; nunca se devuelven tokens al navegador.
Las cookies se fijan sobre la `RedirectResponse` devuelta (no sobre el `Response` inyectado),
que es el objeto que FastAPI envía de vuelta.
"""
import hmac
import time
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import RedirectResponse

from app.core.deps import (
    OAUTH_COOKIE_NAME,
    DbSession,
    MutationSession,
    clear_oauth_state_cookie,
    clear_session_cookie,
    set_oauth_state_cookie,
    set_session_cookie,
)
from app.core.security import sign_state, verify_state
from app.services import oauth_google, sessions
from app.services.oauth_google import OAUTH_STATE_TTL_SECONDS, OAuthError

router = APIRouter(tags=["auth"])


def _constant_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(
        a.encode("utf-8", "surrogatepass"), b.encode("utf-8", "surrogatepass")
    )


def _login_error(reason: str) -> RedirectResponse:
    redirect = RedirectResponse(f"/login?error={reason}", status_code=302)
    clear_oauth_state_cookie(redirect)
    return redirect


@router.post("/auth/logout", status_code=204, response_model=None)
async def logout(response: Response, session: MutationSession, db: DbSession) -> Response:
    # Revocación inmediata: la cookie deja de valer en la siguiente petición. Un segundo
    # logout con esa cookie ya no tiene sesión y recibe 401 (fail closed).
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


@router.get("/auth/google/callback", response_model=None)
async def google_callback(
    request: Request,
    db: DbSession,
    error: Annotated[str | None, Query()] = None,
    code: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
) -> RedirectResponse:
    if error is not None:
        return _login_error("access_denied")
    if code is None or state is None:
        return _login_error("missing_params")

    cookie = request.cookies.get(OAUTH_COOKIE_NAME)
    payload = verify_state(cookie, OAUTH_STATE_TTL_SECONDS) if cookie else None
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
        user = await oauth_google.upsert_google_identity(db, claims, tokens)
        issued = await sessions.create_session(db, user.id)
    except OAuthError:
        return _login_error("oauth_failed")

    await db.commit()
    redirect = RedirectResponse("/", status_code=302)
    set_session_cookie(redirect, issued.cookie_token)
    clear_oauth_state_cookie(redirect)
    return redirect

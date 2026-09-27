"""Rutas de autenticación (A1.8). El login con Google (`/auth/google/*`) llega en A1.3."""
from fastapi import APIRouter, Response

from app.core.deps import DbSession, MutationSession, clear_session_cookie
from app.services.sessions import revoke_session

router = APIRouter(tags=["auth"])


@router.post("/auth/logout", status_code=204, response_model=None)
async def logout(response: Response, session: MutationSession, db: DbSession) -> Response:
    # Revocación inmediata: la cookie deja de valer en la siguiente petición. Un segundo
    # logout con esa cookie ya no tiene sesión y recibe 401 (fail closed).
    await revoke_session(db, session)
    await db.commit()
    response.status_code = 204
    clear_session_cookie(response)
    return response

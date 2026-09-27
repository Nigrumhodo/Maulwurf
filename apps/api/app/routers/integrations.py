"""`GET /integrations/status` (A1.8): estado de Google para la UI (D1.3/D3.7).

Solo metadatos: estado, scopes concedidos y frescura. Nunca tokens ni su forma cifrada.
"""
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel

from app.core.deps import CurrentSession, DbSession
from app.models import GoogleCredential

router = APIRouter(tags=["integrations"])


class GoogleStatus(BaseModel):
    status: Literal["connected", "disconnected"]
    scopes: list[str]
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
    if credential is None:
        google = GoogleStatus(
            status="disconnected", scopes=[], token_expires_at=None, last_refresh_at=None
        )
    else:
        google = GoogleStatus(
            status="connected" if credential.status == "connected" else "disconnected",
            scopes=list(credential.scopes),
            token_expires_at=credential.token_expires_at,
            last_refresh_at=credential.last_refresh_at,
        )
    return IntegrationsStatus(google=google)

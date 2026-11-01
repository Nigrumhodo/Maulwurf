"""Perfil del usuario (A1.4/A1.8) y borrado de cuenta observable (A4.4).

`GET /me` nunca es cacheable. `PATCH /me` solo cambia la zona horaria IANA del perfil, que
afecta a digests futuros y no a las fechas de clases ya registradas (snapshot en `audios`).
Settings de Calendar llegan en S3 y preferencias Gmail/digests en S4.

A4.4 adds `DELETE /me` and `GET /me/deletion/{id}`. The first call applies the tombstone,
revokes the other sessions and runs the operation; the initiating session enters restricted
mode, so from then on only the deletion status and logout are reachable with its cookie.
"""
import uuid
from datetime import datetime
from typing import Annotated, Literal, cast

import httpx
from fastapi import APIRouter, Body, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.deps import (
    COOKIE_NAME,
    CurrentSession,
    DbSession,
    DeletionSession,
    MutationSession,
)
from app.core.errors import auth_required
from app.core.validation import is_iana_timezone
from app.models import AccountDeletion, User
from app.models import Session as SessionRow
from app.services import deletion
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


class AccountDeletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Omitted means the conservative choice: Maulwurf-created Google events are kept.
    delete_maulwurf_events: bool = False


class AccountDeletionAccepted(BaseModel):
    deletion_operation_id: str


class AccountDeletionStatus(BaseModel):
    id: str
    status: Literal["pending", "completed", "completed_with_remote_failures"]
    delete_maulwurf_events: bool
    created_at: datetime
    completed_at: datetime | None
    expires_at: datetime
    error_summary: str | None


def deletion_google_transport() -> httpx.BaseTransport | None:
    """Test/deployment seam for the blocking Calendar client; production uses the network."""
    return None


DeletionGoogleTransport = Annotated[
    httpx.BaseTransport | None, Depends(deletion_google_transport)
]


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


@router.delete(
    "/me",
    status_code=202,
    response_model=AccountDeletionAccepted,
    responses={
        202: {"description": "Borrado aceptado; la operación es observable e idempotente"},
        401: {"description": "Sin sesión (auth_required)"},
        403: {"description": "CSRF u Origin inválido (csrf_invalid)"},
        422: {"description": "validation_failed: campo desconocido"},
    },
)
async def delete_me(
    response: Response,
    session: MutationSession,
    db: DbSession,
    transport: DeletionGoogleTransport,
    body: Annotated[AccountDeletionRequest | None, Body()] = None,
) -> AccountDeletionAccepted:
    """Start account deletion (A4.4): tombstone, operation, then purge.

    The tombstone transaction is committed before the remote work, so a provider failure
    cannot leave the account active. The restricted session returned here can only read
    `GET /me/deletion/{id}` and log out; every other session was revoked.
    """
    request_body = body or AccountDeletionRequest()
    operation = await deletion.create_account_deletion(
        db,
        user_id=session.user_id,
        session_id=session.id,
        delete_events=request_body.delete_maulwurf_events,
    )
    deletion_id = operation.id
    await db.commit()
    await deletion.execute_account_deletion(db, deletion_id, transport=transport)
    response.headers["Cache-Control"] = "no-store"
    return AccountDeletionAccepted(deletion_operation_id=str(deletion_id))


def _status(operation: AccountDeletion) -> AccountDeletionStatus:
    return AccountDeletionStatus(
        id=str(operation.id),
        status=cast(
            Literal["pending", "completed", "completed_with_remote_failures"],
            operation.status,
        ),
        delete_maulwurf_events=operation.delete_events,
        created_at=operation.created_at,
        completed_at=operation.completed_at,
        expires_at=operation.expires_at,
        error_summary=operation.error_summary,
    )


@router.get(
    "/me/deletion/{deletion_id}",
    response_model=AccountDeletionStatus,
    responses={
        200: {"description": "Estado pendiente, completado o completado con fallos remotos"},
        401: {"description": "Sin sesión o sesión restringida de otra operación"},
        404: {"description": "Operación inexistente, ajena o expirada (not_found)"},
    },
)
async def get_deletion_status(
    deletion_id: uuid.UUID,
    response: Response,
    session: DeletionSession,
    db: DbSession,
) -> AccountDeletionStatus:
    operation = await deletion.get_account_deletion(
        db,
        deletion_id=deletion_id,
        user_id=session.user_id,
        session_deletion_id=session.deletion_operation_id,
    )
    response.headers["Cache-Control"] = "no-store"
    return _status(operation)

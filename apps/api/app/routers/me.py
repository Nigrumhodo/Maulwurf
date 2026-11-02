"""Perfil del usuario (A1.4/A1.8) y borrado de cuenta observable (A4.4).

`GET /me` nunca es cacheable. `PATCH /me` solo cambia la zona horaria IANA del perfil, que
afecta a digests futuros y no a las fechas de clases ya registradas (snapshot en `audios`).
Settings de Calendar llegan en S3 y preferencias Gmail/digests en S4.

A4.4 adds `DELETE /me` and `GET /me/deletion/{id}`. The first call applies the tombstone,
revokes the other sessions and starts the operation; the initiating session enters restricted
mode, so from then on only the deletion status, the authorized resume and logout are reachable
with its cookie. `POST /me/deletion/{id}/resume` retries a `pending`/`running` operation with
the same CSRF/Origin checks.
"""
import logging
import uuid
from datetime import datetime
from typing import Annotated, Literal, cast

import httpx
from fastapi import APIRouter, Body, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.deps import (
    COOKIE_NAME,
    AnyMutationSession,
    CurrentSession,
    DbSession,
    DeletionSession,
    MutationSession,
)
from app.core.errors import auth_required
from app.core.validation import is_iana_timezone
from app.models import AccountDeletion, CalendarSettings, User
from app.models import Session as SessionRow
from app.schemas.notifications import (
    NotificationPreferencesOut,
    NotificationPreferencesPatch,
    format_digest_time,
)
from app.services import deletion, notification_center
from app.services.sessions import csrf_token_for

logger = logging.getLogger(__name__)

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
    status: Literal[
        "pending", "running", "completed", "completed_with_remote_failures"
    ]
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


def _preferences_out(row: CalendarSettings | None) -> NotificationPreferencesOut:
    """Complete M8 preference set; an absent row means the opted-out defaults."""
    if row is None:
        return NotificationPreferencesOut(
            gmail_opt_in=False,
            digest_daily_enabled=False,
            digest_daily_time="07:00",
            digest_weekly_enabled=False,
            digest_weekday=0,
            reminder_offsets_hours=[48, 24, 2],
            reminder_allday_offsets_days=[3, 1],
            updated_at=None,
        )
    return NotificationPreferencesOut(
        gmail_opt_in=row.gmail_opt_in,
        digest_daily_enabled=row.digest_daily_enabled,
        digest_daily_time=format_digest_time(row.digest_daily_time),
        digest_weekly_enabled=row.digest_weekly_enabled,
        digest_weekday=row.digest_weekday,
        reminder_offsets_hours=list(row.reminder_offsets_hours),
        reminder_allday_offsets_days=list(row.reminder_allday_offsets_days),
        updated_at=row.updated_at,
    )


@router.get(
    "/me/notifications",
    responses={
        200: {"description": "Preferencias M8 completas del usuario"},
        401: {"description": "Sin sesión (auth_required)"},
    },
)
async def get_notification_preferences(
    response: Response, session: CurrentSession, db: DbSession
) -> NotificationPreferencesOut:
    """Read the current preferences without creating the settings row."""
    response.headers["Cache-Control"] = "no-store"
    row = await notification_center.get_preferences(db, session.user_id)
    return _preferences_out(row)


@router.patch(
    "/me/notifications",
    responses={
        200: {"description": "Preferencias M8 completas tras la actualización parcial"},
        401: {"description": "Sin sesión (auth_required)"},
        403: {"description": "Origin/CSRF inválidos (csrf_invalid)"},
        422: {"description": "validation_failed: preferencia inválida"},
    },
)
async def update_notification_preferences(
    body: NotificationPreferencesPatch,
    response: Response,
    session: MutationSession,
    db: DbSession,
) -> NotificationPreferencesOut:
    """Apply only the sent fields; Gmail remains opt-in and no scope is granted here."""
    response.headers["Cache-Control"] = "no-store"
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        row = await notification_center.get_preferences(db, session.user_id)
        return _preferences_out(row)
    row = await notification_center.apply_preferences(db, session.user_id, changes)
    out = _preferences_out(row)
    await db.commit()
    return out


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
    cannot leave the account active. Once the tombstone is durable the contract is `202
    accepted`: an exception in the purge is logged by operation id and the operation stays
    resumable through `POST /me/deletion/{id}/resume`. The remote phase runs inline (see the
    PR note on latency); a retry never repeats an already-succeeded Google deletion.
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
    try:
        await deletion.execute_account_deletion(db, deletion_id, transport=transport)
    except Exception:
        # Why: the tombstone is already durable, so returning 500 would misstate the
        # contract and leave the client without the operation id. Log only the id: an
        # exception message may contain profile or provider payloads.
        logger.error("account_deletion.left_pending deletion_id=%s", deletion_id)
        await db.rollback()
    response.headers["Cache-Control"] = "no-store"
    return AccountDeletionAccepted(deletion_operation_id=str(deletion_id))


def _status(operation: AccountDeletion) -> AccountDeletionStatus:
    return AccountDeletionStatus(
        id=str(operation.id),
        status=cast(
            Literal[
                "pending", "running", "completed", "completed_with_remote_failures"
            ],
            operation.status,
        ),
        delete_maulwurf_events=operation.delete_events,
        created_at=operation.created_at,
        completed_at=operation.completed_at,
        expires_at=operation.expires_at,
        error_summary=operation.error_summary,
    )


@router.post(
    "/me/deletion/{deletion_id}/resume",
    status_code=202,
    response_model=AccountDeletionStatus,
    responses={
        200: {"description": "La operación ya estaba cerrada; reintento idempotente"},
        202: {"description": "Reanudada; la operación sigue pendiente o en ejecución"},
        401: {"description": "Sin sesión o sesión restringida de otra operación"},
        403: {"description": "CSRF u Origin inválido (csrf_invalid)"},
        404: {"description": "Operación inexistente, ajena o expirada (not_found)"},
    },
)
async def resume_deletion(
    deletion_id: uuid.UUID,
    response: Response,
    session: AnyMutationSession,
    db: DbSession,
    transport: DeletionGoogleTransport,
) -> AccountDeletionStatus:
    """Resume a deletion whose execution failed after the tombstone.

    The restricted initiator session is the only credential the account still has, so it is
    allowed here (with the usual Origin/CSRF check) while every other route rejects it. The
    operation must belong to that user and, for a restricted session, be exactly its own
    operation. A completed operation is a no-op that returns its final status.
    """
    operation = await deletion.get_account_deletion(
        db,
        deletion_id=deletion_id,
        user_id=session.user_id,
        session_deletion_id=session.deletion_operation_id,
    )
    if operation.status in {"pending", "running"}:
        # Capture plain values before executing: the claim commits and may expire the ORM
        # session object, and the re-read must not reload it outside an await.
        user_id = session.user_id
        session_deletion_id = session.deletion_operation_id
        try:
            await deletion.execute_account_deletion(db, deletion_id, transport=transport)
        except Exception:
            logger.error("account_deletion.resume_left_pending deletion_id=%s", deletion_id)
            await db.rollback()
        operation = await deletion.get_account_deletion(
            db,
            deletion_id=deletion_id,
            user_id=user_id,
            session_deletion_id=session_deletion_id,
        )
    response.headers["Cache-Control"] = "no-store"
    response.status_code = (
        status.HTTP_202_ACCEPTED
        if operation.status in {"pending", "running"}
        else status.HTTP_200_OK
    )
    return _status(operation)


@router.get(
    "/me/deletion/{deletion_id}",
    response_model=AccountDeletionStatus,
    responses={
        200: {
            "description": (
                "Estado pendiente, en ejecución, completado o completado con fallos remotos"
            )
        },
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

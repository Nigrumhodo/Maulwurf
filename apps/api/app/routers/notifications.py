"""M8 delivery surface (A4.2): test send, cursor listing and warned resend.

`GET /me/notifications` and `PATCH /me/notifications` live in `routers/me.py` next to the
profile they belong to. This router owns the delivery panel:

- `GET /notifications` never mixes tenants and returns only identity, status, timing and a
  redacted error code;
- `POST /notifications/test` requires the Gmail opt-in and a verified recipient, then records
  an audited occurrence for the user's verified address (J4.1 owns the real send);
- `POST /notifications/{id}/resend` requires `acknowledge_duplicate_risk=true`, creates a
  **new** occurrence and leaves the original uncertainty untouched.

Gmail offers no exactly-once delivery, so `delivery_unknown` is terminal and nothing here
retries it automatically. No recipient, message body or provider payload is exposed.
"""

import uuid
from datetime import datetime
from typing import Annotated, cast

from fastapi import APIRouter, Query, Response, status
from pydantic import BaseModel, ConfigDict

from app.core.deps import CurrentSession, DbSession, MutationSession
from app.core.errors import (
    acknowledgement_required,
    auth_required,
    not_found,
    notification_not_resendable,
    notifications_test_unavailable,
)
from app.models import Notification, User
from app.schemas.notifications import (
    NotificationListOut,
    NotificationOut,
    NotificationStatus,
    NotificationType,
    NotificationWarningOut,
    NotificationWithWarningOut,
)
from app.services import notification_center
from app.services.notifications import NotificationNotResendable

router = APIRouter(tags=["notifications"])


class ResendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acknowledge_duplicate_risk: bool


def _out(row: Notification) -> NotificationOut:
    return NotificationOut(
        id=str(row.id),
        type=cast(NotificationType, row.type),
        status=cast(NotificationStatus, row.status),
        task_id=None if row.task_id is None else str(row.task_id),
        occurrence=row.occurrence,
        due_version=row.due_version,
        occurrence_seq=row.occurrence_seq,
        scheduled_at=row.scheduled_at,
        attempts=row.attempts,
        sent_at=row.sent_at,
        last_error_code=row.last_error_code,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get(
    "/notifications",
    responses={
        200: {"description": "Página de entregas del usuario, más recientes primero"},
        401: {"description": "Sin sesión (auth_required)"},
        422: {"description": "validation_failed: filtros o cursor inválidos"},
    },
)
async def list_notifications(
    session: CurrentSession,
    db: DbSession,
    response: Response,
    statuses: Annotated[list[NotificationStatus] | None, Query(alias="status")] = None,
    start: Annotated[datetime | None, Query(alias="from")] = None,
    end: Annotated[datetime | None, Query(alias="to")] = None,
    limit: Annotated[
        int, Query(ge=1, le=notification_center.MAX_PAGE_SIZE)
    ] = notification_center.DEFAULT_PAGE_SIZE,
    cursor: Annotated[
        str | None, Query(max_length=notification_center.MAX_CURSOR_LENGTH)
    ] = None,
) -> NotificationListOut:
    """List the user's deliveries; `scheduled_at` is the half-open window field."""
    response.headers["Cache-Control"] = "no-store"
    notification_center.validate_window(start, end)
    rows, next_cursor = await notification_center.page_notifications(
        db,
        session.user_id,
        statuses=statuses,
        start=start,
        end=end,
        limit=limit,
        cursor=cursor,
    )
    return NotificationListOut(
        items=[_out(row) for row in rows],
        next_cursor=next_cursor,
    )


@router.post(
    "/notifications/test",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        202: {"description": "Ocurrencia de prueba registrada con aviso de entrega incierta"},
        401: {"description": "Sin sesión (auth_required)"},
        403: {"description": "Origin/CSRF inválidos (csrf_invalid)"},
        409: {"description": "notifications_test_unavailable: sin opt-in o email no verificado"},
    },
)
async def send_test_notification(
    session: MutationSession, db: DbSession, response: Response
) -> NotificationWithWarningOut:
    """Record the audited test occurrence; the recipient is always the verified email."""
    response.headers["Cache-Control"] = "no-store"
    user = await db.get(User, session.user_id)
    if user is None:
        raise auth_required()
    preferences = await notification_center.get_preferences(db, session.user_id)
    if preferences is None or not preferences.gmail_opt_in:
        raise notifications_test_unavailable("gmail_not_opted_in")
    if not user.email_verified:
        raise notifications_test_unavailable("recipient_unverified")
    row = await notification_center.create_test_occurrence(db, user)
    out = _out(row)
    await db.commit()
    return NotificationWithWarningOut(
        **out.model_dump(),
        warning=NotificationWarningOut(
            code="delivery_uncertain",
            message=(
                "Gmail no garantiza exactamente un envío; la entrega puede ser incierta y "
                "no se reintenta automáticamente."
            ),
        ),
    )


@router.post(
    "/notifications/{notification_id}/resend",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        202: {"description": "Nueva ocurrencia auditada creada; la original no se modifica"},
        401: {"description": "Sin sesión (auth_required)"},
        403: {"description": "Origin/CSRF inválidos (csrf_invalid)"},
        404: {"description": "Entrega inexistente o ajena (not_found)"},
        409: {"description": "notification_not_resendable: estado no repetible"},
        422: {"description": "acknowledgement_required: falta aceptar el riesgo de duplicado"},
    },
)
async def resend_notification(
    notification_id: uuid.UUID,
    body: ResendRequest,
    session: MutationSession,
    db: DbSession,
    response: Response,
) -> NotificationWithWarningOut:
    """Create a new audited occurrence after the duplicate risk is acknowledged."""
    response.headers["Cache-Control"] = "no-store"
    if not body.acknowledge_duplicate_risk:
        raise acknowledgement_required()
    try:
        row = await notification_center.resend_notification(
            db, session.user_id, notification_id
        )
    except NotificationNotResendable as exc:
        raise notification_not_resendable(exc.status) from exc
    if row is None:
        raise not_found()
    out = _out(row)
    await db.commit()
    return NotificationWithWarningOut(
        **out.model_dump(),
        warning=NotificationWarningOut(
            code="duplicate_risk_acknowledged",
            message=(
                "Se creó una ocurrencia nueva y auditada; la entrega original conserva su "
                "estado y Gmail podría entregar un duplicado."
            ),
        ),
    )

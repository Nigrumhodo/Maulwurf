"""M4 task endpoints (A3.3, DISENO §3.7).

All routes are tenant-scoped by the authenticated session. Reads rebuild evidence from the
persisted provenance and the immutable segment text; writes validate the source kind and
the optimistic-locking token. `confirm` returns the task with `calendar_sync_status`
separate from `status`; A3.3 does not create the Calendar outbox, so a successful
confirmation is `blocked` with a closed notice until A3.4 lands.
"""

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from app.core.deps import CurrentSession, DbSession, MutationSession
from app.schemas.tasks import (
    TaskConfirmBody,
    TaskConfirmOut,
    TaskCreate,
    TaskLifecycleOut,
    TaskListOut,
    TaskOut,
    TaskPatch,
    TaskVersionBody,
)
from app.services import tasks as task_service

router = APIRouter(tags=["tasks"])


@router.get(
    "/tasks",
    responses={
        200: {"description": "Tenant-scoped task proposals with evidence and pending revision"},
        401: {"description": "Sin sesión (auth_required)"},
        422: {
            "description": "validation_failed: status, limit, cursor o rango de fechas inválidos"
        },
    },
)
async def list_tasks(
    session: CurrentSession,
    db: DbSession,
    status: Annotated[str | None, Query()] = None,
    subject_id: Annotated[uuid.UUID | None, Query()] = None,
    due_from: Annotated[date | None, Query()] = None,
    due_to: Annotated[date | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> TaskListOut:
    return await task_service.list_tasks(
        db,
        session.user_id,
        status=status,
        subject_id=subject_id,
        due_from=due_from,
        due_to=due_to,
        limit=limit,
        cursor=cursor,
    )


@router.get(
    "/tasks/{task_id}",
    responses={
        200: {"description": "Task detail with evidence and pending revision"},
        401: {"description": "Sin sesión (auth_required)"},
        404: {"description": "not_found: inexistente o de otro usuario"},
    },
)
async def get_task(
    task_id: uuid.UUID, session: CurrentSession, db: DbSession
) -> TaskOut:
    return await task_service.get_task(db, session.user_id, task_id)


@router.post(
    "/tasks",
    status_code=201,
    responses={
        201: {"description": "Manual, chat or extraction proposal persisted"},
        401: {"description": "Sin sesión (auth_required)"},
        403: {"description": "csrf_invalid"},
        422: {"description": "validation_failed | evidence_invalid"},
    },
)
async def create_task(
    body: TaskCreate, session: MutationSession, db: DbSession
) -> TaskOut:
    out = await task_service.create_task(db, session.user_id, body)
    await db.commit()
    return out


@router.patch(
    "/tasks/{task_id}",
    responses={
        200: {"description": "In-place edit or pending revision without touching active fields"},
        401: {"description": "Sin sesión (auth_required)"},
        403: {"description": "csrf_invalid"},
        404: {"description": "not_found: inexistente o de otro usuario"},
        409: {"description": "version_conflict | invalid_transition"},
        422: {"description": "validation_failed"},
    },
)
async def patch_task(
    task_id: uuid.UUID, body: TaskPatch, session: MutationSession, db: DbSession
) -> TaskOut:
    out = await task_service.patch_task(db, session.user_id, task_id, body)
    await db.commit()
    return out


@router.post(
    "/tasks/{task_id}/confirm",
    status_code=202,
    responses={
        202: {"description": "Confirmed; Calendar sync is blocked until A3.4 creates the outbox"},
        401: {"description": "Sin sesión (auth_required)"},
        403: {"description": "csrf_invalid"},
        404: {"description": "not_found: tarea o revisión inexistente"},
        409: {"description": "version_conflict | invalid_transition"},
        422: {"description": "date_unresolved | evidence_invalid | validation_failed"},
    },
)
async def confirm_task(
    task_id: uuid.UUID,
    body: TaskConfirmBody,
    session: MutationSession,
    db: DbSession,
) -> TaskConfirmOut:
    out, notice = await task_service.confirm_task(
        db,
        session.user_id,
        task_id,
        expected_version=body.expected_version,
        revision_id=body.revision_id,
    )
    await db.commit()
    return TaskConfirmOut(task=out, notice=notice)


@router.post(
    "/tasks/{task_id}/dismiss",
    responses={
        200: {"description": "Dismissed; future reminders cancelled and the event kept"},
        401: {"description": "Sin sesión (auth_required)"},
        403: {"description": "csrf_invalid"},
        404: {"description": "not_found: inexistente o de otro usuario"},
        409: {"description": "version_conflict | invalid_transition"},
    },
)
async def dismiss_task(
    task_id: uuid.UUID,
    body: TaskVersionBody,
    session: MutationSession,
    db: DbSession,
) -> TaskLifecycleOut:
    result = await task_service.dismiss_task(
        db, session.user_id, task_id, expected_version=body.expected_version
    )
    out = await task_service.get_task(db, session.user_id, task_id)
    await db.commit()
    return TaskLifecycleOut(
        task=out,
        cancelled_reminders=result.cancelled_reminders,
        calendar_event_kept=result.calendar_event_kept,
        notice=result.notice,
    )


@router.post(
    "/tasks/{task_id}/complete",
    responses={
        200: {"description": "Completed; future reminders cancelled and the event kept"},
        401: {"description": "Sin sesión (auth_required)"},
        403: {"description": "csrf_invalid"},
        404: {"description": "not_found: inexistente o de otro usuario"},
        409: {"description": "version_conflict | invalid_transition"},
    },
)
async def complete_task(
    task_id: uuid.UUID,
    body: TaskVersionBody,
    session: MutationSession,
    db: DbSession,
) -> TaskLifecycleOut:
    result = await task_service.complete_task(
        db, session.user_id, task_id, expected_version=body.expected_version
    )
    out = await task_service.get_task(db, session.user_id, task_id)
    await db.commit()
    return TaskLifecycleOut(
        task=out,
        cancelled_reminders=result.cancelled_reminders,
        calendar_event_kept=result.calendar_event_kept,
        notice=result.notice,
    )

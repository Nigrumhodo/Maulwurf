"""M4 task inbox endpoints (A3.3 read slice, DISENO 3.7).

Only tenant-scoped reads are exposed here. Evidence is reconstructed from the persisted
source span and immutable segment text; a quote, time or class id is never accepted from a
client payload. The write endpoints are added by the A3.3 write slice and A3.4/A3.7.
"""

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from app.core.deps import CurrentSession, DbSession, MutationSession
from app.schemas.tasks import (
    TaskConfirmationBody,
    TaskConfirmationOut,
    TaskLifecycleOut,
    TaskListOut,
    TaskOut,
    TaskPatch,
    TaskUpdateOut,
    TaskVersionBody,
)
from app.services import tasks as task_service

router = APIRouter(tags=["tasks"])


@router.get(
    "/tasks",
    responses={
        200: {"description": "Tenant-scoped task proposals with evidence and pending revision"},
        401: {"description": "Sin sesión (auth_required)"},
        422: {"description": "validation_failed: status, limit o cursor inválidos"},
    },
)
async def list_tasks(
    session: CurrentSession,
    db: DbSession,
    status: Annotated[str | None, Query()] = None,
    subject_id: Annotated[uuid.UUID | None, Query()] = None,
    audio_id: Annotated[uuid.UUID | None, Query()] = None,
    due_from: Annotated[date | None, Query()] = None,
    due_to: Annotated[date | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query()] = None,
) -> TaskListOut:
    return await task_service.list_tasks(
        db,
        session.user_id,
        status=status,
        subject_id=subject_id,
        audio_id=audio_id,
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
        404: {"description": "Tarea inexistente o ajena (not_found)"},
    },
)
async def get_task(
    task_id: uuid.UUID, session: CurrentSession, db: DbSession
) -> TaskOut:
    return await task_service.get_task(db, session.user_id, task_id)


@router.patch(
    "/tasks/{task_id}",
    responses={
        200: {
            "description": (
                "Updated active task, or the unchanged active task plus a pending revision"
            )
        },
        401: {"description": "Sin sesión (auth_required)"},
        403: {"description": "CSRF/Origin inválido (csrf_invalid)"},
        404: {"description": "Tarea inexistente o ajena (not_found)"},
        409: {"description": "version_conflict o invalid_transition"},
        422: {"description": "validation_failed: fecha, zona o bloque de agenda inválidos"},
    },
)
async def patch_task(
    task_id: uuid.UUID,
    body: TaskPatch,
    session: MutationSession,
    db: DbSession,
) -> TaskUpdateOut:
    updated = await task_service.patch_task(db, session.user_id, task_id, body)
    await db.commit()
    return updated


@router.post(
    "/tasks/{task_id}/confirm",
    status_code=202,
    responses={
        202: {"description": "Confirmed task; Calendar write pending or blocked"},
        401: {"description": "Sin sesión (auth_required)"},
        403: {"description": "CSRF/Origin inválido (csrf_invalid)"},
        404: {"description": "Tarea inexistente o ajena (not_found)"},
        409: {
            "description": (
                "version_conflict, revision_stale, revision_required or invalid_transition"
            )
        },
        422: {
            "description": (
                "date_unresolved, event_block_missing, event_block_invalid or evidence_invalid"
            )
        },
    },
)
async def confirm_task(
    task_id: uuid.UUID,
    body: TaskConfirmationBody,
    session: MutationSession,
    db: DbSession,
) -> TaskConfirmationOut:
    confirmed = await task_service.confirm_task(
        db,
        session.user_id,
        task_id,
        expected_version=body.expected_version,
        revision_id=body.revision_id,
    )
    await db.commit()
    return confirmed


@router.post(
    "/tasks/{task_id}/dismiss",
    responses={
        200: {"description": "Dismissed task with its kept Calendar event notice"},
        401: {"description": "Sin sesión (auth_required)"},
        403: {"description": "CSRF/Origin inválido (csrf_invalid)"},
        404: {"description": "Tarea inexistente o ajena (not_found)"},
        409: {"description": "version_conflict o invalid_transition"},
    },
)
async def dismiss_task(
    task_id: uuid.UUID,
    body: TaskVersionBody,
    session: MutationSession,
    db: DbSession,
) -> TaskLifecycleOut:
    dismissed = await task_service.dismiss_task(
        db, session.user_id, task_id, expected_version=body.expected_version
    )
    await db.commit()
    return dismissed


@router.post(
    "/tasks/{task_id}/complete",
    responses={
        200: {"description": "Completed task with its kept Calendar event notice"},
        401: {"description": "Sin sesión (auth_required)"},
        403: {"description": "CSRF/Origin inválido (csrf_invalid)"},
        404: {"description": "Tarea inexistente o ajena (not_found)"},
        409: {"description": "version_conflict o invalid_transition"},
    },
)
async def complete_task(
    task_id: uuid.UUID,
    body: TaskVersionBody,
    session: MutationSession,
    db: DbSession,
) -> TaskLifecycleOut:
    completed = await task_service.complete_task(
        db, session.user_id, task_id, expected_version=body.expected_version
    )
    await db.commit()
    return completed

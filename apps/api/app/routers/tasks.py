"""M4 task inbox endpoints (A3.3 read slice, DISENO 3.7).

Only tenant-scoped reads are exposed here. Evidence is reconstructed from the persisted
source span and immutable segment text; a quote, time or class id is never accepted from a
client payload. The write endpoints are added by the A3.3 write slice and A3.4/A3.7.
"""

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from app.core.deps import CurrentSession, DbSession
from app.schemas.tasks import TaskListOut, TaskOut
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

"""Consultas de materias (A1.6) siempre acotadas al tenant (ADR-0006).

Los routers no ejecutan consultas: llaman a estas funciones con el `user_id` de la sesión.
"""
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.models import Audio, Subject
from app.services.tenant import get_owned, owned_by

# Índice único parcial `(user_id, lower(name)) WHERE deleted_at IS NULL` (migración 0001).
NAME_INDEX = "subjects_active_name"


def _name_taken() -> ApiError:
    return ApiError(409, "subject_name_taken", "Ya tienes una materia con ese nombre.")


async def create(
    db: AsyncSession, user_id: uuid.UUID, *, name: str, color: str, teacher: str | None
) -> Subject:
    """Crea una materia del usuario; nombre repetido (sin distinguir mayúsculas) → 409."""
    subject = Subject(user_id=user_id, name=name, color=color, teacher=teacher)
    try:
        # SAVEPOINT: si el índice único rechaza el INSERT, solo se deshace esta parte y la
        # transacción de la petición sigue utilizable.
        async with db.begin_nested():
            db.add(subject)
            await db.flush()  # la BD asigna el id; aún no es definitivo hasta el commit
    except IntegrityError as exc:
        if NAME_INDEX in str(exc.orig):
            raise _name_taken() from exc
        raise
    return subject


async def apply_changes(db: AsyncSession, subject: Subject, changes: dict[str, Any]) -> None:
    """Aplica solo los campos indicados; un nombre ya usado por otra materia → 409."""
    try:
        async with db.begin_nested():
            for field, value in changes.items():
                setattr(subject, field, value)
            await db.flush()
    except IntegrityError as exc:
        if NAME_INDEX in str(exc.orig):
            raise _name_taken() from exc
        raise


async def get_active(db: AsyncSession, user_id: uuid.UUID, subject_id: uuid.UUID) -> Subject | None:
    """Materia activa del usuario; `None` si no existe, es ajena o está borrada.

    Los tres casos son indistinguibles a propósito (ADR-0006): la API responde 404 en todos.
    """
    subject = await get_owned(db, Subject, user_id, subject_id)
    if subject is None or subject.deleted_at is not None:
        return None
    return subject


async def count_active_classes(db: AsyncSession, user_id: uuid.UUID, subject_id: uuid.UUID) -> int:
    """Clases (audios) no borradas de una materia del usuario."""
    total = await db.scalar(
        select(func.count()).where(
            Audio.user_id == user_id,
            Audio.subject_id == subject_id,
            Audio.deleted_at.is_(None),
        )
    )
    return int(total or 0)


async def list_active_with_class_count(
    db: AsyncSession, user_id: uuid.UUID
) -> list[tuple[Subject, int]]:
    """Materias activas del usuario, ordenadas sin distinguir mayúsculas, con sus clases activas."""
    subjects = (
        await db.scalars(
            owned_by(Subject, user_id)
            .where(Subject.deleted_at.is_(None))
            .order_by(func.lower(Subject.name))
        )
    ).all()
    # Una sola consulta agrupada para todas las materias (evita 1 consulta por materia).
    rows = await db.execute(
        select(Audio.subject_id, func.count())
        .where(Audio.user_id == user_id, Audio.deleted_at.is_(None))
        .group_by(Audio.subject_id)
    )
    counts = {subject_id: total for subject_id, total in rows.all()}
    return [(subject, counts.get(subject.id, 0)) for subject in subjects]

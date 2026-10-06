"""Consultas de materias (A1.6) siempre acotadas al tenant (ADR-0006).

Los routers no ejecutan consultas: llaman a estas funciones con el `user_id` de la sesión.
"""
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Audio, Subject
from app.services.tenant import get_owned, owned_by


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

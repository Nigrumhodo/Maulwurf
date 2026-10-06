"""Consultas de materias (A1.6) siempre acotadas al tenant (ADR-0006).

Los routers no ejecutan consultas: llaman a estas funciones con el `user_id` de la sesión.
"""
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Audio, Subject
from app.services.tenant import owned_by


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

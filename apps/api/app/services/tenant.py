"""Acceso a datos con filtro de tenant obligatorio (ADR-0006).

Toda lectura de una tabla de propiedad pasa por aquí con el `user_id` de la sesión
autenticada. Un ID ajeno se comporta igual que uno inexistente (404 en la API).
"""
import uuid
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import TenantOwned


def owned_by[T: TenantOwned](model: type[T], user_id: uuid.UUID) -> Select[tuple[T]]:
    """`SELECT` de `model` restringido a las filas de `user_id`; base de toda consulta."""
    return select(model).where(model.user_id == user_id)


async def get_owned[T: TenantOwned](
    session: AsyncSession, model: type[T], user_id: uuid.UUID, resource_id: Any
) -> T | None:
    """Recurso por ID solo si pertenece a `user_id`; `None` si no existe o es ajeno."""
    pk = model.id  # type: ignore[attr-defined]
    result = await session.execute(owned_by(model, user_id).where(pk == resource_id))
    return result.scalar_one_or_none()

"""Revalidación de un evento justo antes de publicarlo (J2.1).

Un evento puede quedar pendiente mientras su audio se borra, su dueño pierde el acceso o
llega una versión nueva. Esta lectura decide si el job todavía debe salir.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Audio, OutboxEvent, User
from app.services.outbox import Outcome


async def revalidate(db: AsyncSession, event: OutboxEvent) -> Outcome | None:
    """Devuelve el motivo de rechazo, o `None` si el evento sigue siendo publicable.

    Solo se llama con eventos de audio (el gate ya descarta los demás). Lectura de snapshot,
    sin `FOR SHARE`: `delete_audio` toma `audios` antes que `outbox_events`, y un bloqueo
    aquí invertiría ese orden y provocaría deadlock. La ventana entre esta lectura y el
    encolado la cierra la comprobación dentro del job (J2.2), no esta.
    """
    row = (
        await db.execute(
            select(Audio.deleted_at, Audio.active_transcript_version, User.status)
            .join(User, User.id == Audio.user_id)
            .where(Audio.user_id == event.user_id, Audio.id == event.resource_id)
        )
    ).one_or_none()
    if row is None or row.deleted_at is not None:
        return Outcome.TOMBSTONED
    if row.status != "active":
        return Outcome.OWNER_REVOKED
    # Sin versión activa no hay con qué comparar. Es el caso de los eventos creados antes de
    # activar un transcript: se publican igual, como hasta ahora.
    active = row.active_transcript_version
    if active is not None and event.resource_version != active:
        return Outcome.STALE_VERSION
    return None

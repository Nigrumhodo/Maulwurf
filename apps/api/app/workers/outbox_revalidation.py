"""Revalidates an event right before it is published (J2.1).

An event can stay pending while its audio is deleted, its owner loses access, or
a new version arrives. This read decides whether the job should still be published.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Audio, OutboxEvent, User
from app.services.outbox import Outcome


async def revalidate(db: AsyncSession, event: OutboxEvent) -> Outcome | None:
    """Returns the rejection reason, or `None` if the event is still publishable.

    Only called with audio events (the gate already drops the others). Snapshot read,
    without `FOR SHARE`: `delete_audio` locks `audios` before `outbox_events`, and a lock here would
    reverse that order and cause a deadlock. The window between this read and enqueueing is
    closed by the check inside the job (J2.2), not by this one.
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
    # Without an active version there is nothing to compare against. This covers events
    # created before a transcript was activated: they are published anyway, as before.
    active = row.active_transcript_version
    if active is not None and event.resource_version != active:
        return Outcome.STALE_VERSION
    return None

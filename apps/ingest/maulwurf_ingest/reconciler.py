"""Post-commit recovery (S2.7). One pass, no ASR, no retry loop.

A dead process can leave ``transcript_committed_cleanup_pending`` with the text
already stored and the outbox still disabled. This pass enables that outbox only
after cleanup is verified on this host. A missing directory is not proof that the
previous tmpfs was destroyed (that evidence is S4.1), so the attempt stays
pending and is not retranscribed. ``cleanup_status=failed`` is left alone.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import uuid
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from pathlib import Path

import asyncpg

from maulwurf_ingest import admission, cleanup, persistence
from maulwurf_ingest import lease as leases
from maulwurf_ingest.cleanup import CleanupEvidence

logger = logging.getLogger(__name__)

DEFAULT_LEASE_TTL = timedelta(seconds=30)


class ReconcileAction(StrEnum):
    ENABLED = "enabled"
    HELD = "held"
    CLEANUP_FAILED = "cleanup_failed"
    LOST = "lost"


@dataclass(frozen=True)
class ReconcileResult:
    attempt_id: uuid.UUID
    action: ReconcileAction


def local_workdirs(tmp_dir: Path, attempt_id: uuid.UUID) -> list[Path]:
    """Attempt directories this process can see. Symlinks are ignored."""
    prefix = f"attempt-{attempt_id}-"
    if not tmp_dir.is_dir():
        return []
    found: list[Path] = []
    for entry in tmp_dir.iterdir():
        if entry.name.startswith(prefix) and entry.is_dir() and not entry.is_symlink():
            found.append(entry)
    return sorted(found)


def _combine(parts: list[CleanupEvidence]) -> CleanupEvidence:
    return CleanupEvidence(
        workdir_absent=all(part.workdir_absent for part in parts),
        tmp_is_tmpfs=all(part.tmp_is_tmpfs for part in parts),
        live_processes=sum(part.live_processes for part in parts),
        open_descriptors=sum(part.open_descriptors for part in parts),
        mounts_under_workdir=sum(part.mounts_under_workdir for part in parts),
        unreadable_processes=sum(part.unreadable_processes for part in parts),
    )


async def _destroy(workdirs: list[Path]) -> CleanupEvidence:
    parts: list[CleanupEvidence] = []
    for workdir in workdirs:
        # No process group: the owner that created the directory is already gone.
        parts.append(await asyncio.to_thread(cleanup.destroy_and_verify, workdir, None))
    return _combine(parts)


async def _active_version(
    pool: asyncpg.Pool, lease: leases.Lease,
) -> tuple[uuid.UUID, int] | None:
    row = await pool.fetchrow(
        """
        SELECT ia.audio_id, a.active_transcript_version
          FROM ingestion_attempts ia
          JOIN audios a ON a.user_id = ia.user_id AND a.id = ia.audio_id
         WHERE ia.id = $1 AND ia.user_id = $2 AND ia.fencing_token = $3
           AND a.deleted_at IS NULL
        """,
        lease.attempt_id, lease.user_id, lease.fencing_token,
    )
    if row is None:
        return None
    audio_id = row["audio_id"]
    version = row["active_transcript_version"]
    if not isinstance(audio_id, uuid.UUID) or not isinstance(version, int):
        return None
    return audio_id, version


async def _enable(pool: asyncpg.Pool, lease: leases.Lease) -> ReconcileAction:
    target = await _active_version(pool, lease)
    if target is None:
        return ReconcileAction.HELD
    audio_id, version = target
    enabled = await persistence.enable_text_outbox(
        pool, lease, audio_id=audio_id, version=version,
    )
    if not enabled:
        return ReconcileAction.LOST
    logger.info("reconciler.enabled attempt_id=%s", lease.attempt_id)
    return ReconcileAction.ENABLED


async def reconcile_committed(
    pool: asyncpg.Pool, *, instance: str, tmp_dir: str | None = None,
    lease_ttl: timedelta = DEFAULT_LEASE_TTL,
) -> tuple[ReconcileResult, ...]:
    """Enable the text outbox for expired post-commit attempts. Never retranscribes."""
    root = Path(tmp_dir) if tmp_dir is not None else Path(tempfile.gettempdir())
    rows = await pool.fetch(
        """
        SELECT id, user_id, cleanup_status
          FROM ingestion_attempts
         WHERE status = 'transcript_committed_cleanup_pending'
           AND cleanup_status = ANY($1::text[])
           AND (lease_expires_at IS NULL OR lease_expires_at <= now())
        """,
        ["pending", "verified"],
    )
    results: list[ReconcileResult] = []
    for row in rows:
        attempt_id = row["id"]
        user_id = row["user_id"]
        if not isinstance(attempt_id, uuid.UUID) or not isinstance(user_id, uuid.UUID):
            continue
        cleanup_status = str(row["cleanup_status"])
        workdirs = local_workdirs(root, attempt_id)
        # No local files and no recorded verification: do not guess that the
        # previous mount is gone, and do not take the lease.
        if cleanup_status == "pending" and not workdirs:
            logger.info("reconciler.held attempt_id=%s", attempt_id)
            results.append(ReconcileResult(attempt_id, ReconcileAction.HELD))
            continue
        lease = await leases.reclaim_committed(
            pool, user_id=user_id, attempt_id=attempt_id, instance=instance, ttl=lease_ttl,
        )
        if lease is None:
            results.append(ReconcileResult(attempt_id, ReconcileAction.LOST))
            continue
        if workdirs:
            evidence = await _destroy(workdirs)
            recorded = await leases.record_cleanup(
                pool, lease, verified=evidence.verified, evidence=evidence.as_json(),
            )
            if not evidence.verified:
                if recorded:
                    admission.halt(attempt_id)
                logger.error("reconciler.cleanup_failed attempt_id=%s", attempt_id)
                results.append(ReconcileResult(attempt_id, ReconcileAction.CLEANUP_FAILED))
                continue
            if not recorded:
                results.append(ReconcileResult(attempt_id, ReconcileAction.LOST))
                continue
        results.append(ReconcileResult(attempt_id, await _enable(pool, lease)))
    return tuple(results)

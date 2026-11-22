"""Post-commit recovery (S2.7). One pass, no ASR, no retry loop.

A dead process can leave ``transcript_committed_cleanup_pending`` with the text
already stored and the outbox still disabled. This pass enables that outbox only
after cleanup is verified on this host. A missing directory is not proof that the
previous tmpfs was destroyed (that evidence is S4.1), so the attempt stays
pending and is not retranscribed. ``cleanup_status=failed`` is left alone.
"""

from __future__ import annotations

import logging
import tempfile
import uuid
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from pathlib import Path

import asyncpg

from maulwurf_ingest import admission, cleanup, metrics, persistence, rls
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
        parts.append(await cleanup.AttemptDir.destroy_one(workdir))
    return _combine(parts)


async def _active_version(
    pool: asyncpg.Pool, lease: leases.Lease,
) -> tuple[uuid.UUID, int] | None:
    row = await rls.fetchrow(pool, lease.user_id,
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


async def _record_cleanup(
    pool: asyncpg.Pool, lease: leases.Lease, *, verified: bool, evidence: dict[str, object],
) -> bool | None:
    """Persist cleanup evidence; ``None`` when the write itself failed.

    A driver error on this statement used to abort the whole pass, including
    ``sweep_stranded``. One attempt that cannot record its evidence must not stop
    the recovery of the rest: the caller treats the row as not recorded and
    continues with the next one.
    """
    try:
        return await leases.record_cleanup(
            pool, lease, verified=verified, evidence=evidence,
        )
    except Exception as exc:  # noqa: BLE001 — one row must not abort the whole pass
        # Only the type: driver errors can include the DSN with credentials.
        logger.error(
            "reconciler.cleanup_record_failed attempt_id=%s error=%s",
            lease.attempt_id, type(exc).__name__,
        )
        return None


def _note_unrecorded_cleanup(attempt_id: uuid.UUID) -> None:
    """Make a cleanup this pass could not persist explicitly visible.

    The row stays ``pending`` (no durable ``failed`` latch exists), so admission
    stays open, but neither a driver error nor a moved fencing token may leave
    only a log line: the alert and the failure metric are the observable record.
    """
    admission.flag_unrecorded_cleanup(attempt_id)
    metrics.record_failure("cleanup", "cleanup_unrecorded")


async def reconcile_committed(
    pool: asyncpg.Pool, *, instance: str, tmp_dir: str | None = None,
    lease_ttl: timedelta = DEFAULT_LEASE_TTL,
) -> tuple[ReconcileResult, ...]:
    """Enable the text outbox for expired post-commit attempts. Never retranscribes."""
    root = Path(tmp_dir) if tmp_dir is not None else Path(tempfile.gettempdir())
    rows = await rls.fetch(pool, None,
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
            try:
                evidence = await _destroy(workdirs)
            except Exception as exc:  # noqa: BLE001 — an exception is not proof of absence
                # The periodic sweeper must not turn an unverified cleanup into a silent
                # retry: record it as failed (durable across restarts) and stop admission.
                logger.error(
                    "reconciler.cleanup_error attempt_id=%s error=%s",
                    attempt_id, type(exc).__name__,
                )
                cleanup.log_cleanup_failed(
                    str(attempt_id), None, error_type=type(exc).__name__,
                )
                recorded = await _record_cleanup(
                    pool, lease, verified=False,
                    evidence={"verified": False, "error": type(exc).__name__},
                )
                if recorded:
                    admission.halt(attempt_id)
                else:
                    _note_unrecorded_cleanup(attempt_id)
                results.append(ReconcileResult(attempt_id, ReconcileAction.CLEANUP_FAILED))
                continue
            recorded = await _record_cleanup(
                pool, lease, verified=evidence.verified, evidence=evidence.as_json(),
            )
            if not evidence.verified:
                if recorded:
                    admission.halt(attempt_id)
                else:
                    _note_unrecorded_cleanup(attempt_id)
                logger.error("reconciler.cleanup_failed attempt_id=%s", attempt_id)
                results.append(ReconcileResult(attempt_id, ReconcileAction.CLEANUP_FAILED))
                continue
            if not recorded:
                _note_unrecorded_cleanup(attempt_id)
                results.append(ReconcileResult(attempt_id, ReconcileAction.LOST))
                continue
        results.append(ReconcileResult(attempt_id, await _enable(pool, lease)))
    return tuple(results)


# Same status and error code as ``LOSS_REASONS["technical_loss"]`` in
# ``apps/api/app/services/state_machine.py``. Ingest cannot import the API package.
STRANDED_STATES = ("receiving", "transcribing")
STRANDED_STATUS = "requires_reupload"
STRANDED_ERROR_CODE = "technical_loss"
SWEEP_BATCH_SIZE = 100
SWEEP_MAX_BATCHES = 10

_DUE_SQL = """
SELECT id, user_id, fencing_token
  FROM ingestion_attempts
 WHERE status = ANY($1::text[])
   AND lease_expires_at <= now()
 ORDER BY lease_expires_at
 LIMIT $2
"""

# Fence before any deletion. The predicate token is the one just read. Bumping it
# first means a concurrent acquire loses the row and cannot recreate the directory
# we are about to destroy. RETURNING is the token cleanup must record against.
_FENCE_SQL = """
UPDATE ingestion_attempts
   SET status = $3, error_code = $4,
       fencing_token = fencing_token + 1, updated_at = now()
 WHERE id = $1 AND user_id = $2 AND fencing_token = $5
   AND status = ANY($6::text[]) AND lease_expires_at <= now()
 RETURNING fencing_token
"""

# Orphan directories belong to terminal rows. A live receiving/transcribing lease
# still owns its tmpfs and is left to the owner or to the stranded fence above.
_ORPHAN_SKIP_STATES = frozenset({"receiving", "transcribing"})
_ORPHAN_SQL = """
SELECT id, user_id, fencing_token, status,
       (lease_expires_at IS NOT NULL AND lease_expires_at > now()) AS lease_live
  FROM ingestion_attempts
 WHERE id = ANY($1::uuid[])
"""


async def _destroy_stranded(
    pool: rls.Executor, attempt_id: uuid.UUID, user_id: uuid.UUID, token: int,
    workdirs: list[Path],
) -> None:
    """Verified destruction after the row was fenced onto ``token``."""
    lease = leases.Lease(attempt_id, user_id, "sweep", token, DEFAULT_LEASE_TTL)
    try:
        evidence = await _destroy(workdirs)
    except Exception as exc:  # noqa: BLE001 — an exception is not proof of absence
        logger.error(
            "reconciler.stranded_cleanup_error attempt_id=%s error=%s",
            attempt_id, type(exc).__name__,
        )
        cleanup.log_cleanup_failed(str(attempt_id), None, error_type=type(exc).__name__)
        recorded = await _record_cleanup(
            pool, lease, verified=False,
            evidence={"verified": False, "error": type(exc).__name__},
        )
        if recorded:
            admission.halt(attempt_id)
        else:
            _note_unrecorded_cleanup(attempt_id)
        return
    recorded = await _record_cleanup(
        pool, lease, verified=evidence.verified, evidence=evidence.as_json(),
    )
    if evidence.verified:
        return
    if recorded:
        admission.halt(attempt_id)
    else:
        _note_unrecorded_cleanup(attempt_id)
    logger.error("reconciler.stranded_cleanup_failed attempt_id=%s", attempt_id)


async def sweep_stranded(
    pool: rls.Executor, *, tmp_dir: str | None = None,
) -> tuple[uuid.UUID, ...]:
    """End receiving/transcribing attempts whose lease expired. Bounded, never retranscribes.

    The row is fenced before any directory is destroyed. A lost race leaves the
    new owner's files in place. No directory is not proof the audio is gone, so
    ``cleanup_status`` stays as it was when this host has nothing to delete.
    Committed attempts and ``awaiting_upload`` are not selected.
    """
    root = Path(tmp_dir) if tmp_dir is not None else Path(tempfile.gettempdir())
    ended: list[uuid.UUID] = []
    for _ in range(SWEEP_MAX_BATCHES):
        rows = await rls.fetch(
            pool, None, _DUE_SQL, list(STRANDED_STATES), SWEEP_BATCH_SIZE,
        )
        if not rows:
            break
        for row in rows:
            attempt_id = row["id"]
            user_id = row["user_id"]
            token = row["fencing_token"]
            if (
                not isinstance(attempt_id, uuid.UUID)
                or not isinstance(user_id, uuid.UUID)
                or isinstance(token, bool)
                or not isinstance(token, int)
            ):
                continue
            fenced = await rls.fetchrow(
                pool, user_id, _FENCE_SQL,
                attempt_id, user_id, STRANDED_STATUS, STRANDED_ERROR_CODE, token,
                list(STRANDED_STATES),
            )
            if fenced is None:
                logger.info("reconciler.stranded_lost_race attempt_id=%s", attempt_id)
                continue
            new_token = fenced["fencing_token"]
            if isinstance(new_token, bool) or not isinstance(new_token, int):
                continue
            workdirs = local_workdirs(root, attempt_id)
            if workdirs:
                await _destroy_stranded(pool, attempt_id, user_id, new_token, workdirs)
            ended.append(attempt_id)
            logger.info(
                "reconciler.stranded_ended attempt_id=%s user_id=%s", attempt_id, user_id,
            )
        if len(rows) < SWEEP_BATCH_SIZE:
            break
    if ended:
        logger.info("reconciler.stranded_swept count=%s", len(ended))
    return tuple(ended)


async def sweep_orphan_dirs(
    pool: rls.Executor, *, tmp_dir: str | None = None,
) -> tuple[uuid.UUID, ...]:
    """Destroy leftover attempt directories that no live lease still owns.

    Runs after the stranded fence. A directory whose row is still
    ``receiving``/``transcribing`` or whose lease is in the future is skipped.
    A successful verified destruction is recorded; a directory that survives
    keeps admission closed.
    """
    root = Path(tmp_dir) if tmp_dir is not None else Path(tempfile.gettempdir())
    dirs = cleanup.list_attempt_dirs(root)
    if not dirs:
        return ()
    ids: list[uuid.UUID] = []
    for path in dirs:
        attempt_id = cleanup.attempt_id_from_dirname(path.name)
        if attempt_id is not None:
            ids.append(attempt_id)
    if not ids:
        return ()
    rows = await rls.fetch(pool, None, _ORPHAN_SQL, ids)
    protected: set[uuid.UUID] = set()
    owners: dict[uuid.UUID, tuple[uuid.UUID, int]] = {}
    for row in rows:
        attempt_id = row["id"]
        user_id = row["user_id"]
        token = row["fencing_token"]
        status = row["status"]
        if not isinstance(attempt_id, uuid.UUID):
            continue
        if row["lease_live"] is True or status in _ORPHAN_SKIP_STATES:
            protected.add(attempt_id)
            continue
        if (
            isinstance(user_id, uuid.UUID)
            and not isinstance(token, bool)
            and isinstance(token, int)
        ):
            owners[attempt_id] = (user_id, token)
    swept = await cleanup.sweep_orphan_dirs(root, protected=protected)
    removed: list[uuid.UUID] = []
    for item in swept:
        if item.evidence is not None and item.evidence.workdir_absent:
            removed.append(item.attempt_id)
        owner = owners.get(item.attempt_id)
        if owner is None:
            continue
        user_id, token = owner
        lease = leases.Lease(item.attempt_id, user_id, "sweep", token, DEFAULT_LEASE_TTL)
        if item.evidence is None:
            recorded = await _record_cleanup(
                pool, lease, verified=False, evidence={"verified": False, "error": "orphan_sweep"},
            )
            if recorded:
                admission.halt(item.attempt_id)
            else:
                _note_unrecorded_cleanup(item.attempt_id)
            continue
        recorded = await _record_cleanup(
            pool, lease, verified=item.evidence.verified, evidence=item.evidence.as_json(),
        )
        if item.evidence.verified:
            logger.info("cleanup.orphan_recovered attempt_id=%s", item.attempt_id)
            continue
        if recorded:
            admission.halt(item.attempt_id)
        else:
            _note_unrecorded_cleanup(item.attempt_id)
        # A surviving directory is already counted by the filesystem sweep.
        if item.evidence.workdir_absent:
            cleanup.log_cleanup_failed(str(item.attempt_id), item.evidence)
    return tuple(removed)

"""Revalidation and run claim for the `index(audio_id, transcript_version)` job (J2.2).

The job payload carries only IDs. Every check reads the database: the dispatcher's
revalidation is a snapshot taken before enqueueing (`workers/outbox_revalidation.py`), so the
check here is the real guard. The tenant is read from the audio row, never from the payload.

The claim is the `processing_runs` row of stage `index`. Its unique key makes a redelivery
land on the same row, and its status is the lease: a `running` row blocks other workers until
`LEASE` expires, which covers a worker that died mid-job. Each claim takes the next
`fencing_token`, so a worker that was silent past `LEASE` cannot complete the run or activate
the generation after the reconciler closed it (A2.20).
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Audio, IndexGeneration, ProcessingRun, Transcript, User
from app.services.indexing import INDEX_ACTIVE, INDEX_RETIRED, activate_generation
from app.services.runs import INDEX_CONFIG_VERSION, latest_attempt

STAGE_INDEX = "index"
# Longer than the job timeout (`workers/index_job.py`), so a live job never looks expired.
LEASE = timedelta(minutes=15)


class IndexSkip(StrEnum):
    """Why an `index` delivery changes nothing. Logged by the caller, with IDs only."""

    TOMBSTONED = "tombstoned"
    OWNER_REVOKED = "owner_revoked"
    STALE_VERSION = "stale_version"
    CLEANUP_PENDING = "cleanup_pending"
    NO_TRANSCRIPT = "no_transcript"
    ALREADY_INDEXED = "already_indexed"
    IN_PROGRESS = "in_progress"
    SUPERSEDED = "superseded"


class RunFenceOutcome(StrEnum):
    """Result of a fenced write on a processing run."""

    APPLIED = "applied"
    FENCED = "fenced"


class RunFenced(Exception):
    """The claim that tries to activate a generation no longer owns a running run."""


@dataclass(frozen=True)
class RunClaim:
    """The run lease a delivery holds: its row and the token that fences stale workers."""

    run_id: uuid.UUID
    fencing_token: int


def index_version_for(audio_id: uuid.UUID, transcript_version: int) -> str:
    """Generation name for one class version; the same delivery always maps to the same name.

    Generations are per user and built from every eligible class (`services/indexing.py`).
    Naming one per class version keeps redeliveries idempotent: the second delivery finds the
    generation it already produced.
    """
    return f"audio-{audio_id}-t{transcript_version}"


async def revalidate(
    db: AsyncSession, *, audio_id: uuid.UUID, transcript_version: int
) -> uuid.UUID | IndexSkip:
    """Returns the owning user when the delivery may index, or the reason it must do nothing."""
    row = (
        await db.execute(
            select(Audio.user_id, Audio.deleted_at, Audio.active_transcript_version, User.status)
            .join(User, User.id == Audio.user_id)
            .where(Audio.id == audio_id)
        )
    ).one_or_none()
    if row is None or row.deleted_at is not None:
        return IndexSkip.TOMBSTONED
    if row.status != "active":
        return IndexSkip.OWNER_REVOKED
    if row.active_transcript_version != transcript_version:
        return IndexSkip.STALE_VERSION
    attempt = await latest_attempt(db, user_id=row.user_id, audio_id=audio_id)
    if attempt is None or attempt.cleanup_status != "verified":
        return IndexSkip.CLEANUP_PENDING
    if not await _transcript_exists(
        db, user_id=row.user_id, audio_id=audio_id, version=transcript_version
    ):
        return IndexSkip.NO_TRANSCRIPT
    owner: uuid.UUID = row.user_id
    return owner


async def _transcript_exists(
    db: AsyncSession, *, user_id: uuid.UUID, audio_id: uuid.UUID, version: int
) -> bool:
    found = await db.scalar(
        select(Transcript.id).where(
            Transcript.user_id == user_id,
            Transcript.audio_id == audio_id,
            Transcript.version == version,
        )
    )
    return found is not None


async def claim_run(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    audio_id: uuid.UUID,
    transcript_version: int,
    now: datetime | None = None,
) -> RunClaim | IndexSkip:
    """Takes the run lease and returns the claim, or the reason another delivery owns it.

    Runs under `FOR UPDATE`, so two deliveries that race on the same run queue up behind each
    other and the second one reads the state the first one left. Every successful claim takes
    the next fencing token: the previous claim can no longer finish or activate this run.
    """
    await db.execute(
        pg_insert(ProcessingRun)
        .values(
            user_id=user_id,
            audio_id=audio_id,
            stage=STAGE_INDEX,
            transcript_version=transcript_version,
            config_version=INDEX_CONFIG_VERSION,
            status="pending",
        )
        # Columns, not the constraint name: that name is truncated by PostgreSQL (see runs.py).
        .on_conflict_do_nothing(
            index_elements=["audio_id", "stage", "transcript_version", "config_version"]
        )
    )
    run = (
        await db.execute(
            select(ProcessingRun)
            .where(
                ProcessingRun.user_id == user_id,
                ProcessingRun.audio_id == audio_id,
                ProcessingRun.stage == STAGE_INDEX,
                ProcessingRun.transcript_version == transcript_version,
                ProcessingRun.config_version == INDEX_CONFIG_VERSION,
            )
            .with_for_update()
        )
    ).scalar_one()
    clock = now or datetime.now(UTC)
    if run.status == "succeeded":
        return IndexSkip.ALREADY_INDEXED
    if run.status == "running" and not _lease_expired(run, clock):
        return IndexSkip.IN_PROGRESS
    run.status = "running"
    run.started_at = clock
    run.finished_at = None
    run.error_code = None
    run.attempt_count += 1
    run.fencing_token += 1
    return RunClaim(run_id=run.id, fencing_token=run.fencing_token)


def _lease_expired(run: ProcessingRun, now: datetime) -> bool:
    return run.started_at is None or run.started_at < now - LEASE


async def finish_run(
    db: AsyncSession,
    *,
    run_id: uuid.UUID,
    fencing_token: int,
    status: str,
    error_code: str | None,
) -> RunFenceOutcome:
    """Records the terminal state only while the claim still owns the running run.

    `FENCED` is the typed no-op for a worker the reconciler overtook: it leaves the terminal
    state (and the requeued delivery) untouched.
    """
    result = await db.execute(
        update(ProcessingRun)
        .where(
            ProcessingRun.id == run_id,
            ProcessingRun.fencing_token == fencing_token,
            ProcessingRun.status == "running",
        )
        .values(status=status, error_code=error_code, finished_at=datetime.now(UTC))
        .returning(ProcessingRun.id)
        .execution_options(synchronize_session=False)
    )
    if result.scalar_one_or_none() is None:
        return RunFenceOutcome.FENCED
    return RunFenceOutcome.APPLIED


async def require_build_fence(
    db: AsyncSession, *, run_id: uuid.UUID, fencing_token: int
) -> None:
    """Takes a shared lock on the live run for the batch that is about to commit.

    The `FOR SHARE` is the build-phase fence ADR-0009 left to A2.18. It serializes with the
    reconciler's closing UPDATE: while a batch holds the shared lock, the run cannot be closed;
    once the batch commits, a stale worker's next fence sees the terminal row and raises
    `RunFenced` before its own commit. No write can therefore land after orphan cleanup closed
    the run, and the next build cannot adopt a stale batch under the same `index_version`.

    Autoflush is disabled on purpose: the fence must run before the pending chunks/embeddings
    are sent, otherwise a failed fence would already have written the batch it is rejecting.
    """
    with db.no_autoflush:
        locked = await db.scalar(
            select(ProcessingRun.id)
            .where(
                ProcessingRun.id == run_id,
                ProcessingRun.fencing_token == fencing_token,
                ProcessingRun.status == "running",
            )
            .with_for_update(read=True)
        )
    if locked is None:
        raise RunFenced(f"run {run_id} is not owned by fencing token {fencing_token}")


async def require_run_fence(
    db: AsyncSession, *, run_id: uuid.UUID, fencing_token: int
) -> None:
    """Locks the run row and rejects a claim that the reconciler already reclaimed.

    The `FOR UPDATE` is what makes the later activation atomic with this check: a reconciler
    close of the same row either happens before (and this call sees a terminal run) or waits
    until the activation transaction commits.
    """
    locked = await db.scalar(
        select(ProcessingRun.id)
        .where(
            ProcessingRun.id == run_id,
            ProcessingRun.fencing_token == fencing_token,
            ProcessingRun.status == "running",
        )
        .with_for_update()
    )
    if locked is None:
        raise RunFenced(f"run {run_id} is not owned by fencing token {fencing_token}")


async def activate_claimed_generation(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    version: str,
    claim: RunClaim,
    commit: bool = True,
) -> IndexGeneration:
    """Activates the generation only if the claim still holds the run lease.

    Both statements share the caller's transaction, so a stale claim cannot retire the active
    generation or point the classes at a build the reconciler already discarded.
    """
    await require_run_fence(
        db, run_id=claim.run_id, fencing_token=claim.fencing_token
    )
    return await activate_generation(db, user_id=user_id, version=version, commit=commit)


async def generation_for_version(
    db: AsyncSession, *, user_id: uuid.UUID, version: str
) -> IndexGeneration | None:
    return (
        await db.execute(
            select(IndexGeneration).where(
                IndexGeneration.user_id == user_id,
                IndexGeneration.version == version,
            )
        )
    ).scalar_one_or_none()


def generation_skip(status: str) -> IndexSkip | None:
    """Maps the status of an existing generation to the skip it implies (None: keep building)."""
    if status == INDEX_ACTIVE:
        return IndexSkip.ALREADY_INDEXED
    if status == INDEX_RETIRED:
        return IndexSkip.SUPERSEDED
    return None

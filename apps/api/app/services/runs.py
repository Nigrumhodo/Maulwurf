"""Retry over text vs reupload of a new ephemeral session (A2.8, S2.md §A2.8 on M2).

Normative sources: ESPECIFICACION.md M2, DISENO §3.5 (retry/reupload rows) and the decision
matrix of PROCESOS.md §5. All decision and SQL lives here and not in the router (ADR-0006
guard). The router only resolves ownership with a row lock and mounts the response:

- `retry` requires a USABLE transcript (active version whose `transcripts` row still exists;
  the tombstone clears the pointer, so a purged version fails closed) and only creates
  idempotent TEXT-stage runs over that persisted text. Runs dedupe on the unique key
  `(audio_id, stage, transcript_version, config_version)`; the outbox event dedupes on
  `(type, resource_id, resource_version, dedupe_key)` with a per-cycle key, so a repeated
  retry never duplicates work and a genuine new cycle always gets exactly one event.
- `reupload` only exists WITHOUT a usable transcript and without an active attempt: it opens
  a new `awaiting_upload` attempt on the SAME audio row (same ingestion identity: subject,
  date, time zone, language) reusing the previous attempt's consent snapshot, and clears
  `sha256` so the content identity is unknown again for the next `PUT` (A2.5 excludes the
  own audio, so re-sending the same file proceeds to ASR instead of matching itself).
- Conflicts are typed: `409 transcript_unavailable` (no usable text / obsolete version) and
  `409 invalid_transition` (stage not required, stage already succeeded, attempt still
  active including `transcript_committed_cleanup_pending`, transcript already present on
  reupload). Shape errors (empty, duplicate or unknown stages) are `422 validation_failed`.
- **Versioning (S3.4):** a run is identified by its stage, the transcript version it reads
  and the configuration/prompt version it applies (`RunSpec`). A retry or a backfill only
  reuses a row when all three match; a changed transcript or configuration version creates a
  NEW row and the previous one stays queryable. `run_history` lists that durable ledger.

The dispatcher keeps its cleanup gate as defense in depth: a retry event is enabled, but it
only publishes once the class's latest attempt has `cleanup_status='verified'`.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ApiError, validation_failed
from app.models import Audio, IngestionAttempt, OutboxEvent, ProcessingRun, Transcript
from app.models.ingestion import ACTIVE_INGESTION_STATES
from app.services import state_machine
from app.services.analysis_windows import DEFAULT_OVERLAP_TOKENS, DEFAULT_TARGET_TOKENS
from app.services.audios import UNASSIGNED_OWNER

INDEX_STAGE = "index"
ANALYSIS_STAGE = "analyze"
STAGES = (INDEX_STAGE, ANALYSIS_STAGE)
# Index configuration version. It is part of the run's unique key and is recorded on every
# row, so bumping it (chunk/embedding pipeline change) creates new runs instead of silently
# reusing results produced by another configuration.
INDEX_CONFIG_VERSION = "v1"
# The analysis prompt is owned by L3.1. Until it publishes the versioned extraction prompts,
# this provisional prompt version is recorded on each `analyze` run; the config version is
# derived from it so a prompt bump always creates a new run.
ANALYSIS_PROMPT_VERSION = "v1"
# The only terminal run states a retry may requeue (M2: failed/cancelled work can be redone;
# `succeeded` is a conflict and `pending`/`running` an idempotent no-op).
RETRYABLE_RUN_STATES = ("failed", "cancelled")
# Stage -> cleanup-gated outbox type (drift-tested against `CLEANUP_GATED_EVENT_TYPES`).
STAGE_EVENT_TYPE = {"index": "index_requested", "analyze": "analyze_requested"}

RunAction = Literal["create", "reset", "reuse", "conflict"]


@dataclass(frozen=True)
class RunSpec:
    """Versions that identify one durable run (S3.4).

    `config_version` covers the pipeline configuration; `prompt_version` is the explicit
    prompt identifier for `analyze` runs (None for `index`). Both are persisted, and the
    pair is part of the unique key through `config_version`.
    """

    stage: str
    config_version: str
    prompt_version: str | None


def analysis_config_version(prompt_version: str = ANALYSIS_PROMPT_VERSION) -> str:
    """Config version of the analysis stage for one prompt/window configuration.

    A prompt or window-configuration change changes this string, so `ensure_stage_run`
    creates a new run instead of reusing results produced under another configuration.
    """
    if not prompt_version.strip():
        raise ValueError("prompt_version must not be empty")
    return (
        f"analyze-v1:prompt={prompt_version}"
        f":window={DEFAULT_TARGET_TOKENS}/{DEFAULT_OVERLAP_TOKENS}"
    )


ANALYSIS_CONFIG_VERSION = analysis_config_version()


def run_spec(stage: str) -> RunSpec:
    """The current versions of one stage; an unknown stage is a programming error."""
    if stage == INDEX_STAGE:
        return RunSpec(
            stage=INDEX_STAGE,
            config_version=INDEX_CONFIG_VERSION,
            prompt_version=None,
        )
    if stage == ANALYSIS_STAGE:
        return RunSpec(
            stage=ANALYSIS_STAGE,
            config_version=analysis_config_version(),
            prompt_version=ANALYSIS_PROMPT_VERSION,
        )
    raise ValueError(f"unknown stage: {stage!r}")


def _invalid_transition(details: dict[str, object]) -> ApiError:
    # Same Spanish literal as `state_machine.invalid_transition` (DISENO §3.5 catalog).
    return ApiError(
        409,
        "invalid_transition",
        "La operación no es válida para el estado actual de esta clase.",
        details,
    )


def transcript_unavailable(active_transcript_version: int | None) -> ApiError:
    return ApiError(
        409,
        "transcript_unavailable",
        "Esta clase no tiene una transcripción utilizable; vuelve a subir el audio.",
        {"active_transcript_version": active_transcript_version},
    )


# --- Pure decision core (no I/O, unit-covered) ------------------------------------------


def validate_retry_stages(
    stages: Sequence[str],
    transcript_version: int,
    *,
    active_transcript_version: int | None,
    required_stages: Sequence[str],
) -> None:
    """Validate the whole retry matrix BEFORE any write; raise `ApiError` on rejection.

    Order matters for the contract: shape (`422`) first, then usability of the text
    (`409 transcript_unavailable`), then whether the stage is even owed for this class
    (`409 invalid_transition`). A programming error (unknown stage label) fails closed.
    """
    if not stages:
        raise validation_failed({"stages": "Debe reintentar al menos una etapa."})
    seen: set[str] = set()
    for stage in stages:
        if stage not in state_machine.STAGES:
            raise validation_failed({"stages": f"Etapa desconocida: {stage}."})
        if stage in seen:
            raise validation_failed({"stages": "Etapas duplicadas."})
        seen.add(stage)
    if active_transcript_version is None:
        raise transcript_unavailable(None)
    if transcript_version != active_transcript_version:
        # Obsolete version: the text the caller asked to rerun is not the usable one.
        raise transcript_unavailable(active_transcript_version)
    for stage in stages:
        if stage not in required_stages:
            raise _invalid_transition(
                {"stage": stage, "required_stages": list(required_stages)}
            )


def retry_run_action(existing_status: str | None) -> RunAction:
    """What a retry must do for one stage, given the current status of its run row.

    `None` (no run yet) → create; terminal failed/cancelled → reset to pending; active →
    reuse (idempotent no-op, the work is already queued); succeeded → conflict. An unknown
    status fails closed: it is corrupt data, never a state this decision may widen.
    """
    if existing_status is None:
        return "create"
    if existing_status in state_machine.ACTIVE_STAGE_STATES:
        return "reuse"
    if existing_status == "succeeded":
        return "conflict"
    if existing_status in RETRYABLE_RUN_STATES:
        return "reset"
    raise ValueError(f"unknown run status: {existing_status!r}")


def reupload_conflict(*, attempt_status: str | None, has_usable_transcript: bool) -> None:
    """Raise the typed `409` when a reupload is not allowed; silent when it is."""
    if has_usable_transcript:
        # DISENO §3.5: «409 if a transcript already exists» — the button must say
        # «retry analysis/index», never «upload again».
        raise _invalid_transition(
            {"current_status": attempt_status, "reason": "transcript_available"}
        )
    if attempt_status in ACTIVE_INGESTION_STATES:
        # `attempts_one_active` allows at most one live attempt per class.
        raise _invalid_transition({"current_status": attempt_status})


def stage_event_type(stage: str) -> str:
    try:
        return STAGE_EVENT_TYPE[stage]
    except KeyError:
        raise ValueError(f"unknown stage: {stage!r}") from None


# --- SQL ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class StageRunOutcome:
    """Flat values of one requested stage; safe to read after `commit()`."""

    stage: str
    run_id: uuid.UUID
    status: str


@dataclass(frozen=True)
class RetryOutcome:
    audio_id: uuid.UUID
    transcript_version: int
    runs: tuple[StageRunOutcome, ...]


@dataclass(frozen=True)
class ReuploadReservation:
    """Flat values of the new ephemeral session, mirroring `audios.Reservation`."""

    audio_id: uuid.UUID
    attempt_id: uuid.UUID
    upload_expires_at: datetime


async def usable_transcript_version(
    db: AsyncSession, *, user_id: uuid.UUID, audio: Audio
) -> int | None:
    """Active transcript version with a LIVE `transcripts` row, or None (fail closed).

    The tombstone (A2.7) clears `active_transcript_version` before purging rows, but any
    drift between pointer and rows must read as «no usable text», never as retryable.
    """
    version = audio.active_transcript_version
    if version is None:
        return None
    row = await db.scalar(
        select(Transcript.id).where(
            Transcript.user_id == user_id,
            Transcript.audio_id == audio.id,
            Transcript.version == version,
        )
    )
    return version if row is not None else None


async def latest_attempt(
    db: AsyncSession, *, user_id: uuid.UUID, audio_id: uuid.UUID
) -> IngestionAttempt | None:
    # Same «latest attempt» definition as the outbox dispatcher (`workers/dispatcher.py`):
    # `id DESC` only breaks `created_at` ties deterministically (UUIDv4 is not chronological).
    result = await db.execute(
        select(IngestionAttempt)
        .where(
            IngestionAttempt.user_id == user_id,
            IngestionAttempt.audio_id == audio_id,
        )
        .order_by(IngestionAttempt.created_at.desc(), IngestionAttempt.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _locked_run(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    audio_id: uuid.UUID,
    spec: RunSpec,
    transcript_version: int,
) -> ProcessingRun | None:
    """The run row of this (stage, version, config) under `FOR UPDATE`, or None.

    The row lock serializes concurrent retries of the same stage so the decision below
    (create/reset/reuse/conflict) is never evaluated against a row in flight. Only the row
    that matches the CURRENT versions is considered: an old transcript or config version is
    history and must never be reused as the answer of this request.
    """
    result = await db.execute(
        select(ProcessingRun)
        .where(
            ProcessingRun.user_id == user_id,
            ProcessingRun.audio_id == audio_id,
            ProcessingRun.stage == spec.stage,
            ProcessingRun.transcript_version == transcript_version,
            ProcessingRun.config_version == spec.config_version,
        )
        .with_for_update()
    )
    return result.scalar_one_or_none()


async def _emit_stage_event(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    audio_id: uuid.UUID,
    stage: str,
    transcript_version: int,
    run_id: uuid.UUID,
    cycle: int,
) -> None:
    """Enqueue one enabled, cleanup-gated event per run CYCLE (idempotent insert).

    The dedupe key carries the attempt counter so a re-retry of the same run (failed again
    after a first retry) gets a fresh event, while an exact duplicate of the same cycle is
    swallowed by the unique constraint instead of double-dispatching.
    """
    await db.execute(
        pg_insert(OutboxEvent)
        .values(
            user_id=user_id,
            type=stage_event_type(stage),
            resource_type="audio",
            resource_id=audio_id,
            resource_version=transcript_version,
            payload={},
            enabled=True,
            blocked_reason=None,
            dedupe_key=f"{run_id}:{cycle}",
        )
        # Infer the real unique index by columns (not by name) so the dedupe target stays
        # explicit and independent of identifier-naming details.
        .on_conflict_do_nothing(
            index_elements=["type", "resource_id", "resource_version", "dedupe_key"]
        )
    )


async def ensure_stage_run(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    audio_id: uuid.UUID,
    stage: str,
    transcript_version: int,
) -> StageRunOutcome:
    """Create or requeue exactly one pending run for this stage + emit its event.

    The run identity includes the current `RunSpec` versions, so a request after a
    transcript or configuration/prompt change creates a new pending row next to the old
    one; it never resets or reuses a run produced by another version. All of it runs inside
    the caller's transaction: a conflict on a LATER stage rolls back the runs already
    written, so a rejected retry never leaves half of its work behind.
    """
    spec = run_spec(stage)
    run = await _locked_run(
        db,
        user_id=user_id,
        audio_id=audio_id,
        spec=spec,
        transcript_version=transcript_version,
    )
    if run is None:
        inserted = await db.execute(
            pg_insert(ProcessingRun)
            .values(
                user_id=user_id,
                audio_id=audio_id,
                stage=spec.stage,
                transcript_version=transcript_version,
                config_version=spec.config_version,
                prompt_version=spec.prompt_version,
                status="pending",
            )
            # The unique index closes the create race (the loser inserts nothing and
            # classifies the winner's row). Infer it by columns, not by name: the
            # auto-generated constraint name
            # (`uq_processing_runs_audio_id_stage_transcript_version_config_version`) is
            # 67 bytes, over PostgreSQL's 63-byte identifier limit, so it is truncated on
            # disk and cannot be referenced literally. Columns match the unique key.
            .on_conflict_do_nothing(
                index_elements=["audio_id", "stage", "transcript_version", "config_version"]
            )
            .returning(ProcessingRun.id)
        )
        run_id = inserted.scalar_one_or_none()
        if run_id is not None:
            await _emit_stage_event(
                db,
                user_id=user_id,
                audio_id=audio_id,
                stage=stage,
                transcript_version=transcript_version,
                run_id=run_id,
                cycle=0,
            )
            return StageRunOutcome(stage=stage, run_id=run_id, status="pending")
        # The racer's row committed while we inserted: the truth is that row (fail closed).
        run = await _locked_run(
            db,
            user_id=user_id,
            audio_id=audio_id,
            spec=spec,
            transcript_version=transcript_version,
        )
        if run is None:
            raise _invalid_transition({"stage": stage, "reason": "run_lost"})
    action = retry_run_action(run.status)
    if action == "conflict":
        raise _invalid_transition({"stage": stage, "stage_status": run.status})
    if action == "reuse":
        # Idempotent 202: the work is already queued or running; no new run, no new event.
        return StageRunOutcome(stage=stage, run_id=run.id, status=run.status)
    # reset: conditional UPDATE decides the race honestly (H2 pattern of A2.7).
    result = await db.execute(
        update(ProcessingRun)
        .where(
            ProcessingRun.id == run.id,
            ProcessingRun.status.in_(RETRYABLE_RUN_STATES),
        )
        .execution_options(synchronize_session=False)
        .values(
            status="pending",
            error_code=None,
            finished_at=None,
            attempt_count=ProcessingRun.attempt_count + 1,
        )
        .returning(ProcessingRun.attempt_count)
    )
    cycle = result.scalar_one_or_none()
    if cycle is None:
        # Only reachable if the FOR UPDATE lock was bypassed; the row still decides.
        raise _invalid_transition({"stage": stage, "stage_status": "unknown"})
    await _emit_stage_event(
        db,
        user_id=user_id,
        audio_id=audio_id,
        stage=stage,
        transcript_version=transcript_version,
        run_id=run.id,
        cycle=cycle,
    )
    return StageRunOutcome(stage=stage, run_id=run.id, status="pending")


async def request_retry(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    audio: Audio,
    stages: Sequence[str],
    transcript_version: int,
) -> RetryOutcome:
    """`POST /audios/{id}/retry` over the persisted text; `ApiError` on any conflict."""
    active = await usable_transcript_version(db, user_id=user_id, audio=audio)
    validate_retry_stages(
        stages,
        transcript_version,
        active_transcript_version=active,
        required_stages=audio.required_stages,
    )
    latest = await latest_attempt(db, user_id=user_id, audio_id=audio.id)
    if latest is not None and state_machine.blocks_derived_stages(latest.status):
        # Cleanup is not verified yet: index/analysis must not start over half-deleted
        # temporaries (M2 cleanup gate, same authority as A2.6).
        raise _invalid_transition({"current_status": latest.status})
    # Take the `FOR UPDATE` row locks in a canonical (sorted) stage order: two concurrent
    # retries of the same stages in opposite order would otherwise lock the rows in opposite
    # order and PostgreSQL would resolve the cycle as a deadlock. Every retry now contends
    # for the SAME first row, so the second one just waits. The response below reports the
    # runs in the caller's order, which the client already knows.
    by_stage: dict[str, StageRunOutcome] = {}
    for stage in sorted(stages):
        by_stage[stage] = await ensure_stage_run(
            db,
            user_id=user_id,
            audio_id=audio.id,
            stage=stage,
            transcript_version=transcript_version,
        )
    # Flat values captured before the router's `commit()` (ORM attributes expire after it).
    return RetryOutcome(
        audio_id=audio.id,
        transcript_version=transcript_version,
        runs=tuple(by_stage[stage] for stage in stages),
    )


def run_is_current(
    *,
    stage: str,
    transcript_version: int,
    config_version: str,
    prompt_version: str | None,
    active_transcript_version: int | None,
) -> bool:
    """True only when the row matches the class's active text and the current versions."""
    spec = run_spec(stage)
    return (
        active_transcript_version == transcript_version
        and config_version == spec.config_version
        and prompt_version == spec.prompt_version
    )


@dataclass(frozen=True)
class RunHistoryEntry:
    """Flat snapshot of one durable run; safe after the caller's commit."""

    id: uuid.UUID
    stage: str
    transcript_version: int
    config_version: str
    prompt_version: str | None
    status: str
    attempt_count: int
    error_code: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    current: bool


async def run_history(
    db: AsyncSession, *, user_id: uuid.UUID, audio: Audio, limit: int = 100
) -> list[RunHistoryEntry]:
    """Tenant-scoped run ledger of one class, newest first (S3.4).

    Old rows are never deleted by a version bump: `current` distinguishes the run of the
    active transcript + current configuration from the historical ones that stay queryable.
    """
    if limit <= 0:
        raise ValueError("limit must be positive")
    result = await db.execute(
        select(ProcessingRun)
        .where(ProcessingRun.user_id == user_id, ProcessingRun.audio_id == audio.id)
        .order_by(ProcessingRun.created_at.desc(), ProcessingRun.id.desc())
        .limit(limit)
    )
    return [
        RunHistoryEntry(
            id=run.id,
            stage=run.stage,
            transcript_version=run.transcript_version,
            config_version=run.config_version,
            prompt_version=run.prompt_version,
            status=run.status,
            attempt_count=run.attempt_count,
            error_code=run.error_code,
            started_at=run.started_at,
            finished_at=run.finished_at,
            created_at=run.created_at,
            current=run_is_current(
                stage=run.stage,
                transcript_version=run.transcript_version,
                config_version=run.config_version,
                prompt_version=run.prompt_version,
                active_transcript_version=audio.active_transcript_version,
            ),
        )
        for run in result.scalars()
    ]


async def ensure_reupload_allowed(
    db: AsyncSession, *, user_id: uuid.UUID, audio: Audio
) -> IngestionAttempt:
    """Read-only reupload guards; returns the attempt whose consent snapshot is reused."""
    active = await usable_transcript_version(db, user_id=user_id, audio=audio)
    latest = await latest_attempt(db, user_id=user_id, audio_id=audio.id)
    # The active-attempt guard is an EXISTENCE query, not the status of the "latest" row:
    # `created_at` is transaction-scoped (`now()`), so two attempts can tie and the random
    # UUID `id DESC` tiebreak could hide the live row behind a terminal one. `attempts_one_active`
    # allows at most one live attempt, so finding ANY is both necessary and sufficient.
    live_status = await db.scalar(
        select(IngestionAttempt.status)
        .where(
            IngestionAttempt.user_id == user_id,
            IngestionAttempt.audio_id == audio.id,
            IngestionAttempt.status.in_(ACTIVE_INGESTION_STATES),
        )
        .limit(1)
    )
    if live_status is None and latest is not None:
        current_status: str | None = latest.status
    else:
        current_status = live_status
    reupload_conflict(
        attempt_status=current_status,
        has_usable_transcript=active is not None,
    )
    if latest is None:
        # An audio without any attempt has no consent snapshot to reuse; fail closed.
        raise _invalid_transition({"current_status": None})
    return latest


async def open_reupload(
    db: AsyncSession, *, user_id: uuid.UUID, audio: Audio, previous: IngestionAttempt
) -> ReuploadReservation:
    """Open a new `awaiting_upload` attempt on the SAME audio (same ingestion identity).

    Consent is copied from the previous attempt (the student already gave it for this
    class; reupload re-asks nothing and records nothing new). `fencing_token` restarts at
    1 because it is scoped per attempt. `sha256` goes back to NULL so the content identity
    is unknown again — the next `PUT` recomputes it and decides dedupe on its own.
    """
    now = datetime.now(UTC)
    upload_expires_at = now + timedelta(minutes=settings.upload_ttl_minutes)
    attempt = IngestionAttempt(
        user_id=user_id,
        audio_id=audio.id,
        privacy_notice_version=previous.privacy_notice_version,
        cloud_processing_accepted_at=previous.cloud_processing_accepted_at,
        third_party_voice_acknowledged_at=previous.third_party_voice_acknowledged_at,
        declared_providers=previous.declared_providers,
        status="awaiting_upload",
        owner_instance=UNASSIGNED_OWNER,
        fencing_token=1,
        upload_expires_at=upload_expires_at,
    )
    db.add(attempt)
    audio.sha256 = None
    await db.flush()
    # Flat values captured before the router's `commit()`.
    return ReuploadReservation(
        audio_id=audio.id, attempt_id=attempt.id, upload_expires_at=upload_expires_at
    )

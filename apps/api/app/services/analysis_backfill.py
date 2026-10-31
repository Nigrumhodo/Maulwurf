"""Explicit analysis backfill for F1 classes (S3.1, ESPECIFICACION.md M2/M4).

S2 classes are transcribed with ``required_stages=['index']``: the ``analyze`` stage stays
``not_requested`` because analysis arrives in F2. This module is the explicit backfill that
upgrades those classes to F2 and queues one analysis run over their **active persisted
transcript**.

Non-negotiable rules implemented here:

- **Persisted text only.** The backfill never opens an ingestion attempt, never reads a
  file, and never calls the ingestion service or Riva. It only needs the class, its active
  transcript version, the latest attempt's cleanup evidence and the derived stage state.
  The worker that consumes the queued event is the only component allowed to read the
  transcript text.
- **No reupload and no retranscription.** Nothing in this module inserts or updates
  ``ingestion_attempts``, ``audios.sha256``, ``original_bytes`` or a transcript. A class
  without a live transcript is skipped, never sent back through ASR.
- **One run authority.** Queueing reuses ``runs.ensure_stage_run``, the same code path as
  ``POST /audios/{id}/retry``: the unique ``processing_runs`` key and the cleanup-gated
  ``analyze_requested`` outbox event stay single-sourced.
- **Idempotent and tenant-scoped.** Every query filters ``user_id``; a second execution
  finds the pending run and does not duplicate it. Rows are only queued while the latest
  attempt has ``cleanup_status='verified'``.

Outcomes are reported as ``BackfillStatus`` values. The decision is taken in two steps and
every non-writing outcome is resolved **before** any row is mutated: ``already_analyzed``,
``already_queued`` and ``unknown_run_state`` leave ``required_stages`` untouched, and
``already_f2_no_run`` marks a class that already owes analysis but has no run row (its entry
point is ``POST /audios/{id}/retry``). Only the branch that actually calls
``ensure_stage_run`` promotes ``required_stages`` from F1 to F2, so a dry run or a no-op pass
never persists the upgrade.

The analyzer (L3.6) and the ``tasks``/``task_sources`` tables (A3.1) are not part of this
module. Until they land, the existing ``analyze`` ARQ function is still the S2 no-op, so this
ticket delivers the explicit queue and its evidence, not extraction output.
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Audio, ProcessingRun, User
from app.services import state_machine
from app.services.runs import (
    ANALYSIS_STAGE,
    ensure_stage_run,
    latest_attempt,
    run_spec,
    usable_transcript_version,
)

# The analysis prompt/configuration version is owned by L3.1/L3.6; S3.4 records it in the
# run row and derives the run's config version from the prompt version.
ANALYSIS_CONFIG_VERSION = run_spec(ANALYSIS_STAGE).config_version
# Statuses the backfill may requeue, mirroring `runs.RETRYABLE_RUN_STATES`.
REQUEUEABLE_RUN_STATES = ("failed", "cancelled")


class BackfillStatus(StrEnum):
    """Outcome of one class. Values are stable CLI/API evidence, not user-facing copy."""

    QUEUED = "queued"
    WOULD_QUEUE = "would_queue"
    ALREADY_QUEUED = "already_queued"
    ALREADY_ANALYZED = "already_analyzed"
    # F2 class that already owes analysis but has no analyze run row: `retry` is its entry
    # point, so the backfill neither queues it nor rewrites its stage list.
    ALREADY_F2_NO_RUN = "already_f2_no_run"
    UNKNOWN_RUN_STATE = "unknown_run_state"
    NO_TRANSCRIPT = "no_transcript"
    CLEANUP_PENDING = "cleanup_pending"
    OWNER_REVOKED = "owner_revoked"
    NOT_SELECTED = "not_selected"
    NOT_FOUND = "not_found"


@dataclass(frozen=True)
class BackfillItem:
    """One class decision; contains IDs and statuses only, never text or paths."""

    audio_id: uuid.UUID
    status: BackfillStatus
    transcript_version: int | None = None
    run_id: uuid.UUID | None = None
    run_status: str | None = None


@dataclass(frozen=True)
class BackfillReport:
    """Result of one bounded backfill pass. The caller owns the transaction and commit."""

    items: tuple[BackfillItem, ...]
    truncated: bool
    applied: bool

    @property
    def counts(self) -> Counter[BackfillStatus]:
        return Counter(item.status for item in self.items)


def analysis_required(required_stages: Sequence[str]) -> bool:
    """True when the class was already upgraded to F2 (analysis is owed)."""
    return ANALYSIS_STAGE in required_stages


def with_analysis_stage(required_stages: Sequence[str]) -> list[str]:
    """Return the stage list with ``analyze`` once, preserving the existing order."""
    if analysis_required(required_stages):
        return list(required_stages)
    return [*required_stages, ANALYSIS_STAGE]


def classify_existing_run(
    *,
    analysis_already_required: bool,
    existing_status: str | None,
    has_previous_run: bool = False,
) -> BackfillStatus | None:
    """Terminal outcome for this pass, or ``None`` when analysis must be queued.

    Called before the caller mutates anything: an already analyzed, already queued or
    corrupt run must never promote an F1 class to F2. A previous run under another
    transcript/config version means reanalysis is necessary (S3.4), so it queues a new run
    even for an F2 class. Only an F2 class with no analysis run at all keeps ``retry`` as
    its entry point.
    """
    if existing_status is None:
        if has_previous_run:
            return None
        return BackfillStatus.ALREADY_F2_NO_RUN if analysis_already_required else None
    if existing_status == "succeeded":
        return BackfillStatus.ALREADY_ANALYZED
    if existing_status in state_machine.ACTIVE_STAGE_STATES:
        return BackfillStatus.ALREADY_QUEUED
    if existing_status not in REQUEUEABLE_RUN_STATES:
        return BackfillStatus.UNKNOWN_RUN_STATE
    return None


def analysis_backfill_available(
    *,
    has_usable_transcript: bool,
    attempt_status: str | None,
    cleanup_status: str | None,
    analyze_state: str,
    has_current_run: bool,
) -> bool:
    """Whether the class page may offer «backfill/re-analysis» right now.

    F1 classes read ``not_requested`` and terminal analysis failures/cancellations may be
    retried. An active or successful analysis of the CURRENT transcript/config versions is
    not offered again. A class with no run for those versions (a version changed, S3.4) is
    offered even when its stage list already owes analysis. Without verified cleanup there
    is nothing safe to queue (fail closed).
    """
    if not has_usable_transcript:
        return False
    if attempt_status != "succeeded" or cleanup_status != "verified":
        return False
    if not has_current_run:
        return True
    return analyze_state == "not_requested" or analyze_state in REQUEUEABLE_RUN_STATES


async def select_candidate_ids(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    audio_ids: Sequence[uuid.UUID] | None = None,
    subject_id: uuid.UUID | None = None,
    class_date_from: date | None = None,
    class_date_to: date | None = None,
    limit: int,
) -> tuple[list[uuid.UUID], bool]:
    """Live classes of the tenant that may have a persisted transcript, in stable order.

    Returns ``(ids, truncated)``. ``limit + 1`` rows are read only to report truncation;
    the caller receives at most ``limit`` ids. Empty explicit ``audio_ids`` means "nothing
    requested", not "all classes".
    """
    if limit <= 0:
        raise ValueError("limit must be positive")
    if audio_ids is not None and not audio_ids:
        return [], False
    statement = select(Audio.id).where(
        Audio.user_id == user_id,
        Audio.deleted_at.is_(None),
        Audio.active_transcript_version.is_not(None),
    )
    if audio_ids is not None:
        statement = statement.where(Audio.id.in_(audio_ids))
    if subject_id is not None:
        statement = statement.where(Audio.subject_id == subject_id)
    if class_date_from is not None:
        statement = statement.where(Audio.class_date >= class_date_from)
    if class_date_to is not None:
        statement = statement.where(Audio.class_date <= class_date_to)
    statement = statement.order_by(
        Audio.class_date, Audio.created_at, Audio.id
    ).limit(limit + 1)
    ids = list((await db.scalars(statement)).all())
    return ids[:limit], len(ids) > limit


async def _locked_audio(
    db: AsyncSession, *, user_id: uuid.UUID, audio_id: uuid.UUID
) -> Audio | None:
    """One owned class under ``FOR UPDATE`` so two backfills cannot race its stage list."""
    return (
        await db.execute(
            select(Audio)
            .where(Audio.user_id == user_id, Audio.id == audio_id)
            .with_for_update()
        )
    ).scalar_one_or_none()


async def _owner_status(db: AsyncSession, *, user_id: uuid.UUID) -> str | None:
    return (
        await db.execute(select(User.status).where(User.id == user_id))
    ).scalar_one_or_none()


async def _existing_analysis_run(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    audio_id: uuid.UUID,
    transcript_version: int,
) -> ProcessingRun | None:
    return (
        await db.execute(
            select(ProcessingRun).where(
                ProcessingRun.user_id == user_id,
                ProcessingRun.audio_id == audio_id,
                ProcessingRun.stage == ANALYSIS_STAGE,
                ProcessingRun.transcript_version == transcript_version,
                ProcessingRun.config_version == ANALYSIS_CONFIG_VERSION,
            )
        )
    ).scalar_one_or_none()


async def _any_analysis_run(
    db: AsyncSession, *, user_id: uuid.UUID, audio_id: uuid.UUID
) -> bool:
    """Whether the class has any analysis run, regardless of transcript/config version."""
    found = await db.scalar(
        select(ProcessingRun.id)
        .where(
            ProcessingRun.user_id == user_id,
            ProcessingRun.audio_id == audio_id,
            ProcessingRun.stage == ANALYSIS_STAGE,
        )
        .limit(1)
    )
    return found is not None


async def backfill_audio(
    db: AsyncSession, *, user_id: uuid.UUID, audio_id: uuid.UUID, apply: bool
) -> BackfillItem:
    """Decide and, only when a run is actually queued, upgrade + queue one F1 class.

    No commit happens here: the request/script owns the transaction. The row lock makes the
    decision stable while ``ensure_stage_run`` serializes the run's unique key.
    """
    audio = await _locked_audio(db, user_id=user_id, audio_id=audio_id)
    if audio is None or audio.deleted_at is not None:
        return BackfillItem(audio_id=audio_id, status=BackfillStatus.NOT_FOUND)
    if await _owner_status(db, user_id=user_id) != "active":
        return BackfillItem(audio_id=audio_id, status=BackfillStatus.OWNER_REVOKED)
    # Only the live transcript pointer decides; a stale pointer is «no usable text» and
    # must never trigger a reupload or an ASR run.
    version = await usable_transcript_version(db, user_id=user_id, audio=audio)
    if version is None:
        return BackfillItem(audio_id=audio_id, status=BackfillStatus.NO_TRANSCRIPT)
    attempt = await latest_attempt(db, user_id=user_id, audio_id=audio_id)
    if (
        attempt is None
        or attempt.status != "succeeded"
        or attempt.cleanup_status != "verified"
    ):
        # The cleanup gate is safety-critical: without verified cleanup the audio path is
        # not proven closed, so no text-derived work is queued for this class.
        return BackfillItem(audio_id=audio_id, status=BackfillStatus.CLEANUP_PENDING)

    required = analysis_required(audio.required_stages)
    existing = await _existing_analysis_run(
        db, user_id=user_id, audio_id=audio_id, transcript_version=version
    )
    previous = existing is not None or await _any_analysis_run(
        db, user_id=user_id, audio_id=audio_id
    )
    decision = classify_existing_run(
        analysis_already_required=required,
        existing_status=existing.status if existing is not None else None,
        has_previous_run=previous,
    )
    if decision is not None:
        # Every non-writing outcome is resolved here, before any row is touched.
        return BackfillItem(
            audio_id=audio_id,
            status=decision,
            transcript_version=version,
            run_id=existing.id if existing is not None else None,
            run_status=existing.status if existing is not None else None,
        )
    if not apply:
        return BackfillItem(
            audio_id=audio_id,
            status=BackfillStatus.WOULD_QUEUE,
            transcript_version=version,
        )
    # F1 → F2 is exactly the explicit upgrade the spec assigns to the backfill, and it only
    # happens on the branch that really queues: a no-op apply pass changes no row.
    if not required:
        audio.required_stages = with_analysis_stage(audio.required_stages)
    outcome = await ensure_stage_run(
        db,
        user_id=user_id,
        audio_id=audio_id,
        stage=ANALYSIS_STAGE,
        transcript_version=version,
    )
    return BackfillItem(
        audio_id=audio_id,
        status=BackfillStatus.QUEUED,
        transcript_version=version,
        run_id=outcome.run_id,
        run_status=outcome.status,
    )


async def backfill_classes(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    audio_ids: Sequence[uuid.UUID] | None = None,
    subject_id: uuid.UUID | None = None,
    class_date_from: date | None = None,
    class_date_to: date | None = None,
    limit: int = 500,
    apply: bool = False,
) -> BackfillReport:
    """One bounded, tenant-scoped backfill pass. Dry-run unless ``apply`` is true."""
    selected, truncated = await select_candidate_ids(
        db,
        user_id=user_id,
        audio_ids=audio_ids,
        subject_id=subject_id,
        class_date_from=class_date_from,
        class_date_to=class_date_to,
        limit=limit,
    )
    items = [
        await backfill_audio(db, user_id=user_id, audio_id=audio_id, apply=apply)
        for audio_id in selected
    ]
    found = {item.audio_id for item in items}
    if audio_ids is not None:
        # Explicit ids that the candidate query did not select may still belong to the
        # tenant (tombstoned, without a live transcript, filtered out or beyond the limit);
        # only an id absent from the tenant is reported as missing, so the CLI never
        # mislabels a real class as unknown.
        missing = sorted(set(audio_ids) - found)
        existing: set[uuid.UUID] = set()
        if missing:
            existing = set(
                (
                    await db.scalars(
                        select(Audio.id).where(
                            Audio.user_id == user_id, Audio.id.in_(missing)
                        )
                    )
                ).all()
            )
        for audio_id in missing:
            status = (
                BackfillStatus.NOT_SELECTED
                if audio_id in existing
                else BackfillStatus.NOT_FOUND
            )
            items.append(BackfillItem(audio_id=audio_id, status=status))
    return BackfillReport(items=tuple(items), truncated=truncated, applied=apply)

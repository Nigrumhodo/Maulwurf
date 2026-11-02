"""ARQ `analyze(audio_id)` (L3.6).

The payload is the class id only. The delivery claims the `analyze` run for the active
transcript and the current prompt, reads the persisted text, and publishes one batch.
Nothing is written until every window has returned. A permanent error is recorded and
not raised, so ARQ does not spend a retry on it; a transient error is raised as `Retry`
with the S3.6 delay. `LLMNotConfigured` fails the run and writes no tasks.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final

from arq.worker import Retry
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import (
    bind_cross_tenant,
    bind_user,
    clear_user,
    reset_cross_tenant,
    session_scope,
)
from app.models import Audio, ProcessingRun, Segment, Subject, Transcript, User
from app.services.analysis import (
    ClassContext,
    ExtractionModel,
    StreamingExtractionModel,
    analyze_transcript,
    apply_task_writes,
    load_existing_proposals,
    plan_task_writes,
    revision_task_ids,
)
from app.services.analysis_validation import load_class_transcript
from app.services.chunking import SegmentInput
from app.services.index_job import (
    LEASE,
    RunClaim,
    RunFenced,
    RunFenceOutcome,
    finish_run,
    require_run_fence,
)
from app.services.llm import HttpChatProvider
from app.services.runs import latest_attempt, run_spec
from app.workers.retry import retry_decision

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
ModelFactory = Callable[[], ExtractionModel]

SESSIONS_KEY: Final = "analyze_session_factory"
MODELS_KEY: Final = "analyze_model_factory"
NOW_KEY: Final = "analyze_now"
JOB_TIMEOUT_S: Final = 600


class AnalyzeSkip(StrEnum):
    """Why a delivery writes nothing. Logged with ids only."""

    TOMBSTONED = "tombstoned"
    OWNER_REVOKED = "owner_revoked"
    NO_TRANSCRIPT = "no_transcript"
    CLEANUP_PENDING = "cleanup_pending"
    ALREADY_ANALYZED = "already_analyzed"
    IN_PROGRESS = "in_progress"


@dataclass(frozen=True)
class _Claim:
    user_id: uuid.UUID
    audio_id: uuid.UUID
    subject_id: uuid.UUID
    transcript_version: int
    run: RunClaim


async def analyze(ctx: dict[str, Any], audio_id: str) -> None:
    """ARQ entry. Permanent failures are swallowed; transient ones become `Retry`."""
    job_try = int(ctx.get("job_try", 1))
    try:
        await run_analyze(
            _sessions(ctx),
            _models(ctx),
            uuid.UUID(audio_id),
            job_try=job_try,
            now=_now(ctx),
        )
    except Exception as exc:
        decision = retry_decision(exc, job_try=job_try)
        if decision.retries and decision.delay_seconds is not None:
            logger.warning(
                "analyze.retry_scheduled audio_id=%s attempt=%d error=%s delay=%.2f",
                audio_id,
                job_try,
                type(exc).__name__,
                decision.delay_seconds,
            )
            raise Retry(defer=decision.delay_seconds) from exc
        logger.warning(
            "analyze.failed audio_id=%s attempt=%d error=%s",
            audio_id,
            job_try,
            type(exc).__name__,
        )
    finally:
        clear_user()


async def run_analyze(
    sessions: SessionFactory,
    models: ModelFactory,
    audio_id: uuid.UUID,
    *,
    job_try: int,
    now: datetime | None = None,
) -> None:
    """Claim, extract, publish once. Re-raises after recording a failure."""
    clock = now or datetime.now(UTC)
    claimed = await _claim_cross_tenant(sessions, audio_id, clock)
    if isinstance(claimed, AnalyzeSkip):
        logger.info("analyze.noop reason=%s audio_id=%s", claimed.value, audio_id)
        return
    bind_user(claimed.user_id)
    try:
        await _extract_and_publish(sessions, models, claimed, clock)
    except RunFenced:
        logger.info("analyze.noop reason=run_fenced run_id=%s", claimed.run.run_id)
    except Exception as exc:
        await _record(sessions, claimed.run, retry_decision(exc, job_try=job_try).error_code)
        raise


def _sessions(ctx: dict[str, Any]) -> SessionFactory:
    factory: SessionFactory = ctx.get(SESSIONS_KEY, session_scope)
    return factory


def _models(ctx: dict[str, Any]) -> ModelFactory:
    factory: ModelFactory | None = ctx.get(MODELS_KEY)
    if factory is not None:
        return factory
    return lambda: StreamingExtractionModel(HttpChatProvider.from_settings(settings))


def _now(ctx: dict[str, Any]) -> datetime | None:
    clock = ctx.get(NOW_KEY)
    return clock if isinstance(clock, datetime) else None


async def _claim_cross_tenant(
    sessions: SessionFactory, audio_id: uuid.UUID, now: datetime
) -> _Claim | AnalyzeSkip:
    token = bind_cross_tenant()
    try:
        async with sessions() as db:
            return await _claim(db, audio_id, now)
    finally:
        reset_cross_tenant(token)


async def _claim(db: AsyncSession, audio_id: uuid.UUID, now: datetime) -> _Claim | AnalyzeSkip:
    row = (
        await db.execute(
            select(
                Audio.user_id,
                Audio.deleted_at,
                Audio.subject_id,
                Audio.active_transcript_version,
                User.status,
            )
            .join(User, User.id == Audio.user_id)
            .where(Audio.id == audio_id)
        )
    ).one_or_none()
    if row is None or row.deleted_at is not None:
        return AnalyzeSkip.TOMBSTONED
    if row.status != "active":
        return AnalyzeSkip.OWNER_REVOKED
    version = row.active_transcript_version
    if not isinstance(version, int):
        return AnalyzeSkip.NO_TRANSCRIPT
    attempt = await latest_attempt(db, user_id=row.user_id, audio_id=audio_id)
    if attempt is None or attempt.cleanup_status != "verified":
        return AnalyzeSkip.CLEANUP_PENDING
    spec = run_spec("analyze")
    await db.execute(
        pg_insert(ProcessingRun)
        .values(
            user_id=row.user_id,
            audio_id=audio_id,
            stage=spec.stage,
            transcript_version=version,
            config_version=spec.config_version,
            prompt_version=spec.prompt_version,
            status="pending",
        )
        .on_conflict_do_nothing(
            index_elements=["audio_id", "stage", "transcript_version", "config_version"]
        )
    )
    run = (
        await db.execute(
            select(ProcessingRun)
            .where(
                ProcessingRun.user_id == row.user_id,
                ProcessingRun.audio_id == audio_id,
                ProcessingRun.stage == spec.stage,
                ProcessingRun.transcript_version == version,
                ProcessingRun.config_version == spec.config_version,
            )
            .with_for_update()
        )
    ).scalar_one()
    if run.status == "succeeded":
        return AnalyzeSkip.ALREADY_ANALYZED
    if run.status == "running" and run.started_at is not None and run.started_at >= now - LEASE:
        return AnalyzeSkip.IN_PROGRESS
    run.status = "running"
    run.started_at = now
    run.finished_at = None
    run.error_code = None
    run.attempt_count += 1
    run.fencing_token += 1
    if run.prompt_version is None:
        run.prompt_version = spec.prompt_version
    return _Claim(
        user_id=row.user_id,
        audio_id=audio_id,
        subject_id=row.subject_id,
        transcript_version=version,
        run=RunClaim(run_id=run.id, fencing_token=run.fencing_token),
    )


async def _extract_and_publish(
    sessions: SessionFactory,
    models: ModelFactory,
    claimed: _Claim,
    now: datetime,
) -> None:
    async with sessions() as db:
        context = await _load_context(db, claimed)
    batch = await analyze_transcript(context, models(), now=now)
    async with sessions() as db:
        await require_run_fence(
            db, run_id=claimed.run.run_id, fencing_token=claimed.run.fencing_token
        )
        existing = await load_existing_proposals(
            db, user_id=claimed.user_id, audio_id=claimed.audio_id
        )
        revised = await revision_task_ids(
            db, user_id=claimed.user_id, run_id=claimed.run.run_id
        )
        writes = plan_task_writes(claimed.run.run_id, batch.proposals, existing, revised)
        await apply_task_writes(
            db,
            user_id=claimed.user_id,
            audio_id=claimed.audio_id,
            subject_id=claimed.subject_id,
            transcript_id=context.view.transcript_id,
            transcript_version=claimed.transcript_version,
            run_id=claimed.run.run_id,
            writes=writes,
        )
        outcome = await finish_run(
            db,
            run_id=claimed.run.run_id,
            fencing_token=claimed.run.fencing_token,
            status="succeeded",
            error_code=None,
        )
        if outcome is not RunFenceOutcome.APPLIED:
            raise RunFenced(f"run {claimed.run.run_id} lost the fence before publish")


async def _load_context(db: AsyncSession, claimed: _Claim) -> ClassContext:
    audio = (
        await db.execute(
            select(Audio).where(Audio.user_id == claimed.user_id, Audio.id == claimed.audio_id)
        )
    ).scalar_one()
    subject = (
        await db.execute(
            select(Subject.name).where(
                Subject.user_id == claimed.user_id, Subject.id == claimed.subject_id
            )
        )
    ).scalar_one()
    transcript = (
        await db.execute(
            select(Transcript).where(
                Transcript.user_id == claimed.user_id,
                Transcript.audio_id == claimed.audio_id,
                Transcript.version == claimed.transcript_version,
            )
        )
    ).scalar_one()
    segments = (
        await db.execute(
            select(Segment)
            .where(
                Segment.user_id == claimed.user_id,
                Segment.transcript_id == transcript.id,
            )
            .order_by(Segment.ordinal)
        )
    ).scalars().all()
    view = await load_class_transcript(
        db,
        user_id=claimed.user_id,
        audio_id=claimed.audio_id,
        transcript_version=claimed.transcript_version,
    )
    raw_teacher = audio.teacher
    teacher = raw_teacher if isinstance(raw_teacher, str) and raw_teacher.strip() else "unknown"
    return ClassContext(
        class_date=audio.class_date,
        class_timezone=audio.class_timezone,
        subject=subject,
        teacher=teacher,
        transcript_text=transcript.text,
        segments=tuple(
            SegmentInput(
                id=row.id,
                ordinal=row.ordinal,
                text=row.text,
                char_start=row.char_start,
                char_end=row.char_end,
                t_start=row.t_start,
                t_end=row.t_end,
            )
            for row in segments
        ),
        view=view,
    )


async def _record(sessions: SessionFactory, claim: RunClaim, error_code: str) -> None:
    try:
        async with sessions() as db:
            outcome = await finish_run(
                db,
                run_id=claim.run_id,
                fencing_token=claim.fencing_token,
                status="failed",
                error_code=error_code,
            )
        if outcome is RunFenceOutcome.FENCED:
            logger.info("analyze.noop reason=run_fenced run_id=%s", claim.run_id)
    except Exception as exc:
        logger.warning(
            "analyze.record_failed run_id=%s error=%s", claim.run_id, type(exc).__name__
        )

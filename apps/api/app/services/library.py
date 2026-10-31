"""Tenant-scoped reads for the class library and progress stream (A2.9, S2.md §A2.9).

All SQL lives here (ADR-0006): the router only resolves ownership and mounts the response.
Listing and detail derive the per-stage state and the honest aggregate through
`state_machine`, so nothing is stored twice and `not_requested` never reads as success. No
query in this module may drop the `user_id` filter.

The progress generator (`stream_progress`) lives here to keep the import graph acyclic: the
pure snapshot/encoding helpers are in `services/progress.py`, and this module imports them.
"""

import asyncio
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import bind_user
from app.models import (
    Audio,
    IngestionAttempt,
    ProcessingRun,
    Segment,
    Subject,
    Transcript,
)
from app.services import progress as progress_service
from app.services import runs as runs_service
from app.services import state_machine
from app.services.exports import srt_exportable
from app.services.progress import ProgressSnapshot
from app.services.tenant import get_owned


@dataclass(frozen=True)
class SubjectRef:
    id: uuid.UUID
    name: str
    color: str


@dataclass(frozen=True)
class AttemptSummary:
    attempt_id: uuid.UUID
    status: str
    received_bytes: int
    fragments_done: int | None
    fragments_total: int | None
    cleanup_status: str


@dataclass(frozen=True)
class AudioSummary:
    id: uuid.UUID
    subject: SubjectRef
    title: str | None
    class_date: date
    class_timezone: str
    language_code: str
    duration_seconds: float | None
    created_at: datetime
    attempt: AttemptSummary | None
    stages: dict[str, str]
    aggregate_status: str


@dataclass(frozen=True)
class TranscriptInfo:
    version: int
    timestamp_precision: str
    char_count: int


@dataclass(frozen=True)
class AudioDetail:
    summary: AudioSummary
    transcript: TranscriptInfo | None
    srt_exportable: bool


def _attempt_summary(attempt: IngestionAttempt | None) -> AttemptSummary | None:
    if attempt is None:
        return None
    return AttemptSummary(
        attempt_id=attempt.id,
        status=attempt.status,
        received_bytes=attempt.received_bytes,
        fragments_done=attempt.fragments_done,
        fragments_total=attempt.fragments_total,
        cleanup_status=attempt.cleanup_status,
    )


def _derive_stages(
    audio: Audio, runs: list[ProcessingRun], *, usable_version: int | None
) -> dict[str, str]:
    """Per-stage state from the persisted runs; `usable_version` scopes obsolete rows out."""
    return {
        stage: state_machine.stage_status(
            [run for run in runs if run.stage == stage],
            required=stage in audio.required_stages,
            active_transcript_version=usable_version,
        )
        for stage in state_machine.STAGES
    }


def _aggregate(
    audio: Audio,
    attempt: IngestionAttempt | None,
    stages: dict[str, str],
    *,
    usable_version: int | None,
) -> str:
    return state_machine.aggregate_status(
        attempt_status=attempt.status if attempt is not None else None,
        has_transcript=usable_version is not None,
        required_stages=audio.required_stages,
        stages=stages,
    )


async def _latest_attempts(
    db: AsyncSession, *, user_id: uuid.UUID, audio_ids: list[uuid.UUID]
) -> dict[uuid.UUID, IngestionAttempt]:
    """Latest attempt per audio; same `created_at DESC, id DESC` rule as `runs.latest_attempt`."""
    if not audio_ids:
        return {}
    result = await db.execute(
        select(IngestionAttempt)
        .where(IngestionAttempt.user_id == user_id, IngestionAttempt.audio_id.in_(audio_ids))
        .order_by(
            IngestionAttempt.audio_id,
            IngestionAttempt.created_at.desc(),
            IngestionAttempt.id.desc(),
        )
        .distinct(IngestionAttempt.audio_id)
    )
    return {attempt.audio_id: attempt for attempt in result.scalars()}


async def _runs_by_audio(
    db: AsyncSession, *, user_id: uuid.UUID, audio_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[ProcessingRun]]:
    if not audio_ids:
        return {}
    result = await db.execute(
        select(ProcessingRun)
        .where(ProcessingRun.user_id == user_id, ProcessingRun.audio_id.in_(audio_ids))
        .order_by(ProcessingRun.created_at)
    )
    grouped: dict[uuid.UUID, list[ProcessingRun]] = {}
    for run in result.scalars():
        grouped.setdefault(run.audio_id, []).append(run)
    return grouped


async def _usable_versions(
    db: AsyncSession, *, user_id: uuid.UUID, audios: list[Audio]
) -> dict[uuid.UUID, int]:
    """Active version per audio that still has a LIVE `transcripts` row (fail closed)."""
    pairs = [
        (audio.id, audio.active_transcript_version)
        for audio in audios
        if audio.active_transcript_version is not None
    ]
    if not pairs:
        return {}
    audio_ids = [audio_id for audio_id, _ in pairs]
    result = await db.execute(
        select(Transcript.audio_id, Transcript.version).where(
            Transcript.user_id == user_id, Transcript.audio_id.in_(audio_ids)
        )
    )
    live = {(row[0], row[1]) for row in result.all()}
    return {audio_id: version for audio_id, version in pairs if (audio_id, version) in live}


async def _subjects(
    db: AsyncSession, *, user_id: uuid.UUID, subject_ids: list[uuid.UUID]
) -> dict[uuid.UUID, SubjectRef]:
    if not subject_ids:
        return {}
    result = await db.execute(
        select(Subject).where(Subject.user_id == user_id, Subject.id.in_(subject_ids))
    )
    return {
        subject.id: SubjectRef(id=subject.id, name=subject.name, color=subject.color)
        for subject in result.scalars()
    }


async def list_audios(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    subject_id: uuid.UUID | None = None,
    limit: int = 50,
) -> list[AudioSummary]:
    """Live classes of the tenant, newest first; `subject_id` narrows the view.

    A foreign or unknown `subject_id` yields an empty list: the filter never reveals whether
    the subject exists in another tenant (ADR-0006).
    """
    statement = (
        select(Audio)
        .where(Audio.user_id == user_id, Audio.deleted_at.is_(None))
        .order_by(Audio.class_date.desc(), Audio.created_at.desc())
        .limit(limit)
    )
    if subject_id is not None:
        statement = statement.where(Audio.subject_id == subject_id)
    audios = list((await db.scalars(statement)).all())
    if not audios:
        return []
    audio_ids = [audio.id for audio in audios]
    attempts = await _latest_attempts(db, user_id=user_id, audio_ids=audio_ids)
    runs = await _runs_by_audio(db, user_id=user_id, audio_ids=audio_ids)
    versions = await _usable_versions(db, user_id=user_id, audios=audios)
    subjects = await _subjects(
        db, user_id=user_id, subject_ids=list({audio.subject_id for audio in audios})
    )
    summaries = []
    for audio in audios:
        usable = versions.get(audio.id)
        attempt = attempts.get(audio.id)
        stages = _derive_stages(audio, runs.get(audio.id, []), usable_version=usable)
        summaries.append(
            AudioSummary(
                id=audio.id,
                subject=subjects[audio.subject_id],
                title=audio.title,
                class_date=audio.class_date,
                class_timezone=audio.class_timezone,
                language_code=audio.language_code,
                duration_seconds=audio.duration_seconds,
                created_at=audio.created_at,
                attempt=_attempt_summary(attempt),
                stages=stages,
                aggregate_status=_aggregate(
                    audio, attempt, stages, usable_version=usable
                ),
            )
        )
    return summaries


async def get_audio_detail(
    db: AsyncSession, *, user_id: uuid.UUID, audio: Audio
) -> AudioDetail:
    """Full detail of one owned, live class: per-stage states, aggregate and transcript info."""
    usable = await runs_service.usable_transcript_version(db, user_id=user_id, audio=audio)
    transcript_info: TranscriptInfo | None = None
    exportable = False
    if usable is not None:
        transcript = (
            await db.execute(
                select(Transcript).where(
                    Transcript.user_id == user_id,
                    Transcript.audio_id == audio.id,
                    Transcript.version == usable,
                )
            )
        ).scalar_one_or_none()
        if transcript is not None:
            transcript_info = TranscriptInfo(
                version=transcript.version,
                timestamp_precision=transcript.timestamp_precision,
                char_count=transcript.char_count,
            )
            segments = await segments_for(db, user_id=user_id, transcript_id=transcript.id)
            exportable = srt_exportable(
                timestamp_precision=transcript.timestamp_precision,
                segments=segments,
                duration_seconds=audio.duration_seconds,
            )
    runs = await _runs_by_audio(db, user_id=user_id, audio_ids=[audio.id])
    attempt = await runs_service.latest_attempt(db, user_id=user_id, audio_id=audio.id)
    subject = (
        await db.execute(
            select(Subject).where(Subject.user_id == user_id, Subject.id == audio.subject_id)
        )
    ).scalar_one_or_none()
    if subject is None:  # FK guarantees the row; fail closed if it ever drifts.
        raise LookupError("subject missing for an owned audio")
    stages = _derive_stages(audio, runs.get(audio.id, []), usable_version=usable)
    summary = AudioSummary(
        id=audio.id,
        subject=SubjectRef(id=subject.id, name=subject.name, color=subject.color),
        title=audio.title,
        class_date=audio.class_date,
        class_timezone=audio.class_timezone,
        language_code=audio.language_code,
        duration_seconds=audio.duration_seconds,
        created_at=audio.created_at,
        attempt=_attempt_summary(attempt),
        stages=stages,
        aggregate_status=_aggregate(audio, attempt, stages, usable_version=usable),
    )
    return AudioDetail(summary=summary, transcript=transcript_info, srt_exportable=exportable)


async def active_transcript(
    db: AsyncSession, *, user_id: uuid.UUID, audio: Audio
) -> Transcript | None:
    """The active, live transcript of a class, or None (fail closed, like `retry`)."""
    version = await runs_service.usable_transcript_version(db, user_id=user_id, audio=audio)
    if version is None:
        return None
    return (
        await db.execute(
            select(Transcript).where(
                Transcript.user_id == user_id,
                Transcript.audio_id == audio.id,
                Transcript.version == version,
            )
        )
    ).scalar_one_or_none()


async def segments_for(
    db: AsyncSession, *, user_id: uuid.UUID, transcript_id: uuid.UUID
) -> list[Segment]:
    result = await db.execute(
        select(Segment)
        .where(Segment.user_id == user_id, Segment.transcript_id == transcript_id)
        .order_by(Segment.ordinal)
    )
    return list(result.scalars())


async def get_segment(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    transcript_id: uuid.UUID,
    segment_id: uuid.UUID,
) -> Segment | None:
    """One owned segment; a foreign or unknown id is indistinguishable from missing."""
    return (
        await db.execute(
            select(Segment).where(
                Segment.user_id == user_id,
                Segment.transcript_id == transcript_id,
                Segment.id == segment_id,
            )
        )
    ).scalar_one_or_none()


async def progress_snapshot(
    db: AsyncSession, *, user_id: uuid.UUID, audio_id: uuid.UUID
) -> ProgressSnapshot | None:
    """Current honest progress of an owned class, or None if it no longer exists."""
    audio = await get_owned(db, Audio, user_id, audio_id)
    if audio is None or audio.deleted_at is not None:
        return None
    attempt = await runs_service.latest_attempt(db, user_id=user_id, audio_id=audio_id)
    usable = await runs_service.usable_transcript_version(db, user_id=user_id, audio=audio)
    runs = await _runs_by_audio(db, user_id=user_id, audio_ids=[audio_id])
    stages = _derive_stages(audio, runs.get(audio_id, []), usable_version=usable)
    return ProgressSnapshot(
        attempt_status=attempt.status if attempt is not None else None,
        attempt_error_code=attempt.error_code if attempt is not None else None,
        received_bytes=attempt.received_bytes if attempt is not None else 0,
        fragments_done=attempt.fragments_done if attempt is not None else None,
        fragments_total=attempt.fragments_total if attempt is not None else None,
        stages=stages,
        aggregate=_aggregate(audio, attempt, stages, usable_version=usable),
    )


def _terminal_event(snapshot: ProgressSnapshot) -> dict[str, Any]:
    if snapshot.aggregate == "failed":
        # The terminal code is the attempt's persisted `error_code`, or `aggregate_failed`
        # when the failure has no attempt to name (S2.md §A2.9 / DISENO §3.5).
        return {
            "code": snapshot.attempt_error_code or "aggregate_failed",
            "detail": snapshot.attempt_status,
        }
    return {"aggregate": snapshot.aggregate, "stages": dict(snapshot.stages)}


async def stream_progress(
    factory: async_sessionmaker[AsyncSession],
    *,
    user_id: uuid.UUID,
    audio_id: uuid.UUID,
    poll_seconds: float,
    heartbeat_seconds: float,
    max_seconds: float,
) -> AsyncGenerator[str, None]:
    """SSE generator: a DB snapshot on connect/reconnect, real deltas, one terminal event.

    Each poll opens a short-lived session from `factory` (no long transaction). The stream is
    STATE, so a reconnect re-sends `snapshot` regardless of `Last-Event-ID`; `done`/`error` is
    the single terminal frame, after which the connection closes.
    """
    # Bind the tenant in the task that opens the short sessions (A2.17), independent of
    # whether the request context was inherited by the streaming task.
    bind_user(user_id)
    loop = asyncio.get_running_loop()
    started = loop.time()
    last_heartbeat = started
    event_id = 0
    last_key: tuple[int, int | None, int | None, str | None] | None = None
    while True:
        async with factory() as db:
            snapshot = await progress_snapshot(db, user_id=user_id, audio_id=audio_id)
        if snapshot is None:
            event_id += 1
            yield progress_service.encode_event(
                name="error", id=event_id, data={"code": "not_found"}
            )
            return
        key = snapshot.progress_key()
        if last_key is None:
            event_id += 1
            yield progress_service.encode_event(
                name="snapshot", id=event_id, data=snapshot.snapshot_payload()
            )
            last_key = key
        elif key != last_key:
            event_id += 1
            yield progress_service.encode_event(
                name="progress", id=event_id, data=snapshot.progress_payload()
            )
            last_key = key
        if snapshot.aggregate in progress_service.TERMINAL_AGGREGATES:
            event_id += 1
            yield progress_service.encode_event(
                name="done" if snapshot.aggregate != "failed" else "error",
                id=event_id,
                data=_terminal_event(snapshot),
            )
            return
        now = loop.time()
        if now - last_heartbeat >= heartbeat_seconds:
            yield progress_service.heartbeat()
            last_heartbeat = now
        if now - started >= max_seconds:
            return
        await asyncio.sleep(poll_seconds)

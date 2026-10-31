"""I-S3-SG-04: `processing_runs` per transcript/config version against PostgreSQL.

The ticket behavior: every run records the transcript version and the configuration/prompt
version it applied; reanalysis only reprocesses what is needed (a NEW run when a version
changed, never a reset/reuse of the old row); old runs stay queryable. The suite drives the
real unique key of `processing_runs`, the backfill decision and the tenant-scoped history
endpoint.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session
from app.main import app
from app.models import Audio, ProcessingRun, Transcript
from app.services.analysis_backfill import (
    BackfillStatus,
    backfill_classes,
)
from app.services.runs import (
    ANALYSIS_CONFIG_VERSION,
    ANALYSIS_PROMPT_VERSION,
    INDEX_CONFIG_VERSION,
    ensure_stage_run,
    run_history,
)
from tests.integration import actors

pytestmark = pytest.mark.integration


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    async def override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = override
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=settings.allowed_origin
    ) as c:
        yield c
    app.dependency_overrides.clear()


async def _add_transcript_version(
    db: AsyncSession, actor: actors.Actor, audio: Audio, *, version: int
) -> Transcript:
    text = f"texto de prueba v{version}"
    transcript = Transcript(
        user_id=actor.user_id,
        audio_id=audio.id,
        version=version,
        language_requested=audio.language_code,
        model="test-model",
        model_config={},
        timestamp_precision="none",
        text=text,
        char_count=len(text),
        fts_config="spanish",
    )
    db.add(transcript)
    await db.flush()
    audio.active_transcript_version = version
    await db.flush()
    return transcript


async def _runs(
    db: AsyncSession, *, user_id: uuid.UUID, audio_id: uuid.UUID
) -> list[ProcessingRun]:
    result = await db.execute(
        select(ProcessingRun)
        .where(ProcessingRun.user_id == user_id, ProcessingRun.audio_id == audio_id)
        .order_by(ProcessingRun.created_at, ProcessingRun.id)
    )
    return list(result.scalars())


async def test_run_records_transcript_config_and_prompt_versions(
    db_session: AsyncSession,
) -> None:
    actor = await actors.make_actor(db_session)
    audio, _attempt, transcript = await actors.make_class_with_transcript(
        db_session, actor, required_stages=["index", "analyze"]
    )

    outcome = await ensure_stage_run(
        db_session,
        user_id=actor.user_id,
        audio_id=audio.id,
        stage="analyze",
        transcript_version=transcript.version,
    )
    await db_session.flush()

    run = await db_session.get(ProcessingRun, outcome.run_id)
    assert run is not None
    assert run.transcript_version == transcript.version
    assert run.config_version == ANALYSIS_CONFIG_VERSION
    assert run.prompt_version == ANALYSIS_PROMPT_VERSION
    assert run.status == "pending"


async def test_same_versions_reuse_the_same_run(db_session: AsyncSession) -> None:
    actor = await actors.make_actor(db_session)
    audio, _attempt, transcript = await actors.make_class_with_transcript(
        db_session, actor, required_stages=["index", "analyze"]
    )

    first = await ensure_stage_run(
        db_session,
        user_id=actor.user_id,
        audio_id=audio.id,
        stage="analyze",
        transcript_version=transcript.version,
    )
    second = await ensure_stage_run(
        db_session,
        user_id=actor.user_id,
        audio_id=audio.id,
        stage="analyze",
        transcript_version=transcript.version,
    )
    await db_session.flush()

    assert first.run_id == second.run_id
    rows = await _runs(db_session, user_id=actor.user_id, audio_id=audio.id)
    assert len(rows) == 1


async def test_changed_transcript_version_creates_a_new_run_and_keeps_the_old_one(
    db_session: AsyncSession,
) -> None:
    actor = await actors.make_actor(db_session)
    audio, _attempt, transcript = await actors.make_class_with_transcript(
        db_session, actor, required_stages=["index", "analyze"]
    )
    first = await ensure_stage_run(
        db_session,
        user_id=actor.user_id,
        audio_id=audio.id,
        stage="analyze",
        transcript_version=transcript.version,
    )
    old = await db_session.get(ProcessingRun, first.run_id)
    assert old is not None
    old.status = "succeeded"

    newer = await _add_transcript_version(db_session, actor, audio, version=2)
    second = await ensure_stage_run(
        db_session,
        user_id=actor.user_id,
        audio_id=audio.id,
        stage="analyze",
        transcript_version=newer.version,
    )
    await db_session.flush()

    assert second.run_id != first.run_id
    rows = await _runs(db_session, user_id=actor.user_id, audio_id=audio.id)
    assert {row.transcript_version for row in rows} == {1, 2}
    preserved = await db_session.get(ProcessingRun, first.run_id)
    assert preserved is not None
    assert preserved.status == "succeeded"
    history = await run_history(db_session, user_id=actor.user_id, audio=audio)
    assert [entry.current for entry in history if entry.transcript_version == 1] == [False]
    assert [entry.current for entry in history if entry.transcript_version == 2] == [True]


async def test_changed_config_creates_a_new_run_instead_of_reusing_succeeded(
    db_session: AsyncSession,
) -> None:
    actor = await actors.make_actor(db_session)
    audio, _attempt, transcript = await actors.make_class_with_transcript(
        db_session, actor, required_stages=["index", "analyze"]
    )
    old = ProcessingRun(
        user_id=actor.user_id,
        audio_id=audio.id,
        stage="analyze",
        transcript_version=transcript.version,
        config_version="v0:prompt=v0",
        prompt_version="v0",
        status="succeeded",
        finished_at=datetime.now(UTC),
    )
    db_session.add(old)
    await db_session.flush()
    old_id = old.id

    outcome = await ensure_stage_run(
        db_session,
        user_id=actor.user_id,
        audio_id=audio.id,
        stage="analyze",
        transcript_version=transcript.version,
    )
    await db_session.flush()

    assert outcome.run_id != old_id
    preserved = await db_session.get(ProcessingRun, old_id)
    assert preserved is not None
    assert preserved.status == "succeeded"
    assert preserved.config_version == "v0:prompt=v0"
    new = await db_session.get(ProcessingRun, outcome.run_id)
    assert new is not None
    assert new.config_version == ANALYSIS_CONFIG_VERSION
    assert new.prompt_version == ANALYSIS_PROMPT_VERSION
    assert new.status == "pending"


async def test_index_and_analysis_versions_do_not_reprocess_each_other(
    db_session: AsyncSession,
) -> None:
    actor = await actors.make_actor(db_session)
    audio, _attempt, transcript = await actors.make_class_with_transcript(
        db_session, actor, required_stages=["index", "analyze"]
    )
    index_run = await ensure_stage_run(
        db_session,
        user_id=actor.user_id,
        audio_id=audio.id,
        stage="index",
        transcript_version=transcript.version,
    )
    await db_session.flush()

    analysis_run = await ensure_stage_run(
        db_session,
        user_id=actor.user_id,
        audio_id=audio.id,
        stage="analyze",
        transcript_version=transcript.version,
    )
    await db_session.flush()

    index_row = await db_session.get(ProcessingRun, index_run.run_id)
    assert index_row is not None
    assert index_row.config_version == INDEX_CONFIG_VERSION
    assert index_row.prompt_version is None
    rows = await _runs(db_session, user_id=actor.user_id, audio_id=audio.id)
    assert {row.id for row in rows} == {index_run.run_id, analysis_run.run_id}


async def test_run_history_endpoint_lists_versions_and_isolates_tenants(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    other = await actors.make_actor(db_session)
    audio, _attempt, transcript = await actors.make_class_with_transcript(
        db_session, actor, required_stages=["index", "analyze"]
    )
    await ensure_stage_run(
        db_session,
        user_id=actor.user_id,
        audio_id=audio.id,
        stage="index",
        transcript_version=transcript.version,
    )
    analysis = await ensure_stage_run(
        db_session,
        user_id=actor.user_id,
        audio_id=audio.id,
        stage="analyze",
        transcript_version=transcript.version,
    )
    await db_session.flush()
    audio_id = audio.id

    response = await client.get(f"/audios/{audio_id}/runs", cookies=actor.cookies)

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 2
    by_stage = {item["stage"]: item for item in body["items"]}
    assert by_stage["index"]["config_version"] == INDEX_CONFIG_VERSION
    assert by_stage["index"]["prompt_version"] is None
    assert by_stage["analyze"]["config_version"] == ANALYSIS_CONFIG_VERSION
    assert by_stage["analyze"]["prompt_version"] == ANALYSIS_PROMPT_VERSION
    assert by_stage["analyze"]["id"] == str(analysis.run_id)
    assert all(item["current"] is True for item in body["items"])

    foreign = await client.get(f"/audios/{audio_id}/runs", cookies=other.cookies)
    assert foreign.status_code == 404

    invalid_limit = await client.get(
        f"/audios/{audio_id}/runs?limit=0", cookies=actor.cookies
    )
    assert invalid_limit.status_code == 422


async def test_backfill_reanalyses_when_the_config_version_changed(
    db_session: AsyncSession,
) -> None:
    actor = await actors.make_actor(db_session)
    audio, _attempt, transcript = await actors.make_class_with_transcript(
        db_session, actor, required_stages=["index", "analyze"]
    )
    old = ProcessingRun(
        user_id=actor.user_id,
        audio_id=audio.id,
        stage="analyze",
        transcript_version=transcript.version,
        config_version="v0:prompt=v0",
        prompt_version="v0",
        status="succeeded",
        finished_at=datetime.now(UTC),
    )
    db_session.add(old)
    await db_session.flush()
    old_id = old.id
    audio_id = audio.id

    report = await backfill_classes(db_session, user_id=actor.user_id, apply=True)
    await db_session.flush()

    assert report.counts[BackfillStatus.QUEUED] == 1
    item = report.items[0]
    assert item.audio_id == audio_id
    assert item.run_id is not None
    assert item.run_id != old_id
    new = await db_session.get(ProcessingRun, item.run_id)
    assert new is not None
    assert new.config_version == ANALYSIS_CONFIG_VERSION
    assert new.prompt_version == ANALYSIS_PROMPT_VERSION
    assert new.status == "pending"
    preserved = await db_session.get(ProcessingRun, old_id)
    assert preserved is not None
    assert preserved.status == "succeeded"

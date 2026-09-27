"""J1.5 contra PostgreSQL y Redis reales: el dispatcher encola solo IDs de eventos listos.

Usa una cola propia por test para que el worker de compose, si está corriendo, no consuma
los jobs; al final borra esa cola y los jobs que creó.
"""
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta

import pytest
from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Audio, IngestionAttempt, OutboxEvent, Subject, User
from app.services.outbox import Outcome
from app.workers.dispatcher import dispatch_pending

pytestmark = pytest.mark.integration

NOW = datetime.now(UTC)


@pytest.fixture
async def redis() -> AsyncIterator[tuple[ArqRedis, str]]:
    pool = await create_pool(RedisSettings.from_dsn(str(settings.redis_url)))
    queue = f"arq:test:{uuid.uuid4().hex}"
    try:
        yield pool, queue
    finally:
        # `arq:job:<id>` no lleva namespace de cola y los jobs reales también se llaman
        # `outbox:<id>`: solo se borran los ids que están en la cola de ESTE test.
        job_ids = await pool.zrange(queue, 0, -1)
        job_keys = [f"arq:job:{job_id.decode()}" for job_id in job_ids]
        await pool.delete(queue, *job_keys)
        await pool.aclose()


async def _audio(db: AsyncSession, cleanup_status: str | None) -> tuple[User, Audio]:
    user = User(google_sub=f"sub-{uuid.uuid4()}", email="d@example.test")
    db.add(user)
    await db.flush()
    subject = Subject(user_id=user.id, name="Química")
    db.add(subject)
    await db.flush()
    audio = Audio(
        user_id=user.id, subject_id=subject.id, class_date=date(2026, 9, 21),
        class_timezone="America/Bogota", language_code="es",
    )
    db.add(audio)
    await db.flush()
    if cleanup_status is not None:
        db.add(
            IngestionAttempt(
                user_id=user.id, audio_id=audio.id, privacy_notice_version="v1",
                cloud_processing_accepted_at=NOW, third_party_voice_acknowledged_at=NOW,
                declared_providers={}, status="succeeded", owner_instance="ingest-test",
                fencing_token=1, cleanup_status=cleanup_status,
            )
        )
        await db.flush()
    return user, audio


def _event(user: User, audio: Audio, type_: str, *, blocked: bool = False) -> OutboxEvent:
    return OutboxEvent(
        user_id=user.id, type=type_, resource_type="audio", resource_id=audio.id,
        resource_version=1, enabled=not blocked,
        blocked_reason="cleanup_pending" if blocked else None,
    )


async def test_publishes_ready_events_with_ids_only(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    pool, queue = redis
    user, audio = await _audio(db_session, "verified")
    index = _event(user, audio, "index_requested")
    analyze = _event(user, audio, "analyze_requested")
    db_session.add_all([index, analyze])
    await db_session.flush()

    report = await dispatch_pending(db_session, pool, queue_name=queue)

    assert report[Outcome.PUBLISH] == 2
    jobs = {job.function: job for job in await pool.queued_jobs(queue_name=queue)}
    assert jobs["index"].args == (str(audio.id), 1)
    assert jobs["analyze"].args == (str(audio.id),)
    assert all(not job.kwargs for job in jobs.values())
    assert index.status == analyze.status == "dispatched"
    assert index.published_at is not None


@pytest.mark.parametrize("cleanup_status", ["pending", "failed", None])
async def test_gated_event_waits_for_verified_cleanup(
    db_session: AsyncSession, redis: tuple[ArqRedis, str], cleanup_status: str | None
) -> None:
    # Aun habilitado (defensa en profundidad), sin cleanup verificado no se publica.
    pool, queue = redis
    user, audio = await _audio(db_session, cleanup_status)
    event = _event(user, audio, "index_requested")
    db_session.add(event)
    await db_session.flush()

    report = await dispatch_pending(db_session, pool, queue_name=queue)

    assert report[Outcome.CLEANUP_PENDING] == 1
    assert await pool.queued_jobs(queue_name=queue) == []
    assert event.status == "pending" and event.published_at is None


async def test_disabled_events_are_not_even_selected(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    pool, queue = redis
    user, audio = await _audio(db_session, "verified")
    db_session.add(_event(user, audio, "index_requested", blocked=True))
    await db_session.flush()

    report = await dispatch_pending(db_session, pool, queue_name=queue)

    assert sum(report.values()) == 0
    assert await pool.queued_jobs(queue_name=queue) == []


async def test_events_without_s1_consumer_stay_pending(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    pool, queue = redis
    user, audio = await _audio(db_session, "pending")
    event = _event(user, audio, "sync_task_event")
    db_session.add(event)
    await db_session.flush()

    report = await dispatch_pending(db_session, pool, queue_name=queue)

    # Ni siquiera entran al lote: siguen pendientes hasta que exista su consumidor.
    assert sum(report.values()) == 0
    assert event.status == "pending"
    assert await pool.queued_jobs(queue_name=queue) == []


async def test_second_cycle_does_not_republish(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    pool, queue = redis
    user, audio = await _audio(db_session, "verified")
    db_session.add(_event(user, audio, "index_requested"))
    await db_session.flush()

    await dispatch_pending(db_session, pool, queue_name=queue)
    second = await dispatch_pending(db_session, pool, queue_name=queue)

    assert sum(second.values()) == 0
    assert len(await pool.queued_jobs(queue_name=queue)) == 1


async def test_unknown_type_is_skipped_once(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    pool, queue = redis
    user, audio = await _audio(db_session, "verified")
    event = _event(user, audio, "index.requested")  # typo de tipo: nunca publicable
    db_session.add(event)
    await db_session.flush()

    first = await dispatch_pending(db_session, pool, queue_name=queue)
    second = await dispatch_pending(db_session, pool, queue_name=queue)

    assert first[Outcome.UNKNOWN_TYPE] == 1
    assert event.status == "skipped" and event.published_at is None
    assert sum(second.values()) == 0  # ya no ocupa hueco en el lote
    assert await pool.queued_jobs(queue_name=queue) == []


async def test_gated_event_on_non_audio_resource_is_skipped(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    pool, queue = redis
    user, audio = await _audio(db_session, "verified")
    event = _event(user, audio, "index_requested")
    event.resource_type = "task"
    db_session.add(event)
    await db_session.flush()

    report = await dispatch_pending(db_session, pool, queue_name=queue)

    assert report[Outcome.UNKNOWN_TYPE] == 1
    assert event.status == "skipped"
    assert await pool.queued_jobs(queue_name=queue) == []


async def test_backlog_without_consumer_does_not_starve_index(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    # Eventos viejos sin consumidor no deben llenar el lote y bloquear un index nuevo.
    pool, queue = redis
    user, audio = await _audio(db_session, "verified")
    for n in range(3):
        backlog = _event(user, audio, "notify")
        backlog.dedupe_key = f"reminder-{n}"
        db_session.add(backlog)
    await db_session.flush()
    index = _event(user, audio, "index_requested")
    db_session.add(index)
    await db_session.flush()

    report = await dispatch_pending(db_session, pool, queue_name=queue, batch_size=1)

    assert report[Outcome.PUBLISH] == 1
    assert index.status == "dispatched"


async def test_latest_attempt_decides_the_gate(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    # Un intento viejo verificado no habilita el index si el más reciente aún no limpió.
    pool, queue = redis
    user, audio = await _audio(db_session, "verified")
    db_session.add(
        IngestionAttempt(
            user_id=user.id, audio_id=audio.id, privacy_notice_version="v1",
            cloud_processing_accepted_at=NOW, third_party_voice_acknowledged_at=NOW,
            declared_providers={}, status="transcript_committed_cleanup_pending",
            owner_instance="ingest-test", fencing_token=2, cleanup_status="pending",
            created_at=datetime.now(UTC) + timedelta(seconds=5),
        )
    )
    db_session.add(_event(user, audio, "index_requested"))
    await db_session.flush()

    report = await dispatch_pending(db_session, pool, queue_name=queue)

    assert report[Outcome.CLEANUP_PENDING] == 1
    assert await pool.queued_jobs(queue_name=queue) == []


async def test_index_without_resource_version_is_skipped(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    # index(audio_id, version) no debe recibir None: un emisor mal formado no se publica.
    pool, queue = redis
    user, audio = await _audio(db_session, "verified")
    event = _event(user, audio, "index_requested")
    event.resource_version = None
    db_session.add(event)
    await db_session.flush()

    report = await dispatch_pending(db_session, pool, queue_name=queue)

    assert report[Outcome.UNKNOWN_TYPE] == 1
    assert event.status == "skipped"
    assert await pool.queued_jobs(queue_name=queue) == []


async def test_fixture_cleanup_leaves_other_queues_jobs_alone(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    # Un job con el mismo prefijo en otra cola (p. ej. el worker de compose) sobrevive.
    pool, _ = redis
    other_queue = f"arq:test-other:{uuid.uuid4().hex}"
    job_id = f"outbox:{uuid.uuid4()}"
    await pool.enqueue_job("index", "x", 1, _job_id=job_id, _queue_name=other_queue)
    try:
        assert await pool.exists(f"arq:job:{job_id}")
    finally:
        await pool.delete(other_queue, f"arq:job:{job_id}")

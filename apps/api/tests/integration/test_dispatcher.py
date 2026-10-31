"""J1.5 contra PostgreSQL y Redis reales: el dispatcher encola solo IDs de eventos listos.

Usa una cola propia por test para que el worker de compose, si está corriendo, no consuma
los jobs; al final borra esa cola y los jobs que creó.
"""
import random
import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

import pytest
from arq.connections import ArqRedis
from arq.jobs import Job
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Audio, IngestionAttempt, OutboxEvent, Subject, User
from app.services.outbox import Outcome
from app.services.outbox_retry import RetryPolicy
from app.workers.dispatcher import dispatch_pending

pytestmark = pytest.mark.integration

NOW = datetime.now(UTC)


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

    # Ni siquiera entra al lote: sigue pendiente hasta que el cleanup se verifique.
    assert sum(report.values()) == 0
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


async def test_events_without_consumer_stay_pending(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    pool, queue = redis
    user, audio = await _audio(db_session, "pending")
    event = _event(user, audio, "notify")
    db_session.add(event)
    await db_session.flush()

    report = await dispatch_pending(db_session, pool, queue_name=queue)

    # Ni siquiera entran al lote: siguen pendientes hasta que exista su consumidor.
    assert sum(report.values()) == 0
    assert event.status == "pending"
    assert await pool.queued_jobs(queue_name=queue) == []


async def test_task_event_is_dispatched_with_task_id_and_version(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    """J3.4: Calendar rows carry (task_id, task_version) and are not audio-revalidated."""
    pool, queue = redis
    user, audio = await _audio(db_session, "pending")
    event = _event(user, audio, "sync_task_event")
    event.resource_type = "task"
    event.resource_id = uuid.uuid4()
    event.resource_version = 3
    db_session.add(event)
    await db_session.flush()

    report = await dispatch_pending(db_session, pool, queue_name=queue)

    assert report[Outcome.PUBLISH] == 1
    jobs = await pool.queued_jobs(queue_name=queue)
    assert len(jobs) == 1
    assert jobs[0].function == "sync_task_event"
    assert jobs[0].args == (str(event.resource_id), 3)
    assert event.status == "dispatched"


async def test_task_event_without_version_is_skipped(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    """J3.4 receives a version; a malformed row is skipped once, never enqueued."""
    pool, queue = redis
    user, audio = await _audio(db_session, "pending")
    event = _event(user, audio, "sync_task_event")
    event.resource_type = "task"
    event.resource_version = None
    db_session.add(event)
    await db_session.flush()

    report = await dispatch_pending(db_session, pool, queue_name=queue)

    assert report[Outcome.UNKNOWN_TYPE] == 1
    assert event.status == "skipped"
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

    assert sum(report.values()) == 0
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


async def test_blocked_gated_backlog_does_not_starve_ready_events(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    # Index antiguos sin cleanup verificado no deben llenar el lote y bloquear uno listo.
    pool, queue = redis
    for _ in range(3):
        stuck_user, stuck_audio = await _audio(db_session, "failed")
        db_session.add(_event(stuck_user, stuck_audio, "index_requested"))
    await db_session.flush()
    user, audio = await _audio(db_session, "verified")
    ready = _event(user, audio, "index_requested")
    db_session.add(ready)
    await db_session.flush()

    report = await dispatch_pending(db_session, pool, queue_name=queue, batch_size=1)

    assert report[Outcome.PUBLISH] == 1
    assert ready.status == "dispatched"


async def test_requeue_after_failed_commit_is_counted_as_deduplicated(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    # Simula un ciclo anterior que encoló y no confirmó: el job ya existe en Redis.
    pool, queue = redis
    user, audio = await _audio(db_session, "verified")
    event = _event(user, audio, "index_requested")
    db_session.add(event)
    await db_session.flush()
    await pool.enqueue_job("index", str(audio.id), 1, _job_id=f"outbox:{event.id}",
                           _queue_name=queue)

    report = await dispatch_pending(db_session, pool, queue_name=queue)

    assert report[Outcome.PUBLISH] == 1
    assert report[Outcome.DEDUPLICATED] == 1
    assert event.status == "dispatched"
    assert len(await pool.queued_jobs(queue_name=queue)) == 1


class _FakeClock:
    """Injected clock: backoff is tested without waiting or reading the real time."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class _ScriptedPool:
    """Test double for `ArqRedis`: each call consumes one entry; `None` delegates to the real pool.

    An entry that is an exception is raised instead of enqueued.
    """

    def __init__(self, real: ArqRedis, script: Sequence[BaseException | None]) -> None:
        self._real = real
        self._script = list(script)

    async def enqueue_job(self, function: str, *args: Any, **kwargs: Any) -> Job | None:
        step = self._script.pop(0) if self._script else None
        if step is not None:
            raise step
        return await self._real.enqueue_job(function, *args, **kwargs)


def _transient() -> ResponseError:
    return ResponseError("TRYAGAIN multi-key operation in progress")


async def test_transient_error_schedules_jittered_backoff_then_publishes(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    pool, queue = redis
    start = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
    clock = _FakeClock(start)
    user, audio = await _audio(db_session, "verified")
    event = _event(user, audio, "index_requested")
    db_session.add(event)
    await db_session.flush()
    flaky = cast(ArqRedis, _ScriptedPool(pool, [_transient()]))

    first = await dispatch_pending(
        db_session, flaky, queue_name=queue, retry=RetryPolicy(rng=random.Random(7)),  # noqa: S311 - jitter, not a security value
        clock=clock,
    )

    expected_wait = RetryPolicy(rng=random.Random(7)).delay(1)  # noqa: S311 - jitter, not a security value
    assert first[Outcome.RETRY_SCHEDULED] == 1
    assert (event.status, event.attempts) == ("pending", 1)
    assert event.next_attempt_at == start + expected_wait
    assert await pool.queued_jobs(queue_name=queue) == []

    clock.now = start + timedelta(seconds=1)
    too_early = await dispatch_pending(db_session, pool, queue_name=queue, clock=clock)
    assert sum(too_early.values()) == 0

    clock.now = event.next_attempt_at
    retried = await dispatch_pending(db_session, pool, queue_name=queue, clock=clock)
    assert retried[Outcome.PUBLISH] == 1
    assert (event.status, event.attempts) == ("dispatched", 2)
    assert len(await pool.queued_jobs(queue_name=queue)) == 1


async def test_third_transient_failure_marks_event_failed(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    pool, queue = redis
    start = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
    clock = _FakeClock(start)
    user, audio = await _audio(db_session, "verified")
    event = _event(user, audio, "index_requested")
    db_session.add(event)
    await db_session.flush()
    flaky = cast(ArqRedis, _ScriptedPool(pool, [_transient(), _transient(), _transient()]))
    policy = RetryPolicy(rng=random.Random(3))  # noqa: S311 - jitter, not a security value

    outcomes = []
    for _ in range(3):
        report = await dispatch_pending(
            db_session, flaky, queue_name=queue, retry=policy, clock=clock
        )
        outcomes.append(report)
        if event.next_attempt_at is not None:
            clock.now = event.next_attempt_at

    assert [r[Outcome.RETRY_SCHEDULED] for r in outcomes[:2]] == [1, 1]
    assert outcomes[2][Outcome.FAILED] == 1
    assert (event.status, event.attempts) == ("failed", 3)
    # Terminal: neither the clock nor a healthy Redis picks it up again.
    clock.now = start + timedelta(days=30)
    later = await dispatch_pending(db_session, pool, queue_name=queue, clock=clock)
    assert sum(later.values()) == 0
    assert await pool.queued_jobs(queue_name=queue) == []


async def test_permanent_error_fails_without_retry(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    pool, queue = redis
    start = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
    clock = _FakeClock(start)
    user, audio = await _audio(db_session, "verified")
    event = _event(user, audio, "index_requested")
    db_session.add(event)
    await db_session.flush()
    broken = cast(ArqRedis, _ScriptedPool(pool, [TypeError("not serializable")]))

    report = await dispatch_pending(db_session, broken, queue_name=queue, clock=clock)

    assert report[Outcome.FAILED] == 1
    assert (event.status, event.attempts) == ("failed", 1)
    assert event.next_attempt_at is None
    clock.now = start + timedelta(days=1)
    later = await dispatch_pending(db_session, pool, queue_name=queue, clock=clock)
    assert sum(later.values()) == 0


async def test_infra_error_stops_cycle_without_charging_events(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    # Redis being down is not the event's fault: it spends no attempts and does not move to failed.
    pool, queue = redis
    start = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
    clock = _FakeClock(start)
    user, audio = await _audio(db_session, "verified")
    first = _event(user, audio, "index_requested")
    first.created_at = start - timedelta(seconds=2)
    second = _event(user, audio, "analyze_requested")
    second.created_at = start - timedelta(seconds=1)
    db_session.add_all([first, second])
    await db_session.flush()
    outage = cast(ArqRedis, _ScriptedPool(pool, [None, RedisConnectionError("refused")]))

    report = await dispatch_pending(db_session, outage, queue_name=queue, clock=clock)

    assert report[Outcome.PUBLISH] == 1
    assert report[Outcome.DEFERRED_INFRA] == 1
    assert first.status == "dispatched"
    assert (second.status, second.attempts, second.next_attempt_at) == ("pending", 0, None)

    recovered = await dispatch_pending(db_session, pool, queue_name=queue, clock=clock)
    assert recovered[Outcome.PUBLISH] == 1
    assert (second.status, second.attempts) == ("dispatched", 1)


async def test_tombstoned_audio_is_not_published(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    pool, queue = redis
    user, audio = await _audio(db_session, "verified")
    event = _event(user, audio, "index_requested")
    db_session.add(event)
    await db_session.flush()
    audio.deleted_at = NOW
    await db_session.flush()

    report = await dispatch_pending(db_session, pool, queue_name=queue)

    assert report[Outcome.TOMBSTONED] == 1
    assert event.status == "skipped" and event.published_at is None
    assert await pool.queued_jobs(queue_name=queue) == []


async def test_revoked_owner_is_not_published(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    pool, queue = redis
    user, audio = await _audio(db_session, "verified")
    event = _event(user, audio, "analyze_requested")
    db_session.add(event)
    await db_session.flush()
    user.status = "deleting"
    await db_session.flush()

    report = await dispatch_pending(db_session, pool, queue_name=queue)

    assert report[Outcome.OWNER_REVOKED] == 1
    assert event.status == "skipped" and event.published_at is None
    assert await pool.queued_jobs(queue_name=queue) == []


async def test_stale_transcript_version_is_not_published(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    pool, queue = redis
    user, audio = await _audio(db_session, "verified")
    audio.active_transcript_version = 2
    event = _event(user, audio, "index_requested")  # resource_version=1: superseded by version 2
    db_session.add(event)
    await db_session.flush()

    report = await dispatch_pending(db_session, pool, queue_name=queue)

    assert report[Outcome.STALE_VERSION] == 1
    assert event.status == "skipped" and event.published_at is None
    assert await pool.queued_jobs(queue_name=queue) == []


async def test_current_transcript_version_still_publishes(
    db_session: AsyncSession, redis: tuple[ArqRedis, str]
) -> None:
    pool, queue = redis
    user, audio = await _audio(db_session, "verified")
    audio.active_transcript_version = 1
    event = _event(user, audio, "index_requested")  # resource_version=1 == active
    db_session.add(event)
    await db_session.flush()

    report = await dispatch_pending(db_session, pool, queue_name=queue)

    assert report[Outcome.PUBLISH] == 1
    assert event.status == "dispatched"

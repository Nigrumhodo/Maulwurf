"""J1.5: contrato de los procesos ARQ (colas y healthcheck), sin Redis."""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from arq.worker import Function

from app.services.scheduler_lease import SchedulerReport
from app.workers import arq_app
from app.workers.arq_app import (
    HEALTH_CHECK_INTERVAL_S,
    RECONCILE_EVERY_MINUTES,
    RETENTION_MINUTE,
    RETENTION_SECOND,
    SchedulerSettings,
    WorkerSettings,
    analyze,
    dispatch_outbox,
    purge_residuals,
    reconcile,
    schedule_notifications,
)
from app.workers.index_job import MAX_TRIES, index
from app.workers.jobs.calendar_sync import JOB_TIMEOUT_S as CALENDAR_SYNC_TIMEOUT_S
from app.workers.jobs.calendar_sync import MAX_TRIES as CALENDAR_SYNC_MAX_TRIES
from app.workers.jobs.calendar_sync import sync_task_event
from app.workers.retry import MAX_TRIES as ANALYSIS_MAX_TRIES


def test_worker_and_scheduler_use_separate_queues() -> None:
    # Si compartieran cola, el scheduler ejecutaría jobs del worker y un solo proceso vivo
    # haría pasar el healthcheck de ambos.
    assert WorkerSettings.queue_name != SchedulerSettings.queue_name


def test_health_key_expires_fast_enough_to_detect_a_dead_process() -> None:
    # ARQ usa 3600 s por defecto: un worker muerto seguiría "sano" durante una hora.
    assert WorkerSettings.health_check_interval == HEALTH_CHECK_INTERVAL_S
    assert SchedulerSettings.health_check_interval == HEALTH_CHECK_INTERVAL_S
    assert HEALTH_CHECK_INTERVAL_S <= 60


def test_worker_registers_index_analyze_and_calendar_sync_with_their_budgets() -> None:
    index_function, analyze_function, calendar_function = WorkerSettings.functions
    assert isinstance(index_function, Function)
    assert index_function.coroutine is index
    assert index_function.max_tries == MAX_TRIES
    assert isinstance(analyze_function, Function)
    assert analyze_function.coroutine is analyze
    assert analyze_function.max_tries == ANALYSIS_MAX_TRIES
    assert isinstance(calendar_function, Function)
    assert calendar_function.coroutine is sync_task_event
    assert calendar_function.max_tries == CALENDAR_SYNC_MAX_TRIES
    assert calendar_function.timeout_s == CALENDAR_SYNC_TIMEOUT_S


def test_scheduler_runs_only_the_outbox_notification_reconciler_and_retention_crons() -> None:
    assert not hasattr(SchedulerSettings, "functions")
    assert [job.coroutine for job in SchedulerSettings.cron_jobs] == [
        dispatch_outbox,
        schedule_notifications,
        reconcile,
        purge_residuals,
    ]


def test_functions_without_their_own_budget_do_not_retry() -> None:
    # Only `index` (J2.2) and `analyze` (S3.6) have a retry budget; the default stays one try.
    assert WorkerSettings.max_tries == 1
    assert SchedulerSettings.max_tries == 1


async def test_dispatch_cron_survives_a_database_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Con la BD caída o sin migrar, el cron registra el tipo de error y el scheduler sigue.
    @asynccontextmanager
    async def broken_scope() -> AsyncIterator[None]:
        # Canario fuera de la posición de contraseña: herramientas que enmascaran DSN no lo
        # ocultan, así que si el cron registrara str(exc) el test fallaría de verdad.
        raise ConnectionRefusedError("connect to db failed: CANARIO-7f3a9c user=maulwurf")
        yield

    monkeypatch.setattr(arq_app, "session_scope", broken_scope)

    await dispatch_outbox({"redis": object()})

    assert "dispatch_outbox.failed error=ConnectionRefusedError" in caplog.text
    assert "CANARIO-7f3a9c" not in caplog.text


async def test_notification_scheduler_cron_survives_a_database_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    @asynccontextmanager
    async def broken_scope() -> AsyncIterator[None]:
        raise ConnectionRefusedError("connect to db failed: CANARIO-4c1e8d user=maulwurf")
        yield

    monkeypatch.setattr(arq_app, "session_scope", broken_scope)

    await schedule_notifications({})

    assert "schedule_notifications.failed error=ConnectionRefusedError" in caplog.text
    assert "CANARIO-4c1e8d" not in caplog.text


async def test_notification_scheduler_cron_uses_the_cross_tenant_jobs_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[object] = []
    token = object()

    def _bind() -> object:
        events.append("bind")
        return token

    def _reset(value: object) -> None:
        events.append(value is token)

    @asynccontextmanager
    async def scope() -> AsyncIterator[object]:
        yield object()

    async def fake_run(db: object) -> SchedulerReport:
        del db
        events.append("run")
        return SchedulerReport(active=True, claimed=1)

    monkeypatch.setattr(arq_app, "bind_cross_tenant", _bind)
    monkeypatch.setattr(arq_app, "reset_cross_tenant", _reset)
    monkeypatch.setattr(arq_app, "session_scope", scope)
    monkeypatch.setattr(arq_app, "run_notification_scheduler", fake_run)

    await schedule_notifications({})

    assert events == ["bind", "run", True]


def test_dispatch_cron_runs_every_minute_at_second_zero() -> None:
    dispatch, _schedule, _reconcile, _retention = SchedulerSettings.cron_jobs
    assert dispatch.second == 0
    assert dispatch.minute is None and dispatch.run_at_startup


def test_notification_scheduler_cron_runs_every_minute_and_at_startup() -> None:
    _dispatch, schedule, _reconcile, _retention = SchedulerSettings.cron_jobs
    assert schedule.coroutine is schedule_notifications
    assert schedule.second == 0
    assert schedule.minute is None and schedule.run_at_startup


def test_reconciler_cron_runs_on_a_fixed_minute_grid_off_the_dispatcher_second() -> None:
    _dispatch, _schedule, reconciler, _retention = SchedulerSettings.cron_jobs
    assert reconciler.second != 0
    assert reconciler.minute == set(range(0, 60, RECONCILE_EVERY_MINUTES))


def test_retention_cron_runs_hourly_off_the_other_crons() -> None:
    _dispatch, _schedule, _reconcile, retention = SchedulerSettings.cron_jobs
    assert retention.second == RETENTION_SECOND
    assert retention.minute == RETENTION_MINUTE
    assert retention.second not in {0, 30}
    assert retention.run_at_startup


async def test_retention_cron_survives_a_database_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    @asynccontextmanager
    async def broken_scope() -> AsyncIterator[None]:
        raise ConnectionRefusedError("CANARIO-7f3a9c user=maulwurf")
        yield

    monkeypatch.setattr(arq_app, "session_scope", broken_scope)

    await purge_residuals({})

    assert "purge_residuals.failed error=ConnectionRefusedError" in caplog.text
    assert "CANARIO-7f3a9c" not in caplog.text


async def test_retention_cron_uses_the_cross_tenant_jobs_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[object] = []
    token = object()

    def _bind() -> object:
        events.append("bind")
        return token

    def _reset(value: object) -> None:
        events.append(value is token)

    @asynccontextmanager
    async def scope() -> AsyncIterator[object]:
        yield object()

    async def fake_purge(db: object) -> dict[str, int]:
        del db
        return {"account_deletions": 0, "google_remote_operations": 0}

    monkeypatch.setattr(arq_app, "bind_cross_tenant", _bind)
    monkeypatch.setattr(arq_app, "reset_cross_tenant", _reset)
    monkeypatch.setattr(arq_app, "session_scope", scope)
    monkeypatch.setattr(arq_app, "purge_expired_residuals", fake_purge)

    await purge_residuals({})

    assert events == ["bind", True]

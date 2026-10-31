"""J1.5: contrato de los procesos ARQ (colas y healthcheck), sin Redis."""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from arq.worker import Function

from app.workers import arq_app
from app.workers.arq_app import (
    HEALTH_CHECK_INTERVAL_S,
    RECONCILE_EVERY_MINUTES,
    SchedulerSettings,
    WorkerSettings,
    analyze,
    dispatch_outbox,
    reconcile,
)
from app.workers.index_job import MAX_TRIES, index
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


def test_worker_registers_index_and_analyze_with_their_retry_budgets() -> None:
    index_function, analyze_function = WorkerSettings.functions
    assert isinstance(index_function, Function)
    assert index_function.coroutine is index
    assert index_function.max_tries == MAX_TRIES
    assert isinstance(analyze_function, Function)
    assert analyze_function.coroutine is analyze
    assert analyze_function.max_tries == ANALYSIS_MAX_TRIES


def test_scheduler_runs_only_the_outbox_and_reconciler_crons() -> None:
    assert not hasattr(SchedulerSettings, "functions")
    assert [job.coroutine for job in SchedulerSettings.cron_jobs] == [dispatch_outbox, reconcile]


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


def test_dispatch_cron_runs_every_minute_at_second_zero() -> None:
    dispatch, _reconcile = SchedulerSettings.cron_jobs
    assert dispatch.second == 0
    assert dispatch.minute is None and dispatch.run_at_startup


def test_reconciler_cron_runs_on_a_fixed_minute_grid_off_the_dispatcher_second() -> None:
    _dispatch, reconciler = SchedulerSettings.cron_jobs
    assert reconciler.second != 0
    assert reconciler.minute == set(range(0, 60, RECONCILE_EVERY_MINUTES))

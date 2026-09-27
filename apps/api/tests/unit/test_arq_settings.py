"""J1.5: contrato de los procesos ARQ (colas y healthcheck), sin Redis."""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

from app.workers import arq_app
from app.workers.arq_app import (
    HEALTH_CHECK_INTERVAL_S,
    SchedulerSettings,
    WorkerSettings,
    analyze,
    dispatch_outbox,
    index,
)


def test_worker_and_scheduler_use_separate_queues() -> None:
    # Si compartieran cola, el scheduler ejecutaría jobs del worker y un solo proceso vivo
    # haría pasar el healthcheck de ambos.
    assert WorkerSettings.queue_name != SchedulerSettings.queue_name


def test_health_key_expires_fast_enough_to_detect_a_dead_process() -> None:
    # ARQ usa 3600 s por defecto: un worker muerto seguiría "sano" durante una hora.
    assert WorkerSettings.health_check_interval == HEALTH_CHECK_INTERVAL_S
    assert SchedulerSettings.health_check_interval == HEALTH_CHECK_INTERVAL_S
    assert HEALTH_CHECK_INTERVAL_S <= 60


def test_worker_registers_only_s1_stubs() -> None:
    assert WorkerSettings.functions == [index, analyze]


def test_scheduler_runs_only_the_outbox_cron() -> None:
    assert not hasattr(SchedulerSettings, "functions")
    assert [job.coroutine for job in SchedulerSettings.cron_jobs] == [dispatch_outbox]


def test_no_retries_until_backoff_policy_exists() -> None:
    assert WorkerSettings.max_tries == 1
    assert SchedulerSettings.max_tries == 1


async def test_dispatch_cron_survives_a_database_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Con la BD caída o sin migrar, el cron registra el tipo de error y el scheduler sigue.
    @asynccontextmanager
    async def broken_scope() -> AsyncIterator[None]:
        # Canario en el DSN: si el cron registrara el mensaje, el test fallaría.
        raise ConnectionRefusedError("postgres://user:canario-secreto@db:5432/api")
        yield

    monkeypatch.setattr(arq_app, "session_scope", broken_scope)

    await dispatch_outbox({"redis": object()})

    assert "dispatch_outbox.failed error=ConnectionRefusedError" in caplog.text
    assert "canario-secreto" not in caplog.text


def test_dispatch_cron_runs_every_minute_at_second_zero() -> None:
    (job,) = SchedulerSettings.cron_jobs
    assert job.second == 0
    assert job.minute is None and job.run_at_startup

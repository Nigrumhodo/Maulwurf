"""J1.5: contrato de los procesos ARQ (colas y healthcheck), sin Redis."""
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

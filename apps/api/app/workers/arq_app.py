"""Procesos ARQ de S1 (J1.5): `worker` y `scheduler`.

Cada proceso usa su propia cola para que el scheduler nunca ejecute jobs del worker, y
por tanto su propia clave de salud (`<cola>:health-check`). El healthcheck de Compose
es `arq <settings> --check`: comprueba que la clave exista en Redis. ARQ la reescribe
cada `HEALTH_CHECK_INTERVAL_S` con TTL de intervalo + 1 s, así que un proceso caído
deja de estar sano en ~30 s. La clave solo contiene contadores (ADR-0005).
"""
import logging
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar

from arq.connections import RedisSettings
from arq.cron import CronJob, cron
from arq.worker import Function, func

from app.core.config import settings
from app.core.db import bind_cross_tenant, reset_cross_tenant, session_scope
from app.core.logging import configure_logging
from app.services.deletion import purge_expired_residuals
from app.workers.dispatcher import dispatch_pending
from app.workers.index_job import JOB_TIMEOUT_S, MAX_TRIES, index
from app.workers.jobs.calendar_sync import JOB_TIMEOUT_S as CALENDAR_SYNC_TIMEOUT_S
from app.workers.jobs.calendar_sync import MAX_TRIES as CALENDAR_SYNC_MAX_TRIES
from app.workers.jobs.calendar_sync import sync_task_event
from app.workers.reconciler import reconcile_once
from app.workers.retry import MAX_TRIES as ANALYSIS_MAX_TRIES

logger = logging.getLogger(__name__)

WORKER_QUEUE = "arq:queue"
SCHEDULER_QUEUE = "arq:scheduler"
HEALTH_CHECK_INTERVAL_S = 30
# Offset from the dispatcher's second 0 so the two crons do not start together.
RECONCILE_EVERY_MINUTES = 5
RECONCILE_SECOND = 30
# A4.4 retention: hourly is enough for a 30-day window; startup also drains a backlog.
RETENTION_MINUTE = 17
RETENTION_SECOND = 45
RETENTION_BATCHES_PER_RUN = 5

_REDIS = RedisSettings.from_dsn(str(settings.redis_url))


async def analyze(ctx: dict[str, Any], audio_id: str) -> None:
    """S2 no-op until L3.6. Its retry budget is already the S3.6 policy (1 + 3)."""
    logger.info("analyze.noop audio_id=%s", audio_id)


async def dispatch_outbox(ctx: dict[str, Any]) -> None:
    """Cron del scheduler: publica en la cola del worker los eventos de outbox listos."""
    # The dispatcher is cross-tenant by design; the jobs role policy requires this
    # explicit switch (A2.17) and the API role can never use it.
    token = bind_cross_tenant()
    try:
        try:
            async with session_scope() as db:
                report = await dispatch_pending(db, ctx["redis"], queue_name=WORKER_QUEUE)
        except Exception as exc:  # p. ej. BD aún sin migrar: el siguiente ciclo reintenta
            # Solo el tipo: el mensaje puede incluir el DSN.
            logger.warning("dispatch_outbox.failed error=%s", type(exc).__name__)
            return
    finally:
        reset_cross_tenant(token)
    if report:
        logger.info("dispatch_outbox %s", dict(report))


async def reconcile(ctx: dict[str, Any]) -> None:
    """Scheduler cron (J2.4): repairs lost dispatches, dead index runs and orphan generations."""
    token = bind_cross_tenant()
    try:
        await reconcile_once(session_scope, ctx["redis"], queue_name=WORKER_QUEUE)
    finally:
        reset_cross_tenant(token)


async def purge_residuals(ctx: dict[str, Any]) -> None:
    """Scheduler cron (A4.4): applies the residual retention window.

    Deletes expired `account_deletions` and `google_remote_operations` rows in bounded batches
    as the cross-tenant jobs role. A failure is logged by type only (never a row value) and
    the next hourly cycle retries; already committed batches stay deleted.
    """
    del ctx  # The cron needs no Redis; keeping the ARQ signature explicit.
    token = bind_cross_tenant()
    totals = {"account_deletions": 0, "google_remote_operations": 0}
    try:
        try:
            async with session_scope() as db:
                for _ in range(RETENTION_BATCHES_PER_RUN):
                    report = await purge_expired_residuals(db)
                    for table, count in report.items():
                        totals[table] += count
                    await db.commit()
                    if not any(report.values()):
                        break
        except Exception as exc:
            # Only the type: an error message can carry a DSN or a row value.
            logger.warning("purge_residuals.failed error=%s", type(exc).__name__)
            return
    finally:
        reset_cross_tenant(token)
    if any(totals.values()):
        logger.info("purge_residuals %s", totals)


async def startup(ctx: dict[str, Any]) -> None:
    """Worker and scheduler startup: JSON logs with redaction (J2.5)."""
    configure_logging()


class WorkerSettings:
    # `index`, `analyze` and Calendar sync have their own retry budgets (J2.2/S3.6/J3.4).
    functions: ClassVar[list[Function | Callable[..., Awaitable[None]]]] = [
        func(index, max_tries=MAX_TRIES, timeout=JOB_TIMEOUT_S),
        func(analyze, max_tries=ANALYSIS_MAX_TRIES),
        func(
            sync_task_event,
            max_tries=CALENDAR_SYNC_MAX_TRIES,
            timeout=CALENDAR_SYNC_TIMEOUT_S,
        ),
    ]
    queue_name = WORKER_QUEUE
    redis_settings = _REDIS
    on_startup = startup
    health_check_interval = HEALTH_CHECK_INTERVAL_S
    keep_result = 60
    max_tries = 1  # no retries for functions without their own budget


class SchedulerSettings:
    # Cada minuto en el segundo 0: latencia máxima de despacho ~60 s.
    cron_jobs: ClassVar[list[CronJob]] = [
        cron(dispatch_outbox, second=0, run_at_startup=True),
        cron(
            reconcile,
            minute=set(range(0, 60, RECONCILE_EVERY_MINUTES)),
            second=RECONCILE_SECOND,
        ),
        cron(
            purge_residuals,
            minute=RETENTION_MINUTE,
            second=RETENTION_SECOND,
            run_at_startup=True,
        ),
    ]
    queue_name = SCHEDULER_QUEUE
    redis_settings = _REDIS
    on_startup = startup
    health_check_interval = HEALTH_CHECK_INTERVAL_S
    keep_result = 60
    max_tries = 1

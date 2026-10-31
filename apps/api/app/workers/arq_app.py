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
from app.workers.dispatcher import dispatch_pending
from app.workers.index_job import JOB_TIMEOUT_S, MAX_TRIES, index
from app.workers.reconciler import reconcile_once
from app.workers.retry import MAX_TRIES as ANALYSIS_MAX_TRIES

logger = logging.getLogger(__name__)

WORKER_QUEUE = "arq:queue"
SCHEDULER_QUEUE = "arq:scheduler"
HEALTH_CHECK_INTERVAL_S = 30
# Offset from the dispatcher's second 0 so the two crons do not start together.
RECONCILE_EVERY_MINUTES = 5
RECONCILE_SECOND = 30

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


async def startup(ctx: dict[str, Any]) -> None:
    """Worker and scheduler startup: JSON logs with redaction (J2.5)."""
    configure_logging()


class WorkerSettings:
    # `index` and `analyze` have their own retry budget (J2.2/S3.6).
    functions: ClassVar[list[Function | Callable[..., Awaitable[None]]]] = [
        func(index, max_tries=MAX_TRIES, timeout=JOB_TIMEOUT_S),
        func(analyze, max_tries=ANALYSIS_MAX_TRIES),
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
    ]
    queue_name = SCHEDULER_QUEUE
    redis_settings = _REDIS
    on_startup = startup
    health_check_interval = HEALTH_CHECK_INTERVAL_S
    keep_result = 60
    max_tries = 1

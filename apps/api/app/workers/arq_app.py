"""Procesos ARQ de S1 (J1.5): `worker` y `scheduler`, todavía sin jobs reales.

Cada proceso usa su propia cola para que el scheduler nunca ejecute jobs del worker, y
por tanto su propia clave de salud (`<cola>:health-check`). El healthcheck de Compose
es `arq <settings> --check`: comprueba que la clave exista en Redis. ARQ la reescribe
cada `HEALTH_CHECK_INTERVAL_S` con TTL de intervalo + 1 s, así que un proceso caído
deja de estar sano en ~30 s. La clave solo contiene contadores (ADR-0005).
"""
import logging
from typing import Any, ClassVar

from arq.connections import RedisSettings
from arq.cron import CronJob, cron
from arq.worker import Function

from app.core.config import settings

logger = logging.getLogger(__name__)

WORKER_QUEUE = "arq:queue"
SCHEDULER_QUEUE = "arq:scheduler"
HEALTH_CHECK_INTERVAL_S = 30

_REDIS = RedisSettings.from_dsn(settings.redis_url)


async def index(ctx: dict[str, Any], audio_id: str, transcript_version: int) -> None:
    # S2/J2.1 conecta el índice real; aquí solo se registran IDs, nunca contenido.
    logger.info("index.noop audio_id=%s transcript_version=%s", audio_id, transcript_version)


async def analyze(ctx: dict[str, Any], audio_id: str) -> None:
    logger.info("analyze.noop audio_id=%s", audio_id)


async def dispatch_outbox(ctx: dict[str, Any]) -> None:
    # El dispatcher con gate de cleanup (U-S1-JF-04) se conecta cuando exista la tabla
    # `outbox_events` (A1.5); hasta entonces el cron solo prueba que el scheduler late.
    logger.debug("dispatch_outbox.noop")


class WorkerSettings:
    functions: ClassVar[list[Function | Any]] = [index, analyze]
    queue_name = WORKER_QUEUE
    redis_settings = _REDIS
    health_check_interval = HEALTH_CHECK_INTERVAL_S
    keep_result = 60
    max_tries = 1  # S1 sin reintentos: backoff/jitter y presupuesto llegan en J2.x


class SchedulerSettings:
    cron_jobs: ClassVar[list[CronJob]] = [cron(dispatch_outbox, run_at_startup=True)]
    queue_name = SCHEDULER_QUEUE
    redis_settings = _REDIS
    health_check_interval = HEALTH_CHECK_INTERVAL_S
    keep_result = 60
    max_tries = 1

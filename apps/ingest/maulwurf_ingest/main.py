"""ASGI del servicio de ingesta (S1.B1).

`/healthz` es liveness: solo prueba que el proceso responde.
`/readyz` es readiness: 200 solo si el proceso sigue endurecido, el tmpfs acepta
temporales y PostgreSQL responde. La capacidad por slot llega con S1.B2. Nunca llama a
proveedores pagados.

Al arrancar se ejecuta el autochequeo de `hardening`: en modo `enforce` (defecto) un
fallo impide servir, así un contenedor mal configurado no llega a aceptar audio.
"""
import asyncio
import contextlib
import logging
import os
import secrets
import socket
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse

from maulwurf_ingest import admission, hardening, metrics, receptions
from maulwurf_ingest.config import settings
from maulwurf_ingest.credential import decode_secret
from maulwurf_ingest.logs import configure_logging
from maulwurf_ingest.prometheus_text import PROMETHEUS_CONTENT_TYPE, render
from maulwurf_ingest.receptions import open_service_pool
from maulwurf_ingest.receptions import router as receptions_router
from maulwurf_ingest.request_context import RequestIdMiddleware
from maulwurf_ingest.supervisor import run_sweeper, sweep_once

logger = logging.getLogger(__name__)

CHECK_TIMEOUT_S = 2.0


class HardeningError(RuntimeError):
    """El proceso no cumple el endurecimiento exigido por S1.B1."""


def enforce_hardening() -> None:
    failures = hardening.check(hardening.collect())
    if not failures:
        logger.info("hardening.ok")
        # En enforce el DSN no es opcional: sin él el contenedor quedaría not_ready
        # para siempre y el log de /readyz (a propósito) no explica la causa.
        if settings.ingest_hardening == "enforce" and not settings.database_url:
            raise HardeningError("MAULWURF_DATABASE_URL sin definir")
        return
    logger.error("hardening.failed checks=%s", ",".join(failures))
    if settings.ingest_hardening == "enforce":
        raise HardeningError(f"endurecimiento incompleto: {', '.join(failures)}")


async def reconcile_on_startup() -> None:
    """One recovery pass before serving. A database miss keeps admission closed.

    The periodic sweeper (`run_sweeper`) repeats this recovery every 60 s once the
    service pool is open. A persisted failed cleanup closes admission before the
    process serves.
    """
    if not settings.database_url:
        return
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    created: asyncpg.Pool | None = None
    try:
        created = await asyncpg.create_pool(
            dsn, min_size=1, max_size=1, timeout=CHECK_TIMEOUT_S, command_timeout=CHECK_TIMEOUT_S,
        )
        if created is None:
            admission.close()
            return
        await sweep_once(
            created, instance=f"ingest:{socket.gethostname()}", tmp_dir=tempfile.gettempdir(),
        )
        await admission.restore_from_database(created)
    except Exception as exc:
        # The DSN is in the exception text. Log only the type.
        logger.warning("reconciler.startup_failed error=%s", type(exc).__name__)
        admission.close()
    finally:
        if created is not None:
            await created.close()


def _sweeper_pool(app: FastAPI) -> asyncpg.Pool | None:
    pool = getattr(app.state, "pool", None)
    return pool if isinstance(pool, asyncpg.Pool) else None


def start_sweeper(app: FastAPI) -> asyncio.Task[None] | None:
    """Start the periodic recovery loop on the service pool. No pool, no loop."""
    pool = _sweeper_pool(app)
    if pool is None:
        return None
    return asyncio.create_task(
        run_sweeper(
            pool, instance=f"ingest:{socket.gethostname()}",
            interval_s=settings.sweep_interval_s, tmp_dir=tempfile.gettempdir(),
        ),
        name="maulwurf-ingest-sweeper",
    )


def refuse_bad_credential() -> None:
    """A present secret must be 32 bytes of hex and must not be a placeholder.

    An absent secret does not stop the process: the internal routes answer 503
    and ``/readyz`` reports ``credential``.
    """
    raw = settings.credential_secret
    previous = settings.credential_previous_secret
    previous_kid = settings.credential_previous_kid
    if (previous is None) != (previous_kid is None):
        raise HardeningError("INGEST_CREDENTIAL_SECRET")
    if raw is None or not raw.strip():
        if previous is not None:
            raise HardeningError("INGEST_CREDENTIAL_SECRET")
        return
    try:
        decode_secret(raw)
        if previous is not None:
            decode_secret(previous)
    except ValueError:
        raise HardeningError("INGEST_CREDENTIAL_SECRET") from None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    enforce_hardening()
    refuse_bad_credential()
    app.state.instance_id = secrets.token_hex(16)
    app.state.pool = None
    await reconcile_on_startup()
    await open_service_pool(app)
    app.state.sweeper = start_sweeper(app)
    try:
        yield
    finally:
        sweeper = getattr(app.state, "sweeper", None)
        if sweeper is not None:
            sweeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sweeper
            app.state.sweeper = None
        pool = getattr(app.state, "pool", None)
        if isinstance(pool, asyncpg.Pool):
            await pool.close()


app = FastAPI(title="Maulwurf Ingest", version="0.1.0", lifespan=lifespan)
app.add_middleware(RequestIdMiddleware)
app.include_router(receptions_router)


async def check_hardening() -> None:
    failures = hardening.check(hardening.collect())
    if failures:
        raise HardeningError(",".join(failures))


async def check_tmpfs() -> None:
    # Crea y libera un temporal vacío: prueba escritura sin dejar nada ni usar contenido.
    tmp_dir = tempfile.gettempdir()
    if os.statvfs(tmp_dir).f_bavail == 0:
        raise OSError("tmpfs lleno")
    with tempfile.TemporaryFile(dir=tmp_dir):
        pass


# /readyz no está autenticado y cada sonda abre una conexión nueva: el semáforo global
# acota las conexiones simultáneas a Postgres aunque llegue una ráfaga de sondas.
_PG_CHECK_GATE = asyncio.Semaphore(2)


async def check_postgres() -> None:
    if not settings.database_url:
        raise RuntimeError("MAULWURF_DATABASE_URL sin definir")
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    async with _PG_CHECK_GATE:
        conn = await asyncpg.connect(dsn, timeout=CHECK_TIMEOUT_S)
        try:
            await conn.execute("SELECT 1")
        finally:
            await conn.close()


async def check_credential() -> None:
    raw = settings.credential_secret
    if raw is None or not raw.strip():
        raise RuntimeError("credential")
    decode_secret(raw)


CHECKS: dict[str, Callable[[], Awaitable[None]]] = {
    "hardening": check_hardening,
    "tmpfs": check_tmpfs,
    "postgres": check_postgres,
    "credential": check_credential,
}


async def _passes(name: str, check: Callable[[], Awaitable[None]]) -> bool:
    try:
        await asyncio.wait_for(check(), timeout=CHECK_TIMEOUT_S)
    except Exception as exc:  # cualquier fallo de la comprobación = no listo
        # Solo el tipo: el mensaje de asyncpg puede incluir el DSN con credenciales.
        logger.warning("readyz.%s_failed error=%s", name, type(exc).__name__)
        return False
    return True


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/internal/metrics", response_class=PlainTextResponse)
async def internal_metrics() -> PlainTextResponse:
    """Instance metrics. Ingest is not public, so there is no extra boundary here."""
    samples = metrics.collect_samples(
        tmp_dir=tempfile.gettempdir(),
        slots_in_use=receptions.slots_in_use(),
        slots_total=receptions.ACTIVE_SLOTS,
        admission_open=admission.uploads_admitted(),
    )
    return PlainTextResponse(render(samples), media_type=PROMETHEUS_CONTENT_TYPE)


@app.get("/readyz", response_model=None)
async def readyz() -> JSONResponse:
    results = await asyncio.gather(*(_passes(n, c) for n, c in CHECKS.items()))
    failed = [name for name, ok in zip(CHECKS, results, strict=True) if not ok]
    if failed:
        return JSONResponse(status_code=503, content={"status": "not_ready", "failed": failed})
    return JSONResponse(status_code=200, content={"status": "ready"})

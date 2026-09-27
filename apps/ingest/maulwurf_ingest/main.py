"""ASGI del servicio de ingesta (S1.B1).

`/healthz` es liveness: solo prueba que el proceso responde.
`/readyz` es readiness: 200 solo si el proceso sigue endurecido, el tmpfs acepta
temporales y PostgreSQL responde. La capacidad por slot llega con S1.B2. Nunca llama a
proveedores pagados.

Al arrancar se ejecuta el autochequeo de `hardening`: en modo `enforce` (defecto) un
fallo impide servir, así un contenedor mal configurado no llega a aceptar audio.
"""
import asyncio
import logging
import os
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from maulwurf_ingest import hardening
from maulwurf_ingest.config import settings

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


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    enforce_hardening()
    yield


app = FastAPI(title="Maulwurf Ingest", version="0.1.0", lifespan=lifespan)


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


CHECKS: dict[str, Callable[[], Awaitable[None]]] = {
    "hardening": check_hardening,
    "tmpfs": check_tmpfs,
    "postgres": check_postgres,
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


@app.get("/readyz", response_model=None)
async def readyz() -> JSONResponse:
    results = await asyncio.gather(*(_passes(n, c) for n, c in CHECKS.items()))
    failed = [name for name, ok in zip(CHECKS, results, strict=True) if not ok]
    if failed:
        return JSONResponse(status_code=503, content={"status": "not_ready", "failed": failed})
    return JSONResponse(status_code=200, content={"status": "ready"})

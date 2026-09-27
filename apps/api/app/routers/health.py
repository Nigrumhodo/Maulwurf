"""Sondas de proceso de la API (A1.1, `/readyz` real en J1.5).

`/healthz` es liveness: solo prueba que el proceso responde y nunca toca dependencias.
`/readyz` es readiness: 200 solo si Postgres y Redis responden a tiempo; si no, 503 con la
lista de dependencias caídas. Nunca llama a proveedores pagados (Riva, LLM, Google).
"""
import asyncio
import logging
from collections.abc import Awaitable, Callable

import redis.asyncio as redis
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import settings
from app.core.db import engine

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])

CHECK_TIMEOUT_S = 2.0


async def check_postgres() -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def check_redis() -> None:
    client = redis.Redis.from_url(str(settings.redis_url), socket_connect_timeout=CHECK_TIMEOUT_S)
    try:
        await client.ping()
    finally:
        await client.aclose()


CHECKS: dict[str, Callable[[], Awaitable[None]]] = {
    "postgres": check_postgres,
    "redis": check_redis,
}


async def _passes(name: str, check: Callable[[], Awaitable[None]]) -> bool:
    try:
        await asyncio.wait_for(check(), timeout=CHECK_TIMEOUT_S)
    except Exception as exc:  # cualquier fallo de la dependencia = no listo
        # Solo el tipo: el mensaje puede incluir el DSN con credenciales.
        logger.warning("readyz.%s_failed error=%s", name, type(exc).__name__)
        return False
    return True


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", response_model=None)
async def readyz() -> JSONResponse:
    results = await asyncio.gather(*(_passes(n, c) for n, c in CHECKS.items()))
    failed = [name for name, ok in zip(CHECKS, results, strict=True) if not ok]
    if failed:
        return JSONResponse(status_code=503, content={"status": "not_ready", "failed": failed})
    return JSONResponse(status_code=200, content={"status": "ready"})

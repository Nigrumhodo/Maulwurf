"""Sondas de proceso de la API (A1.1).

`/healthz` es liveness: solo prueba que el proceso responde y nunca toca dependencias.
`/readyz` es readiness; el chequeo real de Postgres y Redis lo entrega J1.5.
"""
from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz() -> dict[str, str]:
    # Placeholder hasta J1.5: ping a Postgres y Redis con 503 si alguno falla.
    return {"status": "ok"}

"""ASGI mínimo del servicio de ingesta (placeholder de S1; J1.1/J1.8).

`/healthz` es liveness real del proceso. `/readyz` queda pendiente de J1.5/S1.B2:
no declara listo lo que todavía no comprueba (BD, temporales, capacidad).
"""
from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(title="Maulwurf Ingest", version="0.1.0")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    # Liveness: solo prueba que el proceso responde; no toca dependencias.
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> JSONResponse:
    # Placeholder honesto: la comprobación real llega en J1.5/S1.B2.
    return JSONResponse(
        status_code=503,
        content={"status": "not_ready", "detail": ["readyz pendiente (J1.5)"]},
    )

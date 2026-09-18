"""Punto de entrada FastAPI — esqueleto mínimo (A1.1).

TODO(Andres): routers de subjects/audios/me (M1, M2), CORS restrictivo,
CSRF/Origin en mutaciones.
"""
from importlib.metadata import PackageNotFoundError, version

from fastapi import FastAPI

try:
    __version__ = version("maulwurf-api")
except PackageNotFoundError:
    # Dev sin instalar el paquete: no debe romper el import.
    __version__ = "0.1.0"

app = FastAPI(title="Maulwurf API", version=__version__)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    # Liveness: solo prueba que el proceso responde; no toca dependencias.
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> dict[str, str]:
    # TODO(Andres): ping real a Postgres+pgvector y Redis; 503 si no listos (J1.5).
    return {"status": "ok"}

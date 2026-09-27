"""Punto de entrada FastAPI (A1.1): ensambla routers; la lógica vive en `routers/` y `services/`."""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version

from fastapi import FastAPI

from app.core.db import engine
from app.routers import health

try:
    __version__ = version("maulwurf-api")
except PackageNotFoundError:
    # Dev sin instalar el paquete: no debe romper el import.
    __version__ = "0.1.0"


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    # Cierra el pool al parar (compose stop / SIGTERM) en vez de abandonar conexiones.
    await engine.dispose()


app = FastAPI(title="Maulwurf API", version=__version__, lifespan=lifespan)
app.include_router(health.router)

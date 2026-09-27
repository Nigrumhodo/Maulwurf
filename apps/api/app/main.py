"""Punto de entrada FastAPI (A1.1): ensambla routers; la lógica vive en `routers/` y `services/`."""
from importlib.metadata import PackageNotFoundError, version

from fastapi import FastAPI

from app.routers import health

try:
    __version__ = version("maulwurf-api")
except PackageNotFoundError:
    # Dev sin instalar el paquete: no debe romper el import.
    __version__ = "0.1.0"

app = FastAPI(title="Maulwurf API", version=__version__)
app.include_router(health.router)

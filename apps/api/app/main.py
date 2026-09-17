"""Punto de entrada FastAPI — esqueleto mínimo (A1.1).

TODO(Andres): routers de subjects/audios/me (M1, M2), CORS restrictivo,
CSRF/Origin en mutaciones, /healthz y /readyz (J1.5).
"""
from fastapi import FastAPI

app = FastAPI(title="Maulwurf API", version="0.1.0")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}

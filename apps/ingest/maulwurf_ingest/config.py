"""Configuración del servicio de ingesta (prefijo `MAULWURF_`, igual que la API).

Solo las claves que ingest usa hoy. El `.env` compartido trae claves de otros servicios,
por eso se ignoran las ajenas en vez de rechazarlas.
"""
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MAULWURF_", extra="ignore")

    database_url: str | None = None
    # `enforce` (defecto, fail closed): sin endurecimiento el proceso no arranca.
    # `report` solo para desarrollo fuera del contenedor: arranca, pero /readyz responde 503.
    ingest_hardening: Literal["enforce", "report"] = "enforce"


settings = Settings()

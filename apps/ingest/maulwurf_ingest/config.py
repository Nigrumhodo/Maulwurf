"""Configuración del servicio de ingesta.

`MAULWURF_DATABASE_URL` se comparte con la API. Las claves propias de ingest usan el
prefijo `INGEST_` (como `INGEST_SLOTS` en `.env.example`): la API rechaza cualquier
`MAULWURF_*` que no conozca, y el `.env` es compartido.
"""
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    database_url: str | None = Field(default=None, validation_alias="MAULWURF_DATABASE_URL")
    # `enforce` (defecto, fail closed): sin endurecimiento el proceso no arranca.
    # `report` solo para desarrollo fuera del contenedor: arranca, pero /readyz responde 503.
    ingest_hardening: Literal["enforce", "report"] = Field(
        default="enforce", validation_alias="INGEST_HARDENING"
    )


settings = Settings()

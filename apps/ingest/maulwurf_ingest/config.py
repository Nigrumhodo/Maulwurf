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
    # Tope de corte al recibir. Es el provisional de compose, no un máximo aprobado (D4).
    max_upload_bytes: int = Field(
        default=209_715_200, ge=1, validation_alias="INGEST_MAX_UPLOAD_BYTES"
    )
    # Solape local entre fragmentos. No es una medida de Riva.
    fragment_overlap_s: float = Field(
        default=0.25, gt=0, le=2, validation_alias="INGEST_FRAGMENT_OVERLAP_S"
    )


settings = Settings()

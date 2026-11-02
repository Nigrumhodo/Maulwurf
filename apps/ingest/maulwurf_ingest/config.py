"""Configuración del servicio de ingesta.

`MAULWURF_DATABASE_URL` se comparte con la API. Las claves propias de ingest usan el
prefijo `INGEST_` (como `INGEST_SLOTS` en `.env.example`): la API rechaza cualquier
`MAULWURF_*` que no conozca, y el `.env` es compartido.
"""
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # J4.8: Compose passes the optional previous rotation pair through an interpolation
    # whose unset value is an empty string. Treat empty as absent so the pair stays
    # `None`/`None` and startup does not fail on a harmless blank variable.
    model_config = SettingsConfigDict(extra="ignore", env_ignore_empty=True)

    database_url: str | None = Field(default=None, validation_alias="MAULWURF_DATABASE_URL")
    # Stable identifier of the host that owns this ingest tmpfs. `resolve` refuses an
    # attestation whose `host` differs, and refuses to run without an expected host.
    ingest_host_id: str | None = Field(default=None, validation_alias="INGEST_HOST_ID")
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
    # Recovery sweeper target (S4.2). Not an instant-deletion promise: normal
    # cleanup stays immediate.
    sweep_interval_s: float = Field(
        default=60.0, ge=1, validation_alias="INGEST_SWEEP_INTERVAL_S"
    )
    # Piso que Riva aceptó en el spike. No es un máximo aprobado; el uso lo recorta a 30 s.
    fragment_seconds: int = Field(
        default=30, ge=1, validation_alias="INGEST_FRAGMENT_SECONDS"
    )
    # S4.4: the published ASR TTL is 60 min. The old 600 s child cap was below the
    # benchmark's 3 h projection (788 s local + provider). The child cap is that
    # TTL exactly: a lower value would cut off a class the publication still
    # allows, and a higher value would keep transcribing after capabilities and
    # asr_deadline promise 60 min. Tests inject a shorter SupervisorConfig directly.
    asr_timeout_s: float = Field(
        default=3_600.0,
        ge=3_600.0,
        le=3_600.0,
        validation_alias="INGEST_ASR_TIMEOUT_S",
    )
    # Unset: internal routes answer 503 and /readyz reports credential.
    # Set but short, non-hex, or containing change-me: the process refuses to start.
    credential_secret: str | None = Field(default=None, validation_alias="INGEST_CREDENTIAL_SECRET")
    credential_kid: str = Field(default="k1", validation_alias="INGEST_CREDENTIAL_KID")
    credential_previous_secret: str | None = Field(
        default=None, validation_alias="INGEST_CREDENTIAL_PREVIOUS_SECRET",
    )
    credential_previous_kid: str | None = Field(
        default=None, validation_alias="INGEST_CREDENTIAL_PREVIOUS_KID",
    )


settings = Settings()

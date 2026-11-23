"""Configuración del servicio de ingesta.

`MAULWURF_DATABASE_URL` se comparte con la API. Las claves propias de ingest usan el
prefijo `INGEST_` (como `INGEST_SLOTS` en `.env.example`): la API rechaza cualquier
`MAULWURF_*` que no conozca, y el `.env` es compartido.
"""
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Wall clock for one reception (aligned with the 30 min receive TTL for up to 1 GiB
# uploads), and the idle gap that counts as an abandoned body. A slow/hung client still
# releases its slot after RECEPTION_CHUNK_TIMEOUT_S without waiting for the wall clock.
RECEPTION_TIMEOUT_S = 1_800.0
RECEPTION_CHUNK_TIMEOUT_S = 30.0
# Platform-wide ceiling for concurrent executing ingestions/transcriptions.
SAFE_SLOT_CAP = 30
# RLIMIT_AS per ffmpeg child. Keeps each short-fragment decode bounded inside the container.
FFMPEG_ADDRESS_SPACE_FLOOR = 768 * 1024 * 1024
FFMPEG_ADDRESS_SPACE_CEILING = 1024 * 1024 * 1024


def _clamp_address_space(value: object) -> int:
    """Keep ffmpeg's virtual-memory cap inside the container's 1 GiB budget."""
    if isinstance(value, bool) or value is None:
        return FFMPEG_ADDRESS_SPACE_CEILING
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return FFMPEG_ADDRESS_SPACE_CEILING
    if parsed < FFMPEG_ADDRESS_SPACE_FLOOR:
        return FFMPEG_ADDRESS_SPACE_FLOOR
    if parsed > FFMPEG_ADDRESS_SPACE_CEILING:
        return FFMPEG_ADDRESS_SPACE_CEILING
    return parsed


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
    # Reception hard byte cut. Aligned with API `MAULWURF_UPLOAD_MAX_BYTES` (1 GiB default).
    max_upload_bytes: int = Field(
        default=1_073_741_824, ge=1, validation_alias="INGEST_MAX_UPLOAD_BYTES"
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
    # Bearer for `/internal/metrics` only. Unset: the route derives a separate
    # digest and rejects the signing secret presented in clear.
    metrics_token: str | None = Field(default=None, validation_alias="INGEST_METRICS_TOKEN")
    # `local` is the only profile that may open a plaintext Riva channel.
    environment: str = Field(default="production", validation_alias="INGEST_ENV")
    # Explicit PEM for the Riva channel. Unset uses the verified system store.
    riva_ca_file: str | None = Field(default=None, validation_alias="INGEST_RIVA_CA_FILE")
    ingest_slots: int = Field(
        default=1, ge=1, le=SAFE_SLOT_CAP, validation_alias="INGEST_SLOTS",
    )
    reception_timeout_s: float = Field(
        default=RECEPTION_TIMEOUT_S,
        gt=0,
        le=3_600.0,
        validation_alias="INGEST_RECEPTION_TIMEOUT_S",
    )
    reception_chunk_timeout_s: float = Field(
        default=RECEPTION_CHUNK_TIMEOUT_S,
        gt=0,
        le=300.0,
        validation_alias="INGEST_RECEPTION_CHUNK_TIMEOUT_S",
    )
    ffmpeg_address_space_bytes: Annotated[
        int, BeforeValidator(_clamp_address_space)
    ] = Field(
        default=FFMPEG_ADDRESS_SPACE_CEILING,
        validation_alias="INGEST_FFMPEG_AS_BYTES",
    )


settings = Settings()

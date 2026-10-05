"""Contrato público de capabilities de ingesta (A2.2).

Los límites efectivos llegan en configuración publicada por el spike, no como constantes
de aplicación. Si no existe una publicación aprobada, el shape conserva ``null`` y listas
vacías para que la UI no presente límites provisionales como capacidad disponible.
"""
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

_STRICT = ConfigDict(extra="forbid")


class IngestionAttemptTtl(BaseModel):
    """TTLs efectivos de cada fase efímera de un intento."""

    model_config = _STRICT

    upload_start_minutes: int | None = Field(default=None, ge=1)
    receive_minutes: int | None = Field(default=None, ge=1)
    asr_minutes: int | None = Field(default=None, ge=1)

    def has_values(self) -> bool:
        return all(
            value is not None
            for value in (self.upload_start_minutes, self.receive_minutes, self.asr_minutes)
        )


class AsrCapabilities(BaseModel):
    """Identidad no sensible del contrato ASR publicado."""

    model_config = _STRICT

    provider: str = Field(default="nvidia-riva", min_length=1, max_length=100)
    model: str = Field(default="whisper-large-v3", min_length=1, max_length=100)
    timestamp_precision: Literal["none"] | None = None


class IngestionCapabilities(BaseModel):
    """Configuración publicada que sirve ``GET /ingestion/capabilities``."""

    model_config = _STRICT

    source_report: str | None = Field(default=None, min_length=1, max_length=500)
    source_version: str | None = Field(default=None, min_length=1, max_length=100)
    limits_validated: bool = False
    max_upload_bytes: int | None = Field(default=None, ge=1)
    max_duration_seconds: int | None = Field(default=None, ge=1)
    accepted_input_formats: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    language_policy: Literal["explicit_select_required_no_multi"] = (
        "explicit_select_required_no_multi"
    )
    ttl: IngestionAttemptTtl = Field(default_factory=IngestionAttemptTtl)
    asr: AsrCapabilities = Field(default_factory=AsrCapabilities)
    active_slots: int | None = Field(default=None, ge=1)
    required_stages: tuple[str, ...] = ("index",)

    @model_validator(mode="after")
    def _validated_limits_are_complete(self) -> Self:
        values_present = any(
            (
                self.max_upload_bytes is not None,
                self.max_duration_seconds is not None,
                bool(self.accepted_input_formats),
                bool(self.languages),
                any(
                    value is not None
                    for value in (
                        self.ttl.upload_start_minutes,
                        self.ttl.receive_minutes,
                        self.ttl.asr_minutes,
                    )
                ),
                self.active_slots is not None,
            )
        )
        if not self.limits_validated:
            if values_present:
                raise ValueError("limits_validated=false exige límites nulos y listas vacías")
            return self
        if not self.source_report or not self.source_version:
            raise ValueError("limits_validated=true exige source_report y source_version")
        if (
            self.max_upload_bytes is None
            or self.max_duration_seconds is None
            or not self.accepted_input_formats
            or not self.languages
            or not self.ttl.has_values()
            or self.active_slots is None
        ):
            raise ValueError("limits_validated=true exige todos los límites efectivos")
        return self

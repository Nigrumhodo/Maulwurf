"""Contrato público de capabilities de ingesta (A2.2).

Los límites efectivos llegan en configuración publicada por el spike, no como constantes
de aplicación. Si no existe una publicación aprobada, los límites de tamaño, duración,
formatos, idiomas y slots conservan ``null`` y listas vacías para que la UI no presente
valores provisionales como capacidad disponible.

S4.4 separa los TTLs de los límites de tamaño/duración: el benchmark `L-S4-SG-02` mide los
TTLs efectivos (10 min para iniciar la subida, 30 min de recepción y 60 min de ASR) y los
publica con su propia fuente, mientras D4 sigue bloqueada para 200 MiB y 3 h.
"""
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

_STRICT = ConfigDict(extra="forbid")

EFFECTIVE_TTL_MINUTES: Final[dict[str, int]] = {
    "upload_start_minutes": 10,
    "receive_minutes": 30,
    "asr_minutes": 60,
}
TTL_SOURCE_REPORT: Final = "docs/eval/ttl-benchmark.md"
TTL_SOURCE_VERSION: Final = "2026-11-01"


class IngestionAttemptTtl(BaseModel):
    """TTLs efectivos de cada fase efímera de un intento.

    Los valores por defecto son los que publica S4.4 con el benchmark detrás; una
    publicación puede reemplazarlos de forma explícita, pero nunca publicar solo una parte.
    """

    model_config = _STRICT

    upload_start_minutes: int | None = Field(
        default=EFFECTIVE_TTL_MINUTES["upload_start_minutes"], ge=1
    )
    receive_minutes: int | None = Field(
        default=EFFECTIVE_TTL_MINUTES["receive_minutes"], ge=1
    )
    asr_minutes: int | None = Field(default=EFFECTIVE_TTL_MINUTES["asr_minutes"], ge=1)

    def has_values(self) -> bool:
        return all(
            value is not None
            for value in (self.upload_start_minutes, self.receive_minutes, self.asr_minutes)
        )

    def has_any_value(self) -> bool:
        return any(
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
    # Versión vigente del aviso de privacidad (M2/D2.1). No es un límite del spike: el
    # endpoint la inyecta desde `settings.privacy_notice_version`, nunca viene de la
    # configuración publicada. `None` solo en el objeto de configuración interno.
    privacy_notice_version: str | None = Field(default=None, min_length=1, max_length=64)
    max_upload_bytes: int | None = Field(default=None, ge=1)
    max_duration_seconds: int | None = Field(default=None, ge=1)
    accepted_input_formats: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    language_policy: Literal["explicit_select_required_no_multi"] = (
        "explicit_select_required_no_multi"
    )
    ttl: IngestionAttemptTtl = Field(default_factory=IngestionAttemptTtl)
    # S4.4: the benchmark that measured the TTLs. The TTL publication has its own source
    # because it can be validated while D4 (200 MiB / 3 h) is still blocked.
    ttl_source_report: str | None = Field(
        default=TTL_SOURCE_REPORT, min_length=1, max_length=500
    )
    ttl_source_version: str | None = Field(
        default=TTL_SOURCE_VERSION, min_length=1, max_length=100
    )
    asr: AsrCapabilities = Field(default_factory=AsrCapabilities)
    active_slots: int | None = Field(default=None, ge=1)
    required_stages: tuple[str, ...] = ("index",)

    @model_validator(mode="after")
    def _ttl_publication_is_complete(self) -> Self:
        if self.ttl.has_any_value() and not self.ttl.has_values():
            raise ValueError("la publicación TTL exige las tres fases o ninguna")
        if self.ttl.has_values():
            # A full publication (`limits_validated=true`) already points to the report
            # that measured every limit; an independent TTL publication needs its own.
            if not self.limits_validated and (
                not self.ttl_source_report or not self.ttl_source_version
            ):
                raise ValueError(
                    "un TTL publicado sin `limits_validated` exige "
                    "`ttl_source_report` y `ttl_source_version`"
                )
        elif self.ttl_source_report or self.ttl_source_version:
            raise ValueError("sin TTLs no se publica `ttl_source_report`/`ttl_source_version`")
        return self

    @model_validator(mode="after")
    def _validated_limits_are_complete(self) -> Self:
        values_present = any(
            (
                self.max_upload_bytes is not None,
                self.max_duration_seconds is not None,
                bool(self.accepted_input_formats),
                bool(self.languages),
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


class IngestionCapabilitiesOut(IngestionCapabilities):
    """Respuesta pública de ``GET /ingestion/capabilities``.

    El endpoint siempre inyecta la versión vigente del aviso, así que el contrato publicado
    la exige: ``None`` solo existe en el objeto de configuración interno.
    """

    privacy_notice_version: str = Field(min_length=1, max_length=64)

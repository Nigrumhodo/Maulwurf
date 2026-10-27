"""Borrador de congelación L1.4 (S1) de la salida de análisis §M4. Contrato final en L3.2.

`confidence_score` es una señal no calibrada, no una probabilidad ni permiso de agendar.
Los errores de validación se serializan sin eco del valor recibido (el transcript es dato
no confiable): siempre con `app.schemas.errors.validation_errors_redacted`, nunca con
`str(exc)` ni `errors()` por defecto, que sí incluyen `input_value`.
"""
from datetime import date
from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.validation import is_iana_timezone

ActivityType = Literal[
    "examen", "tarea", "entrega", "lectura", "recordatorio", "recomendacion", "proyecto"
]

_STRICT = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DateStatus(StrEnum):
    """Estado de resolución de la fecha de una propuesta."""

    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    MISSING = "missing"


class SourceSpan(BaseModel):
    """Rango `[inicio_char, fin_char)` en puntos de código Unicode del texto inmutable del segmento.

    No son bytes ni índices UTF-16 (JavaScript). La pertenencia del segmento a la
    clase/tenant/versión y el límite superior se validan contra la BD en L3.3/A3.6.
    """

    model_config = _STRICT

    segment_id: UUID
    inicio_char: int = Field(ge=0)
    fin_char: int = Field(gt=0)

    @model_validator(mode="after")
    def _non_empty_range(self) -> Self:
        if self.fin_char <= self.inicio_char:
            raise ValueError("fin_char debe ser mayor que inicio_char (rango vacío o invertido)")
        return self


class Source(BaseModel):
    """Evidencia de una propuesta: al menos un span, sin duplicados ni solapes por segmento."""

    model_config = _STRICT

    spans: list[SourceSpan] = Field(min_length=1)

    @model_validator(mode="after")
    def _distinct_spans(self) -> Self:
        keys = [(s.segment_id, s.inicio_char, s.fin_char) for s in self.spans]
        if len(set(keys)) != len(keys):
            raise ValueError("spans duplicados")
        ordered = sorted(keys)
        for (seg_a, _, fin_a), (seg_b, inicio_b, _) in zip(ordered, ordered[1:], strict=False):
            if seg_a == seg_b and inicio_b < fin_a:
                raise ValueError("spans solapados en el mismo segmento")
        return self


class ProposedItem(BaseModel):
    """Actividad propuesta por el análisis; se revisa en la bandeja antes de confirmarse.

    Los límites de longitud de `titulo` (200) y `detalle` (2000) son de borrador.
    `due_date` (día completo) y `due_at` (instante con zona) son mutuamente excluyentes.
    """

    model_config = _STRICT

    tipo: ActivityType
    titulo: str = Field(min_length=1, max_length=200)
    detalle: str | None = Field(default=None, max_length=2000)
    due_date: date | None = None
    due_at: AwareDatetime | None = None
    timezone: str
    all_day: bool
    date_status: DateStatus
    confidence_score: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    fuente: Source
    cita_textual: str = Field(min_length=1)

    @field_validator("timezone")
    @classmethod
    def _iana_timezone(cls, value: str) -> str:
        if not is_iana_timezone(value):
            raise ValueError("zona horaria IANA no válida")
        return value

    @model_validator(mode="after")
    def _date_rules(self) -> Self:
        if self.due_date is not None and self.due_at is not None:
            raise ValueError("due_date y due_at son mutuamente excluyentes")
        if self.date_status is DateStatus.RESOLVED:
            if self.due_date is None and self.due_at is None:
                raise ValueError("date_status=resolved exige due_date o due_at")
            if self.all_day != (self.due_date is not None):
                raise ValueError("all_day=true exige due_date; all_day=false exige due_at")
        elif self.due_date is not None or self.due_at is not None:
            raise ValueError("date_status ambiguous/missing exige due_date y due_at nulos")
        return self


class AnalysisResult(BaseModel):
    """Salida estructurada completa del análisis de una clase (§M4)."""

    model_config = _STRICT

    items: list[ProposedItem]
    resumen_clase: str
    temas: list[str]

    @field_validator("temas")
    @classmethod
    def _distinct_topics(cls, value: list[str]) -> list[str]:
        if any(not tema for tema in value):
            raise ValueError("los temas no pueden estar vacíos")
        folded = [tema.casefold() for tema in value]
        if len(set(folded)) != len(folded):
            raise ValueError("temas duplicados")
        return value

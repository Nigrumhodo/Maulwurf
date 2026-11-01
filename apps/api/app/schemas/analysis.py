"""The final §M4 analysis contract, frozen by L3.2 (S3).

This module is the structured output the analysis pipeline accepts from the LLM and the
contract A3.1 builds `tasks`/`task_evidence` on. It is not the API task shape.

Frozen rules:

- `due_date` is a whole local day and `due_at` an aware instant; both are mutually
  exclusive and coherent with `date_status` and `all_day`. `ambiguous` and `missing` carry
  neither date.
- `confidence_score` is an uncalibrated signal in [0, 1], never a certainty or a permission
  to auto-schedule.
- Every evidence span `[inicio_char, fin_char)` is measured in Unicode code points of the
  immutable segment text (not bytes and not JavaScript UTF-16 units) and carries its own
  mandatory verbatim `cita_textual`, so each `task_evidence` row has a literal quote.
- `check_against` verifies the spans and quotes against segment texts without touching the
  database. L3.3 owns membership in the class/tenant/transcript version and the DB-backed
  check.

Validation errors must be serialized with `app.schemas.errors.validation_errors_redacted`,
never with `str(exc)` or `errors()`: the transcript is untrusted data and those include
`input_value`.
"""
from collections.abc import Mapping
from datetime import date
from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.validation import is_iana_timezone

ActivityType = Literal[
    "examen", "tarea", "entrega", "lectura", "recordatorio", "recomendacion", "proyecto"
]

# Display fields may be trimmed; evidence is never trimmed because it is a literal slice.
_STRICT = ConfigDict(extra="forbid", str_strip_whitespace=True)
_NO_STRIP = ConfigDict(extra="forbid")


class DateStatus(StrEnum):
    """Estado de resolución de la fecha de una propuesta."""

    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    MISSING = "missing"


class SourceSpan(BaseModel):
    """One evidence span and its own verbatim quote.

    Offsets are Python string indices (Unicode code points) over the immutable text of
    `segment_id`, never UTF-16 units or bytes. `cita_textual` must be exactly
    `segment_text[inicio_char:fin_char]`: the length check rejects a quote that cannot be
    the slice before the segment is loaded, and `check_against` confirms the content once
    it is.
    """

    model_config = _NO_STRIP

    segment_id: UUID
    inicio_char: int = Field(ge=0)
    fin_char: int = Field(gt=0)
    cita_textual: str = Field(min_length=1)

    @field_validator("cita_textual")
    @classmethod
    def _non_blank_quote(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("la cita textual no puede estar vacía")
        return value

    @model_validator(mode="after")
    def _non_empty_range(self) -> Self:
        if self.fin_char <= self.inicio_char:
            raise ValueError("fin_char debe ser mayor que inicio_char (rango vacío o invertido)")
        if len(self.cita_textual) != self.fin_char - self.inicio_char:
            raise ValueError("la cita textual debe medir fin_char - inicio_char")
        return self

    def check_against(self, segment_text: str) -> None:
        """Check this span against the immutable text of its segment.

        :raises ValueError: if the span exceeds the text or the quote is not the exact
            Unicode slice. The messages never include the quote or the transcript.
        """
        if self.fin_char > len(segment_text):
            raise ValueError("fin_char excede el texto del segmento")
        if segment_text[self.inicio_char : self.fin_char] != self.cita_textual:
            raise ValueError("la cita textual no coincide con el segmento")


class Source(BaseModel):
    """Evidence of a proposal: at least one span, without duplicates or overlaps."""

    model_config = _NO_STRIP

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

    def check_against(self, segment_texts: Mapping[UUID, str]) -> None:
        """Check every span against the text of its segment.

        The mapping is the caller's class/tenant/version-scoped view (L3.3); this method
        only proves that the segment is present and that each quote is the exact slice.
        """
        for span in self.spans:
            text = segment_texts.get(span.segment_id)
            if text is None:
                raise ValueError("segmento desconocido en la evidencia")
            span.check_against(text)


class ProposedItem(BaseModel):
    """Activity proposed by the analysis; it is reviewed before confirmation.

    The length limits of `titulo` (200) and `detalle` (2000) are part of the frozen
    contract. `due_date` (whole day) and `due_at` (aware instant) are mutually exclusive.
    `fuente` is required: an extraction is always backed by at least one literal quote.
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

    def check_against(self, segment_texts: Mapping[UUID, str]) -> None:
        """Delegate the evidence check to `Source`."""
        self.fuente.check_against(segment_texts)


class AnalysisResult(BaseModel):
    """Complete structured output of the analysis of one class (§M4)."""

    model_config = _STRICT

    items: list[ProposedItem]
    resumen_clase: str = Field(min_length=1)
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

    def check_against(self, segment_texts: Mapping[UUID, str]) -> None:
        """Check every item's evidence against the supplied segment texts."""
        for item in self.items:
            item.check_against(segment_texts)

"""Formato de un ítem del dataset G4 (L1.5, S1): pregunta de evaluación del RAG.

S1 fija solo el formato; las preguntas reales llegan en S2/S3. Cada ítem se ancla a una
clase sintética del corpus de evaluación (`class_ref`), nunca a una grabación real. La
evidencia apunta a `transcript_version` + `segment_ordinal` porque los `segment_id` todavía
no existen y el ordinal es estable dentro de una versión de transcript. Los offsets son
puntos de código Unicode `[inicio_char, fin_char)` del texto del segmento.
"""
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

SLUG_PATTERN = r"^[a-z0-9-]{3,64}$"
# BCP-47 en minúsculas: subetiqueta primaria de 2-3 letras y subetiquetas opcionales.
LANGUAGE_PATTERN = r"^[a-z]{2,3}(-[a-z0-9]{2,8})*$"

Partition = Literal["development", "test"]


class ExpectedEvidence(BaseModel):
    """Fragmento de la clase sintética que justifica la respuesta."""

    model_config = ConfigDict(extra="forbid")

    class_ref: str = Field(pattern=SLUG_PATTERN)
    transcript_version: int = Field(ge=1)
    segment_ordinal: int = Field(ge=0)
    inicio_char: int = Field(ge=0)
    fin_char: int = Field(gt=0)
    cita_textual: str = Field(min_length=1)

    @model_validator(mode="after")
    def _span_not_empty(self) -> Self:
        if self.fin_char <= self.inicio_char:
            raise ValueError("fin_char debe ser mayor que inicio_char")
        return self


class G4Item(BaseModel):
    """Una pregunta del dataset G4, respondible o no respondible."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(pattern=SLUG_PATTERN)
    language: str = Field(pattern=LANGUAGE_PATTERN)
    partition: Partition
    question: str = Field(min_length=5)
    answerable: bool
    expected_evidence: list[ExpectedEvidence]
    reference_answer: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if not self.id.startswith(f"{self.language}-"):
            raise ValueError("id debe empezar por '<language>-'")
        if self.answerable and not self.expected_evidence:
            raise ValueError("un ítem respondible necesita al menos una evidencia")
        if not self.answerable and self.expected_evidence:
            raise ValueError("un ítem no respondible no lleva evidencia")
        if not self.answerable and self.reference_answer is not None:
            raise ValueError("un ítem no respondible no lleva reference_answer")
        return self

"""Borrador de congelación L1.4 (S1) del chunk §M6 y sus enlaces `chunk_segments`.

Contrato final en L3.2; el chunking real (cómo se concatena el texto) lo define L2.1. Los
offsets son `[inicio_char, fin_char)` en puntos de código Unicode del texto del segmento.
"""
from collections.abc import Mapping
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

_STRICT = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ChunkSegmentLink(BaseModel):
    """Enlace chunk ↔ segmento con el rango de caracteres del segmento que cubre el chunk."""

    model_config = _STRICT

    segment_id: UUID
    segment_ordinal: int = Field(ge=0)
    inicio_char: int = Field(ge=0)
    fin_char: int = Field(gt=0)

    @model_validator(mode="after")
    def _non_empty_range(self) -> Self:
        if self.fin_char <= self.inicio_char:
            raise ValueError("fin_char debe ser mayor que inicio_char (rango vacío o invertido)")
        return self


class Chunk(BaseModel):
    """Unidad de indexación; no es la unidad mínima de evidencia (lo es el segmento).

    `t_start`/`t_end` (segundos) son nulos cuando `timestamp_precision=none`.
    """

    model_config = _STRICT

    audio_id: UUID
    transcript_version: int = Field(ge=1)
    ordinal: int = Field(ge=0)
    text: str = Field(min_length=1)
    segments: list[ChunkSegmentLink] = Field(min_length=1)
    t_start: float | None = Field(default=None, allow_inf_nan=False)
    t_end: float | None = Field(default=None, allow_inf_nan=False)

    @model_validator(mode="after")
    def _links_and_times(self) -> Self:
        ordinals = [link.segment_ordinal for link in self.segments]
        if any(b <= a for a, b in zip(ordinals, ordinals[1:], strict=False)):
            raise ValueError("segments debe ir en segment_ordinal estrictamente creciente")
        ids = [link.segment_id for link in self.segments]
        if len(set(ids)) != len(ids):
            raise ValueError("segment_id repetido en segments")
        if (self.t_start is None) != (self.t_end is None):
            raise ValueError("t_start y t_end van juntos o ambos nulos")
        if self.t_start is not None and self.t_end is not None:
            if not 0 <= self.t_start < self.t_end:
                raise ValueError("se exige 0 <= t_start < t_end")
        return self

    def check_against(self, segment_texts: Mapping[UUID, str]) -> None:
        """Comprueba que cada enlace es reconstruible desde el texto de su segmento.

        :raises ValueError: si un segmento no existe en el mapa o el rango excede su texto.
        """
        for link in self.segments:
            text = segment_texts.get(link.segment_id)
            if text is None:
                raise ValueError(f"segmento desconocido en el ordinal {link.segment_ordinal}")
            if link.fin_char > len(text):
                raise ValueError(
                    f"fin_char excede el texto del segmento en el ordinal {link.segment_ordinal}"
                )

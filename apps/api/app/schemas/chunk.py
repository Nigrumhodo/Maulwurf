"""The §M6 `Chunk` contract, persisted by `chunks`/`chunk_segments` (S2 L2.9).

The contract is the object the chunker emits (L2.1) and the indexer stores (L2.2): every
field maps to a column of migration `0002_s2_schema` without renaming or reshaping, except
`user_id` (tenant context, never part of a chunk) and `tsv` (derived with the transcript's
full-text configuration at write time).

`ChunkSegmentLink.overlap_span` is the validated shape of the `chunk_segments.overlap_span`
jsonb column. It keeps the segment-local range the chunk covers and, when the chunk starts
before that range because of the inter-chunk overlap, the shared sub-range. Offsets are
Python string indices (Unicode code points), never UTF-16 units or bytes.
"""
from collections.abc import Mapping
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

# No `str_strip_whitespace`: trimming `content` would shift it away from `char_start`/`char_end`.
_STRICT = ConfigDict(extra="forbid")


class OverlapRegion(BaseModel):
    """Segment-local region shared with the previous chunk, `[inicio_char, fin_char)`."""

    model_config = _STRICT

    inicio_char: int = Field(ge=0)
    fin_char: int = Field(gt=0)
    tokens: int = Field(ge=0)

    @model_validator(mode="after")
    def _non_empty_range(self) -> Self:
        if self.fin_char <= self.inicio_char:
            raise ValueError("fin_char debe ser mayor que inicio_char (rango vacío o invertido)")
        return self


class ChunkSegmentSpan(BaseModel):
    """Validated payload of `chunk_segments.overlap_span`.

    `segment_ordinal`, `inicio_char` and `fin_char` locate the segment and the part of it
    the chunk covers; `overlap` is the sub-range shared with the previous chunk, absent for
    the first chunk or when this segment falls outside the overlap. The ordinal is stored
    here so a link row is self-describing and ordered without joining `segments`.
    """

    model_config = _STRICT

    segment_ordinal: int = Field(ge=0)
    inicio_char: int = Field(ge=0)
    fin_char: int = Field(gt=0)
    overlap: OverlapRegion | None = None

    @model_validator(mode="after")
    def _ranges(self) -> Self:
        if self.fin_char <= self.inicio_char:
            raise ValueError("fin_char debe ser mayor que inicio_char (rango vacío o invertido)")
        if self.overlap is not None:
            if self.overlap.inicio_char < self.inicio_char or self.overlap.fin_char > self.fin_char:
                raise ValueError("overlap debe quedar dentro del rango del enlace")
        return self


class ChunkSegmentLink(BaseModel):
    """One `chunk_segments` row: the segment and its `overlap_span` jsonb payload."""

    model_config = _STRICT

    segment_id: UUID
    overlap_span: ChunkSegmentSpan


class Chunk(BaseModel):
    """Indexable unit; the segment, not the chunk, is the minimum evidence unit.

    `t_start`/`t_end` (seconds) are null when `timestamp_precision=none`. The links are
    ordered by `overlap_span.segment_ordinal`, which is strictly increasing.
    """

    model_config = _STRICT

    audio_id: UUID
    transcript_id: UUID
    transcript_version: int = Field(ge=1)
    index_version: str = Field(min_length=1)
    ordinal: int = Field(ge=0)
    content: str = Field(min_length=1)
    char_start: int = Field(ge=0)
    char_end: int = Field(gt=0)
    token_count: int = Field(ge=0)
    segments: list[ChunkSegmentLink] = Field(min_length=1)
    t_start: float | None = Field(default=None, allow_inf_nan=False)
    t_end: float | None = Field(default=None, allow_inf_nan=False)

    @model_validator(mode="after")
    def _links_and_times(self) -> Self:
        if self.char_end <= self.char_start:
            raise ValueError("char_end debe ser mayor que char_start (rango vacío o invertido)")
        # `content` is the exact transcript slice; a mismatch would persist offsets that lie.
        if len(self.content) != self.char_end - self.char_start:
            raise ValueError("content no mide char_end - char_start")
        ordinals = [link.overlap_span.segment_ordinal for link in self.segments]
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
        """Check each link's segment-local bounds against the stored segment text.

        Verifies that the segment exists and that `fin_char` does not exceed its length. It
        does NOT rebuild `content` nor require contiguous links: L2.1 owns the concatenation
        rule. The overlap region is already validated inside the link range.

        :raises ValueError: if a segment is unknown or a range exceeds its text.
        """
        for link in self.segments:
            text = segment_texts.get(link.segment_id)
            if text is None:
                raise ValueError(
                    f"segmento desconocido en el ordinal {link.overlap_span.segment_ordinal}"
                )
            if link.overlap_span.fin_char > len(text):
                raise ValueError(
                    "fin_char excede el texto del segmento en el ordinal "
                    f"{link.overlap_span.segment_ordinal}"
                )

"""Response contract for `GET /search` (L2.4/A2.11, S2.md §1.3 and DISENO §3.9)."""

from datetime import date

from pydantic import BaseModel


class SearchSubjectOut(BaseModel):
    id: str
    name: str
    color: str


class SearchResultOut(BaseModel):
    chunk_id: str
    audio_id: str
    subject: SearchSubjectOut
    class_date: date
    snippet: str
    segment_id: str | None
    t_start: float | None
    t_end: float | None
    score_rrf: float


class SearchResponseOut(BaseModel):
    results: list[SearchResultOut]
    took_ms: int
    # Additive to the frozen §1.3 example: without it the keyset pagination is not
    # consumable. `null` means there are no more hits inside the bounded RRF window.
    next_cursor: str | None = None

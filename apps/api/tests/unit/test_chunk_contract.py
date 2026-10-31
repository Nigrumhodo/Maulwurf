"""U-S2-JL-11: a §M6 `Chunk` persists into `chunks`/`chunk_segments` with no remapping.

The real chunker (L2.1, `app.services.chunking`) is the producer: every shape it can emit
must be a valid `Chunk` and must reach the ORM rows field by field. `user_id` comes from
the tenant context and `tsv` is derived at write time, so neither is part of the contract.
"""
import json
import uuid
from typing import Any

import pytest

from app.models import Chunk as ChunkRow
from app.models import ChunkSegment as ChunkSegmentRow
from app.schemas.chunk import Chunk, ChunkSegmentLink
from app.services import indexing
from app.services.chunking import SegmentInput, chunk_segments


class _RecordingSession:
    """Minimal session: the persistence path only calls `add` here."""

    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def chunks(self) -> list[ChunkRow]:
        return [obj for obj in self.added if isinstance(obj, ChunkRow)]

    def links(self) -> list[ChunkSegmentRow]:
        return [obj for obj in self.added if isinstance(obj, ChunkSegmentRow)]


def _segment(
    ordinal: int,
    text: str,
    char_start: int,
    *,
    t_start: float | None = None,
    t_end: float | None = None,
) -> SegmentInput:
    return SegmentInput(
        id=uuid.uuid4(),
        ordinal=ordinal,
        text=text,
        char_start=char_start,
        char_end=char_start + len(text),
        t_start=t_start,
        t_end=t_end,
    )


def _segments(texts: list[str], *, times: bool) -> tuple[str, list[SegmentInput]]:
    text = ""
    segments: list[SegmentInput] = []
    for ordinal, piece in enumerate(texts):
        segments.append(
            _segment(
                ordinal,
                piece,
                len(text),
                t_start=ordinal * 10.0 if times else None,
                t_end=(ordinal + 1) * 10.0 if times else None,
            )
        )
        text += piece
    return text, segments


def _chunks(
    texts: list[str],
    *,
    target_tokens: int = 800,
    overlap_tokens: int = 100,
    times: bool = True,
) -> list[Chunk]:
    text, segments = _segments(texts, times=times)
    return chunk_segments(
        audio_id=uuid.uuid4(),
        transcript_id=uuid.uuid4(),
        transcript_version=1,
        index_version="idx-v1",
        transcript_text=text,
        segments=segments,
        target_tokens=target_tokens,
        overlap_tokens=overlap_tokens,
        count_tokens=_word_count,
    )


def _word_count(text: str) -> int:
    """Deterministic counter for shape tests; the exact tokenizer needs a network cache."""
    return len(text.split())


SHAPES: dict[str, list[Chunk]] = {}


def _all_shapes() -> dict[str, list[Chunk]]:
    if not SHAPES:
        SHAPES["single-segment"] = _chunks(["Una clase completa."])
        SHAPES["multi-segment"] = _chunks(
            ["Uno. Dos. ", "Tres. Cuatro."], target_tokens=4, overlap_tokens=2
        )
        SHAPES["overlap"] = _chunks(
            ["a a a. b b b. c c c. d d d. e e e. f f f."], target_tokens=6, overlap_tokens=4
        )
        SHAPES["oversized-sentence"] = _chunks(["palabra " * 40], target_tokens=8)
        SHAPES["unicode"] = _chunks(["día 🎓 微積分 café. Otra vez."])
        SHAPES["without-timestamps"] = _chunks(["Sin tiempos."], times=False)
        SHAPES["zero-duration"] = chunk_segments(
            audio_id=uuid.uuid4(),
            transcript_id=uuid.uuid4(),
            transcript_version=1,
            index_version="idx-v1",
            transcript_text="Cero. ",
            segments=[_segment(0, "Cero. ", 0, t_start=5.0, t_end=5.0)],
            count_tokens=_word_count,
        )
    return SHAPES


def test_chunker_emits_the_contract_directly() -> None:
    for name, chunks in _all_shapes().items():
        assert chunks, name
        for chunk in chunks:
            assert isinstance(chunk, Chunk), name
            assert Chunk.model_validate(chunk.model_dump()) == chunk


def test_every_chunker_shape_validates_check_against() -> None:
    text, segments = _segments(["Uno. Dos. ", "Tres."], times=True)
    chunks = chunk_segments(
        audio_id=uuid.uuid4(),
        transcript_id=uuid.uuid4(),
        transcript_version=1,
        index_version="idx-v1",
        transcript_text=text,
        segments=segments,
        target_tokens=4,
        overlap_tokens=0,
        count_tokens=_word_count,
    )
    texts = {segment.id: segment.text for segment in segments}
    for chunk in chunks:
        chunk.check_against(texts)


def test_every_contract_field_maps_to_a_same_named_column() -> None:
    chunk_columns = {column.name for column in ChunkRow.__table__.columns}
    assert set(Chunk.model_fields) - {"segments"} <= chunk_columns
    link_columns = {column.name for column in ChunkSegmentRow.__table__.columns}
    assert set(ChunkSegmentLink.model_fields) <= link_columns


def test_overlap_span_is_json_serializable() -> None:
    chunks = _all_shapes()["overlap"]

    assert any(link.overlap_span.overlap is not None for chunk in chunks for link in chunk.segments)
    for chunk in chunks:
        for link in chunk.segments:
            payload = link.overlap_span.model_dump(exclude_none=True)
            assert json.loads(json.dumps(payload)) == payload


def test_chunk_persists_with_no_extra_mapping() -> None:
    user_id = uuid.uuid4()
    for name, chunks in _all_shapes().items():
        for chunk in chunks:
            db = _RecordingSession()
            indexing._persist_chunk(
                db,  # type: ignore[arg-type]
                user_id=user_id,
                fts_config="spanish",
                chunk=chunk,
            )

            (row,) = db.chunks()
            assert row.user_id == user_id
            assert row.audio_id == chunk.audio_id
            assert row.transcript_id == chunk.transcript_id
            assert row.transcript_version == chunk.transcript_version
            assert row.index_version == chunk.index_version
            assert row.ordinal == chunk.ordinal
            assert row.content == chunk.content
            assert row.char_start == chunk.char_start
            assert row.char_end == chunk.char_end
            assert row.token_count == chunk.token_count
            assert row.t_start == chunk.t_start
            assert row.t_end == chunk.t_end
            assert row.tsv is not None
            for column in ChunkRow.__table__.columns:
                if not column.nullable:
                    assert getattr(row, column.name) is not None, (name, column.name)

            links = db.links()
            assert len(links) == len(chunk.segments)
            for link, expected in zip(links, chunk.segments, strict=True):
                assert link.user_id == user_id
                assert link.chunk_id == row.id
                assert link.segment_id == expected.segment_id
                assert link.overlap_span == expected.overlap_span.model_dump(exclude_none=True)
                for column in ChunkSegmentRow.__table__.columns:
                    if not column.nullable:
                        assert getattr(link, column.name) is not None, (name, column.name)


def test_zero_duration_times_are_not_published_as_a_range() -> None:
    (chunk,) = _all_shapes()["zero-duration"]

    assert chunk.t_start is None
    assert chunk.t_end is None


def test_empty_transcript_emits_no_chunks() -> None:
    assert (
        chunk_segments(
            audio_id=uuid.uuid4(),
            transcript_id=uuid.uuid4(),
            transcript_version=1,
            index_version="idx-v1",
            transcript_text="  \n ",
            segments=[],
        )
        == []
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"segment_ordinal": 0, "inicio_char": 0, "fin_char": 5},
        {
            "segment_ordinal": 0,
            "inicio_char": 0,
            "fin_char": 5,
            "overlap": {"inicio_char": 1, "fin_char": 5, "tokens": 2},
        },
    ],
)
def test_overlap_span_shape_is_validated(payload: dict[str, Any]) -> None:
    link = {"segment_id": str(uuid.uuid4()), "overlap_span": payload}
    chunk = {
        "audio_id": str(uuid.uuid4()),
        "transcript_id": str(uuid.uuid4()),
        "transcript_version": 1,
        "index_version": "idx-v1",
        "ordinal": 0,
        "content": "Hola.",
        "char_start": 0,
        "char_end": 5,
        "token_count": 2,
        "segments": [link],
    }

    assert Chunk.model_validate(chunk).segments[0].overlap_span.model_dump(
        exclude_none=True
    ) == payload

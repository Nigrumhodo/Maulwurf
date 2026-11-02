"""U-S1-JL-02: the §M6 `Chunk` contract, aligned with `chunks`/`chunk_segments` (L2.9).

Every offset is a Unicode code point, never a UTF-16 unit. The link payload is the
validated shape of the `chunk_segments.overlap_span` jsonb column, so persisting a
`Chunk` copies fields instead of remapping them.
"""
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.schemas.chunk import Chunk

SEG_A = UUID("3f1c9a52-7d4e-4b8a-9c1e-2a6b5d8e0f11")
SEG_B = UUID("9b2e4c71-1a3f-4d6e-8b5c-7e0a2f9d4c33")
UNICODE_TEXT = "día 🎓 微積分 café"


def _link(
    segment_id: UUID,
    ordinal: int,
    inicio: int,
    fin: int,
    *,
    overlap: dict[str, int] | None = None,
) -> dict[str, Any]:
    span: dict[str, Any] = {
        "segment_ordinal": ordinal,
        "inicio_char": inicio,
        "fin_char": fin,
    }
    if overlap is not None:
        span["overlap"] = overlap
    return {"segment_id": str(segment_id), "overlap_span": span}


def _chunk(**overrides: Any) -> dict[str, Any]:
    chunk: dict[str, Any] = {
        "audio_id": str(uuid4()),
        "transcript_id": str(uuid4()),
        "transcript_version": 1,
        "index_version": "idx-v1",
        "ordinal": 0,
        "content": UNICODE_TEXT,
        "char_start": 0,
        "char_end": len(UNICODE_TEXT),
        "token_count": 6,
        "segments": [_link(SEG_A, 0, 0, len(UNICODE_TEXT))],
        "t_start": 0.0,
        "t_end": 12.5,
    }
    chunk.update(overrides)
    return chunk


def test_offsets_are_unicode_code_points_not_utf16() -> None:
    # 🎓 occupies one code point and two UTF-16 units: the contract uses code points.
    assert len(UNICODE_TEXT) == 14
    assert len(UNICODE_TEXT.encode("utf-16-le")) // 2 == 15

    chunk = Chunk.model_validate(_chunk())
    chunk.check_against({SEG_A: UNICODE_TEXT})


def test_content_is_the_global_slice_of_the_declared_range() -> None:
    chunk = Chunk.model_validate(_chunk())

    assert chunk.content == UNICODE_TEXT[chunk.char_start : chunk.char_end]
    assert len(chunk.content) == chunk.char_end - chunk.char_start


def test_span_past_the_end_fails_check_against() -> None:
    chunk = Chunk.model_validate(
        _chunk(segments=[_link(SEG_A, 0, 0, len(UNICODE_TEXT) + 1)])
    )

    with pytest.raises(ValueError, match="excede"):
        chunk.check_against({SEG_A: UNICODE_TEXT})


def test_unknown_segment_fails_check_against() -> None:
    chunk = Chunk.model_validate(_chunk())

    with pytest.raises(ValueError, match="desconocido"):
        chunk.check_against({SEG_B: UNICODE_TEXT})


def test_check_against_validates_bounds_not_concatenation() -> None:
    # Frozen decision (QA H2): L2.1 owns how `content` is concatenated; this contract
    # only validates each link's local bounds against its segment text.
    chunk = Chunk.model_validate(_chunk(content="x" * len(UNICODE_TEXT)))

    chunk.check_against({SEG_A: UNICODE_TEXT})


def test_multi_segment_chunk_is_valid() -> None:
    chunk = Chunk.model_validate(
        _chunk(segments=[_link(SEG_A, 3, 4, len(UNICODE_TEXT)), _link(SEG_B, 4, 0, 6)])
    )

    chunk.check_against({SEG_A: UNICODE_TEXT, SEG_B: "y otra"})


def test_link_keeps_the_optional_overlap_sub_range() -> None:
    chunk = Chunk.model_validate(
        _chunk(
            segments=[
                _link(
                    SEG_A,
                    0,
                    0,
                    len(UNICODE_TEXT),
                    overlap={"inicio_char": 0, "fin_char": 5, "tokens": 3},
                )
            ]
        )
    )

    overlap = chunk.segments[0].overlap_span.overlap
    assert overlap is not None
    assert (overlap.inicio_char, overlap.fin_char, overlap.tokens) == (0, 5, 3)


def test_content_is_kept_verbatim() -> None:
    content = "  con espacios \n"
    chunk = Chunk.model_validate(_chunk(content=content, char_end=len(content)))

    assert chunk.content == content


def test_without_timestamps_is_valid() -> None:
    Chunk.model_validate(_chunk(t_start=None, t_end=None))


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param(
            {"segments": [_link(SEG_B, 4, 0, 5), _link(SEG_A, 3, 0, 5)]}, id="desordenados"
        ),
        pytest.param(
            {"segments": [_link(SEG_A, 3, 0, 5), _link(SEG_B, 3, 0, 5)]}, id="ordinal-repetido"
        ),
        pytest.param(
            {"segments": [_link(SEG_A, 3, 0, 5), _link(SEG_A, 4, 0, 5)]}, id="segment_id-repetido"
        ),
        pytest.param({"segments": []}, id="sin-enlaces"),
        pytest.param({"segments": [_link(SEG_A, 0, 5, 5)]}, id="rango-vacio"),
        pytest.param({"segments": [_link(SEG_A, 0, 6, 5)]}, id="rango-invertido"),
        pytest.param({"segments": [_link(SEG_A, 0, -1, 5)]}, id="inicio-negativo"),
        pytest.param({"segments": [_link(SEG_A, -1, 0, 5)]}, id="ordinal-negativo"),
        pytest.param(
            {"segments": [_link(SEG_A, 0, 0, 5, overlap={"inicio_char": 5, "fin_char": 6})]},
            id="overlap-fuera-del-enlace",
        ),
        pytest.param(
            {"segments": [_link(SEG_A, 0, 0, 5, overlap={"inicio_char": 4, "fin_char": 4})]},
            id="overlap-vacio",
        ),
        pytest.param(
            {"segments": [_link(SEG_A, 0, 0, 5, overlap={"inicio_char": -1, "fin_char": 3})]},
            id="overlap-inicio-negativo",
        ),
        pytest.param(
            {"segments": [_link(SEG_A, 0, 0, 5, overlap={"inicio_char": 0, "fin_char": 3})]},
            id="overlap-sin-tokens",
        ),
        pytest.param({"t_end": None}, id="solo-t_start"),
        pytest.param({"t_start": None}, id="solo-t_end"),
        pytest.param({"t_start": 3.0, "t_end": 3.0}, id="t_start-igual-t_end"),
        pytest.param({"t_start": -1.0}, id="t_start-negativo"),
        pytest.param({"t_end": float("inf")}, id="t_end-inf"),
        pytest.param({"content": ""}, id="content-vacio"),
        pytest.param({"content": "corto"}, id="content-no-coincide-con-el-rango"),
        pytest.param({"char_end": 0}, id="rango-global-vacio"),
        pytest.param({"char_end": -1}, id="rango-global-invertido"),
        pytest.param({"index_version": ""}, id="index-version-vacia"),
        pytest.param({"token_count": -1}, id="token-count-negativo"),
        pytest.param({"transcript_version": 0}, id="version-0"),
        pytest.param({"transcript_id": None}, id="sin-transcript-id"),
        pytest.param({"user_id": str(uuid4())}, id="campo-extra"),
    ],
)
def test_invalid_chunks(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Chunk.model_validate(_chunk(**overrides))

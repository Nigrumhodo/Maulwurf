"""U-S1-JL-02: borrador §M6 — offsets Unicode `[inicio, fin)` y enlaces `chunk_segments`."""
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.schemas.chunk import Chunk

SEG_A = UUID("3f1c9a52-7d4e-4b8a-9c1e-2a6b5d8e0f11")
SEG_B = UUID("9b2e4c71-1a3f-4d6e-8b5c-7e0a2f9d4c33")
UNICODE_TEXT = "día 🎓 微積分 café"


def _link(segment_id: UUID, ordinal: int, inicio: int, fin: int) -> dict[str, Any]:
    return {
        "segment_id": str(segment_id),
        "segment_ordinal": ordinal,
        "inicio_char": inicio,
        "fin_char": fin,
    }


def _chunk(**overrides: Any) -> dict[str, Any]:
    chunk: dict[str, Any] = {
        "audio_id": str(uuid4()),
        "transcript_version": 1,
        "ordinal": 0,
        "text": UNICODE_TEXT,
        "segments": [_link(SEG_A, 0, 0, len(UNICODE_TEXT))],
        "t_start": 0.0,
        "t_end": 12.5,
    }
    chunk.update(overrides)
    return chunk


def test_offsets_are_unicode_code_points_not_utf16() -> None:
    # 🎓 ocupa un punto de código y dos unidades UTF-16: el contrato usa puntos de código.
    assert len(UNICODE_TEXT) == 14
    assert len(UNICODE_TEXT.encode("utf-16-le")) // 2 == 15

    chunk = Chunk.model_validate(_chunk())
    chunk.check_against({SEG_A: UNICODE_TEXT})


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
    # Decisión congelada (QA H2): la concatenación `text` ↔ slices la define L2.1;
    # aquí solo se validan límites por enlace.
    chunk = Chunk.model_validate(_chunk(text="texto ajeno a los enlaces"))

    chunk.check_against({SEG_A: UNICODE_TEXT})


def test_multi_segment_chunk_is_valid() -> None:
    chunk = Chunk.model_validate(
        _chunk(segments=[_link(SEG_A, 3, 4, len(UNICODE_TEXT)), _link(SEG_B, 4, 0, 6)])
    )

    chunk.check_against({SEG_A: UNICODE_TEXT, SEG_B: "y otra"})


def test_text_is_kept_verbatim() -> None:
    chunk = Chunk.model_validate(_chunk(text="  con espacios \n"))

    assert chunk.text == "  con espacios \n"


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
        pytest.param({"t_end": None}, id="solo-t_start"),
        pytest.param({"t_start": None}, id="solo-t_end"),
        pytest.param({"t_start": 3.0, "t_end": 3.0}, id="t_start-igual-t_end"),
        pytest.param({"t_start": -1.0}, id="t_start-negativo"),
        pytest.param({"t_end": float("inf")}, id="t_end-inf"),
        pytest.param({"text": ""}, id="texto-vacio"),
        pytest.param({"transcript_version": 0}, id="version-0"),
        pytest.param({"user_id": str(uuid4())}, id="campo-extra"),
    ],
)
def test_invalid_chunks(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Chunk.model_validate(_chunk(**overrides))

"""U-S3-JL-01/02 (L3.3): post-LLM membership and the literal quote check.

Enums and ranges stay in U-S3-JL-01's schema suite. Here the segment must belong to the
class view (another class, tenant or version is simply absent) and the quote must be the
exact Unicode slice of that segment. The SQL is compiled, not executed; A3.6 owns the
PostgreSQL reconstruction.
"""

from typing import Any, cast
from uuid import UUID

import pytest
from sqlalchemy import Select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.analysis import AnalysisResult
from app.services.analysis_validation import (
    AnalysisValidationError,
    ClassTranscript,
    class_segments_statement,
    class_transcript_statement,
    load_class_transcript,
    validate_analysis,
)

USER = UUID("11111111-1111-4111-8111-111111111111")
AUDIO = UUID("22222222-2222-4222-8222-222222222222")
TRANSCRIPT = UUID("33333333-3333-4333-8333-333333333333")
SEGMENT = UUID("3f1c9a52-7d4e-4b8a-9c1e-2a6b5d8e0f11")
OTHER = UUID("9b2e4c71-1a3f-4d6e-8b5c-7e0a2f9d4c33")
TEXT = "xxbeta yybeta"
QUOTE = "UNIQUE_QUOTE_ZX9"


def _span(segment: UUID, start: int, end: int, quote: str) -> dict[str, Any]:
    return {
        "segment_id": str(segment),
        "inicio_char": start,
        "fin_char": end,
        "cita_textual": quote,
    }


def _result(*spans: dict[str, Any]) -> AnalysisResult:
    return AnalysisResult.model_validate(
        {
            "items": [
                {
                    "tipo": "tarea",
                    "titulo": "Problem set",
                    "due_date": None,
                    "due_at": None,
                    "timezone": "America/Mexico_City",
                    "all_day": False,
                    "date_status": "missing",
                    "fuente": {"spans": list(spans)},
                }
            ],
            "resumen_clase": "A class about evidence.",
            "temas": ["evidence"],
        }
    )


def _view(segments: dict[UUID, str]) -> ClassTranscript:
    return ClassTranscript(
        user_id=USER,
        audio_id=AUDIO,
        transcript_id=TRANSCRIPT,
        transcript_version=2,
        segments=segments,
    )


def test_the_exact_slice_is_accepted() -> None:
    result = _result(_span(SEGMENT, 2, 6, "beta"))
    validate_analysis(result, _view({SEGMENT: TEXT}))


def test_a_quote_that_only_occurs_elsewhere_is_rejected() -> None:
    result = _result(_span(SEGMENT, 0, 4, "beta"))
    with pytest.raises(AnalysisValidationError) as caught:
        validate_analysis(result, _view({SEGMENT: TEXT}))
    assert caught.value.code == "quote_mismatch"
    assert "beta" not in str(caught.value)


def test_normalization_is_not_enough() -> None:
    nfd = "cafe\u0301"
    quote = "café"
    result = _result(_span(SEGMENT, 0, len(quote), quote))
    with pytest.raises(AnalysisValidationError) as caught:
        validate_analysis(result, _view({SEGMENT: nfd}))
    assert caught.value.code == "quote_mismatch"
    assert quote not in str(caught.value)
    assert quote not in str(caught.value.__cause__)


def test_whitespace_is_part_of_the_slice() -> None:
    text = "  hello"
    validate_analysis(result := _result(_span(SEGMENT, 0, 7, "  hello")), _view({SEGMENT: text}))
    assert result.items[0].fuente.spans[0].cita_textual == "  hello"
    with pytest.raises(AnalysisValidationError) as caught:
        validate_analysis(_result(_span(SEGMENT, 0, 7, " hello ")), _view({SEGMENT: text}))
    assert caught.value.code == "quote_mismatch"


def test_a_span_past_the_segment_is_rejected() -> None:
    result = _result(_span(SEGMENT, 0, 3, "hii"))
    with pytest.raises(AnalysisValidationError) as caught:
        validate_analysis(result, _view({SEGMENT: "hi"}))
    assert caught.value.code == "span_out_of_range"
    assert "hii" not in str(caught.value)


def test_a_segment_outside_the_class_view_is_rejected() -> None:
    result = _result(_span(OTHER, 2, 6, "beta"))
    other_class = _view({OTHER: TEXT})
    validate_analysis(result, other_class)
    with pytest.raises(AnalysisValidationError) as caught:
        validate_analysis(result, _view({SEGMENT: TEXT}))
    assert caught.value.code == "segment_not_in_class"
    assert "beta" not in str(caught.value)


def test_unicode_offsets_stay_code_points() -> None:
    text = "día 🎓"
    result = _result(_span(SEGMENT, 0, len(text), text))
    validate_analysis(result, _view({SEGMENT: text}))


def test_one_foreign_span_rejects_the_whole_result() -> None:
    result = AnalysisResult.model_validate(
        {
            "items": [
                _result(_span(SEGMENT, 2, 6, "beta")).items[0].model_dump(mode="json"),
                _result(_span(OTHER, 0, len(QUOTE), QUOTE)).items[0].model_dump(mode="json"),
            ],
            "resumen_clase": "A class about evidence.",
            "temas": ["evidence"],
        }
    )
    with pytest.raises(AnalysisValidationError) as caught:
        validate_analysis(result, _view({SEGMENT: TEXT}))
    assert caught.value.code == "segment_not_in_class"
    assert QUOTE not in str(caught.value)


def test_an_empty_result_needs_no_segment() -> None:
    result = AnalysisResult.model_validate(
        {"items": [], "resumen_clase": "Nothing to extract.", "temas": []}
    )
    validate_analysis(result, _view({}))


def _compiled(statement: Select[Any]) -> tuple[str, dict[str, Any]]:
    # `postgresql.dialect` is untyped in the installed stubs; the call itself is fine.
    dialect = cast(Any, postgresql).dialect()
    compiled = statement.compile(dialect=dialect)
    return str(compiled), dict(compiled.params)


def test_the_segment_query_is_pinned_to_the_class_version() -> None:
    sql, params = _compiled(class_segments_statement(USER, AUDIO, 4))
    assert "segments" in sql
    assert "transcripts" in sql
    assert "audios" in sql
    assert "deleted_at IS NULL" in sql
    assert "version" in sql
    assert "ordinal" in sql
    assert USER in params.values()
    assert AUDIO in params.values()
    assert 4 in params.values()


def test_the_transcript_query_uses_the_same_pins() -> None:
    sql, params = _compiled(class_transcript_statement(USER, AUDIO, 4))
    assert "transcripts" in sql
    assert "audios" in sql
    assert "deleted_at IS NULL" in sql
    assert USER in params.values()
    assert AUDIO in params.values()
    assert 4 in params.values()


class _Scalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _Result:
    def __init__(self, *, scalar: UUID | None = None, rows: list[Any] | None = None) -> None:
        self._scalar = scalar
        self._rows = rows or []

    def scalar_one_or_none(self) -> UUID | None:
        return self._scalar

    def scalars(self) -> _Scalars:
        return _Scalars(self._rows)


class _Db:
    def __init__(self, results: list[_Result]) -> None:
        self._results = results

    async def execute(self, _statement: object) -> _Result:
        return self._results.pop(0)


async def test_a_missing_version_is_not_an_empty_class() -> None:
    with pytest.raises(AnalysisValidationError) as caught:
        await load_class_transcript(
            cast(AsyncSession, _Db([_Result(scalar=None)])),
            user_id=USER,
            audio_id=AUDIO,
            transcript_version=3,
        )
    assert caught.value.code == "transcript_not_in_class"


async def test_the_loader_returns_only_the_loaded_segments() -> None:
    row = type("Row", (), {"id": SEGMENT, "text": TEXT, "transcript_id": TRANSCRIPT})()
    view = await load_class_transcript(
        cast(AsyncSession, _Db([_Result(scalar=TRANSCRIPT), _Result(rows=[row])])),
        user_id=USER,
        audio_id=AUDIO,
        transcript_version=2,
    )
    assert view.user_id is USER
    assert view.audio_id is AUDIO
    assert view.transcript_id is TRANSCRIPT
    assert view.transcript_version == 2
    assert view.segments == {SEGMENT: TEXT}

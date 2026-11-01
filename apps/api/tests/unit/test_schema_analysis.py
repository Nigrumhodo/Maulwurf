"""U-S3-JL-01 (L3.2): the frozen §M4 analysis contract.

Covers the enums, ranges, `due_date`/`due_at` exclusivity, `date_status` coherence,
`confidence_score`, Unicode code-point spans `[inicio_char, fin_char)` and the mandatory
verbatim quote carried by every evidence span. Segment membership in the class/tenant/
transcript version and the DB-backed literal check are L3.3; `check_against` is the pure
seam it will use.
"""
import json
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.schemas.analysis import AnalysisResult, DateStatus, ProposedItem, SourceSpan
from app.schemas.errors import validation_errors_redacted

SEGMENT = "3f1c9a52-7d4e-4b8a-9c1e-2a6b5d8e0f11"
OTHER_SEGMENT = "9b2e4c71-1a3f-4d6e-8b5c-7e0a2f9d4c33"
# The Spanish example of ESPECIFICACION §M4 has exactly 48 Unicode code points.
SPEC_QUOTE = "el examen parcial será el lunes 21 de septiembre"
UNICODE_TEXT = "día 🎓 微積分 café"


def _evidence(segment: str, start: int, end: int, quote: str) -> dict[str, Any]:
    return {
        "segment_id": segment,
        "inicio_char": start,
        "fin_char": end,
        "cita_textual": quote,
    }


def _quote(segment: str, quote: str, start: int = 0) -> dict[str, Any]:
    return _evidence(segment, start, start + len(quote), quote)


def _source(*spans: dict[str, Any]) -> dict[str, Any]:
    return {"spans": list(spans)}


SPEC_EXAMPLE: dict[str, Any] = {
    "items": [
        {
            "tipo": "examen",
            "titulo": "Examen parcial de Cálculo II — temas 1 a 4",
            "detalle": "Incluye sustitución trigonométrica",
            "due_date": "2026-09-21",
            "due_at": None,
            "timezone": "America/Mexico_City",
            "all_day": True,
            "date_status": "resolved",
            "confidence_score": 0.92,
            "fuente": _source(_evidence(SEGMENT, 0, 48, SPEC_QUOTE)),
        }
    ],
    "resumen_clase": "…3 a 5 frases…",
    "temas": ["integrales por partes", "sustitución trigonométrica"],
}


def _item(**overrides: Any) -> dict[str, Any]:
    item = dict(SPEC_EXAMPLE["items"][0])
    item.update(overrides)
    return item


def test_spec_example_is_valid() -> None:
    result = AnalysisResult.model_validate(SPEC_EXAMPLE)

    item = result.items[0]
    assert item.date_status is DateStatus.RESOLVED
    assert item.fuente.spans[0].segment_id == UUID(SEGMENT)
    assert item.fuente.spans[0].cita_textual == SPEC_QUOTE
    assert result.temas == SPEC_EXAMPLE["temas"]


def test_quote_is_mandatory_per_evidence() -> None:
    with pytest.raises(ValidationError):
        SourceSpan.model_validate(
            {"segment_id": SEGMENT, "inicio_char": 0, "fin_char": 48}
        )


def test_quote_length_must_match_the_span() -> None:
    with pytest.raises(ValidationError, match="medir"):
        ProposedItem.model_validate(
            _item(fuente=_source(_evidence(SEGMENT, 0, 48, "cita más corta")))
        )


def test_multiple_evidences_carry_their_own_quote() -> None:
    first = _quote(SEGMENT, "primera cita")
    second = _quote(SEGMENT, "segunda cita", start=20)
    third = _quote(OTHER_SEGMENT, "otra clase")

    item = ProposedItem.model_validate(_item(fuente=_source(first, second, third)))

    assert [span.cita_textual for span in item.fuente.spans] == [
        "primera cita",
        "segunda cita",
        "otra clase",
    ]


def test_the_item_no_longer_carries_an_aggregate_quote() -> None:
    # L3.2 moves the quote to each evidence span; the DB `task_evidence.quote` is per row.
    with pytest.raises(ValidationError):
        ProposedItem.model_validate(_item(cita_textual=SPEC_QUOTE))


def test_quote_keeps_leading_and_trailing_whitespace_verbatim() -> None:
    text = "  hola  "
    item = ProposedItem.model_validate(_item(fuente=_source(_quote(SEGMENT, text))))

    assert item.fuente.spans[0].cita_textual == text


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param(
            {"due_date": None, "due_at": "2026-09-21T10:00:00-06:00", "all_day": False},
            id="resolved-due_at",
        ),
        pytest.param(
            {"due_date": None, "all_day": False, "date_status": "missing"}, id="missing-sin-fechas"
        ),
        pytest.param(
            {"due_date": None, "all_day": False, "date_status": "ambiguous"},
            id="ambiguous-sin-fechas",
        ),
        pytest.param({"confidence_score": None}, id="confidence-null"),
        pytest.param({"confidence_score": 0}, id="confidence-0"),
        pytest.param({"confidence_score": 1}, id="confidence-1"),
        pytest.param({"detalle": None}, id="sin-detalle"),
        pytest.param(
            {
                "fuente": _source(
                    _quote(SEGMENT, "primera cita"),
                    _quote(SEGMENT, "segunda cita", start=12),
                    _quote(OTHER_SEGMENT, "otra clase"),
                )
            },
            id="spans-contiguos-y-otro-segmento",
        ),
    ],
)
def test_valid_items(overrides: dict[str, Any]) -> None:
    ProposedItem.model_validate(_item(**overrides))


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"due_at": "2026-09-21T10:00:00-06:00"}, id="ambas-fechas"),
        pytest.param({"due_date": None}, id="resolved-sin-fechas"),
        pytest.param({"date_status": "ambiguous"}, id="ambiguous-con-due_date"),
        pytest.param({"date_status": "missing"}, id="missing-con-due_date"),
        pytest.param(
            {"due_date": None, "due_at": "2026-09-21T10:00:00-06:00", "all_day": True},
            id="all_day-con-due_at",
        ),
        pytest.param({"all_day": False}, id="due_date-sin-all_day"),
        pytest.param(
            {"due_date": None, "due_at": "2026-09-21T10:00:00", "all_day": False},
            id="due_at-naive",
        ),
        pytest.param({"confidence_score": -0.01}, id="confidence-negativo"),
        pytest.param({"confidence_score": 1.01}, id="confidence-mayor-1"),
        pytest.param({"confidence_score": float("nan")}, id="confidence-nan"),
        pytest.param({"confidence_score": float("inf")}, id="confidence-inf"),
        pytest.param({"tipo": "parcial"}, id="tipo-fuera-enum"),
        pytest.param({"date_status": "needs_review"}, id="date_status-fuera-enum"),
        pytest.param({"timezone": "Mars/Base"}, id="zona-inexistente"),
        pytest.param({"fuente": {"spans": []}}, id="spans-vacio"),
        pytest.param({"fuente": _source(_evidence(SEGMENT, 5, 5, "x"))}, id="span-vacio"),
        pytest.param({"fuente": _source(_evidence(SEGMENT, 9, 5, "x"))}, id="span-invertido"),
        pytest.param(
            {"fuente": _source(_evidence(SEGMENT, -1, 5, "x" * 6))},
            id="span-inicio-negativo",
        ),
        pytest.param(
            {
                "fuente": _source(
                    _quote(SEGMENT, "cita"), _quote(SEGMENT, "cita")
                )
            },
            id="span-duplicado",
        ),
        pytest.param(
            {
                "fuente": _source(
                    _quote(SEGMENT, "una cita larga"),
                    _quote(SEGMENT, "cita", start=4),
                )
            },
            id="span-solapado",
        ),
        pytest.param(
            {
                "fuente": {
                    "spans": [
                        {"segment_id": "x", "inicio_char": 0, "fin_char": 1, "cita_textual": "a"}
                    ]
                }
            },
            id="segment_id-no-uuid",
        ),
        pytest.param({"user_id": SEGMENT}, id="campo-extra"),
        pytest.param(
            {"fuente": _source(_evidence(SEGMENT, 0, 3, "   "))}, id="cita-solo-espacios"
        ),
        pytest.param({"fuente": _source(_evidence(SEGMENT, 0, 1, ""))}, id="cita-vacia"),
        pytest.param(
            {"fuente": _source(_evidence(SEGMENT, 0, 10, "corta"))},
            id="cita-no-mide-el-span",
        ),
        pytest.param({"titulo": ""}, id="titulo-vacio"),
        pytest.param({"titulo": "x" * 201}, id="titulo-largo"),
        pytest.param({"detalle": "x" * 2001}, id="detalle-largo"),
    ],
)
def test_invalid_items(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ProposedItem.model_validate(_item(**overrides))


@pytest.mark.parametrize(
    "temas",
    [
        pytest.param(["Integrales", "integrales"], id="duplicado-casefold"),
        pytest.param(["integrales", "  "], id="tema-vacio"),
    ],
)
def test_invalid_topics(temas: list[str]) -> None:
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate({**SPEC_EXAMPLE, "temas": temas})


def test_empty_summary_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate({**SPEC_EXAMPLE, "resumen_clase": "   "})


def test_extra_field_in_result_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate({**SPEC_EXAMPLE, "prompt_version": "v1"})


def test_offsets_are_unicode_code_points_not_utf16() -> None:
    # 🎓 occupies one code point and two UTF-16 units: the contract uses code points.
    assert len(UNICODE_TEXT) == 14
    assert len(UNICODE_TEXT.encode("utf-16-le")) // 2 == 15

    start = UNICODE_TEXT.index("🎓")
    item = ProposedItem.model_validate(
        _item(fuente=_source(_quote(SEGMENT, "🎓", start=start)))
    )

    item.check_against({UUID(SEGMENT): UNICODE_TEXT})


def test_check_against_accepts_the_exact_slice() -> None:
    item = ProposedItem.model_validate(
        _item(fuente=_source(_quote(SEGMENT, "día 🎓")))
    )

    item.check_against({UUID(SEGMENT): UNICODE_TEXT})


def test_check_against_rejects_an_unknown_segment() -> None:
    item = ProposedItem.model_validate(_item())

    with pytest.raises(ValueError, match="desconocido"):
        item.check_against({UUID(OTHER_SEGMENT): SPEC_QUOTE})


def test_check_against_rejects_a_span_past_the_segment() -> None:
    item = ProposedItem.model_validate(
        _item(fuente=_source(_quote(SEGMENT, "cita fuera del texto")))
    )

    with pytest.raises(ValueError, match="excede"):
        item.check_against({UUID(SEGMENT): "cita"})


def test_check_against_rejects_a_quote_that_does_not_match_the_slice() -> None:
    item = ProposedItem.model_validate(
        _item(fuente=_source(_quote(SEGMENT, "día ")))
    )

    with pytest.raises(ValueError, match="no coincide"):
        item.check_against({UUID(SEGMENT): "Día "})


def test_check_against_keeps_whitespace_verbatim() -> None:
    item = ProposedItem.model_validate(
        _item(fuente=_source(_quote(SEGMENT, "  hola  ")))
    )

    item.check_against({UUID(SEGMENT): "  hola  "})

    with pytest.raises(ValueError, match="no coincide"):
        item.check_against({UUID(SEGMENT): "  hola! "})


def test_check_against_is_literal_not_normalization_insensitive() -> None:
    # The quote must be the exact code-point slice, not an NFC/NFD equivalent.
    item = ProposedItem.model_validate(_item(fuente=_source(_quote(SEGMENT, "café"))))

    with pytest.raises(ValueError, match="no coincide"):
        item.check_against({UUID(SEGMENT): "cafe\u0301"})


def test_check_against_errors_do_not_echo_the_transcript() -> None:
    item = ProposedItem.model_validate(_item(fuente=_source(_quote(SEGMENT, "día "))))

    with pytest.raises(ValueError) as exc:
        item.check_against({UUID(SEGMENT): "Día "})

    assert "Día" not in str(exc.value)
    assert "día" not in str(exc.value)


def test_analysis_result_checks_every_item() -> None:
    result = AnalysisResult.model_validate(SPEC_EXAMPLE)

    result.check_against({UUID(SEGMENT): SPEC_QUOTE})

    with pytest.raises(ValueError, match="no coincide"):
        result.check_against({UUID(SEGMENT): "x" * len(SPEC_QUOTE)})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("timezone", "Zona/SECRETO-INPUT"),
        ("tipo", "SECRETO-INPUT"),
        ("date_status", "SECRETO-INPUT"),
    ],
)
def test_errors_do_not_echo_input(field: str, value: str) -> None:
    with pytest.raises(ValidationError) as exc:
        ProposedItem.model_validate(_item(**{field: value}))

    errors = validation_errors_redacted(exc.value)
    assert "SECRETO-INPUT" not in json.dumps(errors, default=str)


def test_quote_errors_do_not_echo_input() -> None:
    with pytest.raises(ValidationError) as exc:
        SourceSpan.model_validate(_evidence(SEGMENT, 0, 1, "SECRETO-INPUT"))

    errors = validation_errors_redacted(exc.value)
    assert "SECRETO-INPUT" not in json.dumps(errors, default=str)


def test_default_serialization_echoes_input() -> None:
    # Pydantic includes `input_value` by default (QA H1): serialize only with
    # `validation_errors_redacted`, never with `str(exc)`.
    with pytest.raises(ValidationError) as exc:
        ProposedItem.model_validate(_item(tipo="SECRETO-INPUT"))

    assert "SECRETO-INPUT" in str(exc.value)
    assert "SECRETO-INPUT" in json.dumps(exc.value.errors(), default=str)


def test_redacted_errors_keep_the_reason() -> None:
    with pytest.raises(ValidationError) as exc:
        ProposedItem.model_validate(_item(tipo="SECRETO-INPUT", timezone="Zona/SECRETO-INPUT"))

    errors = validation_errors_redacted(exc.value)

    assert {error["loc"][0] for error in errors} == {"tipo", "timezone"}

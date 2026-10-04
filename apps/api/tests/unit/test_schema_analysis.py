"""U-S1-JL-01: borrador §M4 — exclusión due_date/due_at, rangos, enums y spans."""
import json
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.schemas.analysis import AnalysisResult, DateStatus, ProposedItem

SEGMENT = "3f1c9a52-7d4e-4b8a-9c1e-2a6b5d8e0f11"
OTHER_SEGMENT = "9b2e4c71-1a3f-4d6e-8b5c-7e0a2f9d4c33"

# Ejemplo de ESPECIFICACION §M4 con `segment_id` UUID en lugar de «…».
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
            "fuente": {"spans": [{"segment_id": SEGMENT, "inicio_char": 0, "fin_char": 48}]},
            "cita_textual": "el examen parcial será el lunes 21 de septiembre",
        }
    ],
    "resumen_clase": "…3 a 5 frases…",
    "temas": ["integrales por partes", "sustitución trigonométrica"],
}


def _item(**overrides: Any) -> dict[str, Any]:
    item = dict(SPEC_EXAMPLE["items"][0])
    item.update(overrides)
    return item


def _spans(*spans: tuple[str, int, int]) -> dict[str, Any]:
    return {"spans": [{"segment_id": s, "inicio_char": i, "fin_char": f} for s, i, f in spans]}


def test_spec_example_is_valid() -> None:
    result = AnalysisResult.model_validate(SPEC_EXAMPLE)

    item = result.items[0]
    assert item.date_status is DateStatus.RESOLVED
    assert item.fuente.spans[0].segment_id == UUID(SEGMENT)
    assert result.temas == SPEC_EXAMPLE["temas"]


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
            {"fuente": _spans((SEGMENT, 0, 10), (SEGMENT, 10, 20), (OTHER_SEGMENT, 0, 10))},
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
        pytest.param({"fuente": _spans((SEGMENT, 5, 5))}, id="span-vacio"),
        pytest.param({"fuente": _spans((SEGMENT, 9, 5))}, id="span-invertido"),
        pytest.param({"fuente": _spans((SEGMENT, -1, 5))}, id="span-inicio-negativo"),
        pytest.param({"fuente": _spans((SEGMENT, 0, 5), (SEGMENT, 0, 5))}, id="span-duplicado"),
        pytest.param({"fuente": _spans((SEGMENT, 0, 10), (SEGMENT, 9, 20))}, id="span-solapado"),
        pytest.param({"fuente": {"spans": [{"segment_id": "x", "inicio_char": 0, "fin_char": 1}]}},
                     id="segment_id-no-uuid"),
        pytest.param({"user_id": SEGMENT}, id="campo-extra"),
        pytest.param({"cita_textual": ""}, id="cita-vacia"),
        pytest.param({"cita_textual": "   "}, id="cita-solo-espacios"),
        pytest.param({"titulo": ""}, id="titulo-vacio"),
        pytest.param({"titulo": "x" * 201}, id="titulo-largo"),
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


def test_extra_field_in_result_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate({**SPEC_EXAMPLE, "prompt_version": "v1"})


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

    errors = exc.value.errors(include_input=False, include_url=False)
    assert "SECRETO-INPUT" not in json.dumps(errors, default=str)

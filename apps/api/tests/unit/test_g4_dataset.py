"""L1.5 (U-S1-JL-03): formato y validador del dataset G4, sin contenido real."""
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from tests.fixtures.g4.schema import G4Item
from tests.fixtures.g4.validate import DatasetFormatError, load, main, validate_dataset

EXAMPLE = Path(__file__).resolve().parents[1] / "fixtures" / "g4" / "example.jsonl"
LANGUAGES = ("es", "en", "fr")


def _raw(
    language: str,
    n: int,
    *,
    answerable: bool = True,
    partition: str = "development",
    **overrides: Any,
) -> dict[str, Any]:
    evidence = [
        {
            "class_ref": "sint-calculo-01",
            "transcript_version": 1,
            "segment_ordinal": n,
            "inicio_char": 0,
            "fin_char": 10,
            "cita_textual": "cita sintética",
        }
    ]
    raw: dict[str, Any] = {
        "id": f"{language}-{n:04d}",
        "language": language,
        "partition": partition,
        "question": f"Pregunta sintética número {n}",
        "answerable": answerable,
        "expected_evidence": evidence if answerable else [],
    }
    return {**raw, **overrides}


def _language(language: str, total: int = 50, unanswerable: int = 15) -> list[G4Item]:
    return [
        G4Item.model_validate(
            _raw(
                language,
                n,
                answerable=n >= unanswerable,
                partition="test" if n % 2 else "development",
            )
        )
        for n in range(total)
    ]


def _dataset(**sizes: tuple[int, int]) -> list[G4Item]:
    items: list[G4Item] = []
    for language in LANGUAGES:
        total, unanswerable = sizes.get(language, (50, 15))
        items.extend(_language(language, total, unanswerable))
    return items


def _problems(items: list[G4Item], **kwargs: Any) -> list[str]:
    return validate_dataset(items, languages=LANGUAGES, **kwargs)


def test_example_loads_and_passes_reduced_minimums() -> None:
    items = load(EXAMPLE)

    assert {item.language for item in items} == set(LANGUAGES)
    assert _problems(items, min_per_language=3, min_unanswerable=1) == []


def test_example_fails_real_minimums() -> None:
    problems = _problems(load(EXAMPLE))

    assert any("mínimo 50" in p for p in problems)
    assert any("mínimo 15" in p for p in problems)


def test_full_dataset_passes_real_minimums() -> None:
    assert _problems(_dataset()) == []


@pytest.mark.parametrize(
    ("sizes", "expected"),
    [
        ({"fr": (49, 15)}, "fr: 49 ítems, mínimo 50"),
        ({"en": (50, 14)}, "en: 14 no respondibles, mínimo 15"),
    ],
)
def test_minimums_are_per_language(sizes: dict[str, tuple[int, int]], expected: str) -> None:
    assert _problems(_dataset(**sizes)) == [expected]


def test_surplus_language_does_not_compensate() -> None:
    # 100 ítems en es no tapan los 49 de fr: cada idioma aprueba por separado.
    problems = _problems(_dataset(es=(100, 30), fr=(49, 15)))

    assert problems == ["fr: 49 ítems, mínimo 50"]


def test_language_not_enabled() -> None:
    items = _dataset() + [G4Item.model_validate(_raw("de", 0))]

    assert _problems(items) == ["idioma no habilitado: de"]


def test_enabled_language_missing() -> None:
    items = [item for item in _dataset() if item.language != "fr"]

    assert _problems(items) == ["fr: idioma habilitado sin ítems"]


def test_duplicate_id() -> None:
    items = _dataset()
    items.append(items[20].model_copy(update={"question": "Otra pregunta distinta"}))

    assert _problems(items) == ["id duplicado: es-0020 (2 veces)"]


def test_duplicate_question_ignores_case_and_spacing() -> None:
    items = _dataset()
    items.append(
        G4Item.model_validate(_raw("es", 999, question="  PREGUNTA   sintética\tnúmero 20 "))
    )

    assert _problems(items) == ["es: pregunta duplicada en es-0020 y es-0999"]


def test_missing_test_partition() -> None:
    items = [
        item.model_copy(update={"partition": "development"}) if item.language == "en" else item
        for item in _dataset()
    ]

    assert _problems(items) == ["en: sin ítems en la partición test"]


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"answerable": True, "expected_evidence": []}, "al menos una evidencia"),
        (
            {"answerable": False, "expected_evidence": _raw("es", 1)["expected_evidence"]},
            "no lleva evidencia",
        ),
        (
            {"answerable": False, "expected_evidence": [], "reference_answer": "x"},
            "no lleva reference_answer",
        ),
        ({"id": "en-0001"}, "debe empezar por"),
        ({"partition": "train"}, "partition"),
        ({"question": "abc"}, "question"),
        ({"language": "ES"}, "language"),
        ({"extra": 1}, "extra"),
    ],
)
def test_invalid_item(overrides: dict[str, Any], expected: str) -> None:
    with pytest.raises(ValidationError, match=expected):
        G4Item.model_validate({**_raw("es", 1), **overrides})


@pytest.mark.parametrize(("inicio", "fin"), [(10, 10), (10, 5)])
def test_empty_or_inverted_span(inicio: int, fin: int) -> None:
    raw = _raw("es", 1)
    raw["expected_evidence"][0].update(inicio_char=inicio, fin_char=fin)

    with pytest.raises(ValidationError, match="fin_char debe ser mayor"):
        G4Item.model_validate(raw)


def test_extra_field_in_evidence() -> None:
    raw = _raw("es", 1)
    raw["expected_evidence"][0]["segment_id"] = "x"

    with pytest.raises(ValidationError, match="segment_id"):
        G4Item.model_validate(raw)


def _write_jsonl(path: Path, lines: list[str]) -> Path:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_broken_line_reports_line_number(tmp_path: Path) -> None:
    good = json.dumps(_raw("es", 1))
    path = _write_jsonl(tmp_path / "g4.jsonl", [good, "{roto", "", json.dumps(_raw("es", 2))])

    with pytest.raises(DatasetFormatError) as exc:
        load(path)

    assert len(exc.value.problems) == 1
    assert exc.value.problems[0].startswith("línea 2: ")


def test_load_errors_do_not_echo_question(tmp_path: Path) -> None:
    marker = "contenido-que-no-debe-salir"
    bad = json.dumps(_raw("es", 1, question=f"¿{marker}?", reference_answer=None, extra=marker))
    path = _write_jsonl(tmp_path / "g4.jsonl", [bad])

    with pytest.raises(DatasetFormatError) as exc:
        load(path)

    assert exc.value.problems[0].startswith("línea 1: ")
    assert marker not in str(exc.value)


def test_cli_exit_codes(capsys: pytest.CaptureFixture[str]) -> None:
    reduced = ["--min-per-language", "3", "--min-unanswerable", "1"]

    assert main([str(EXAMPLE), *reduced]) == 0
    assert main([str(EXAMPLE)]) == 1
    assert main([str(EXAMPLE), "--languages", "es,en", *reduced]) == 1
    assert "idioma no habilitado: fr" in capsys.readouterr().err

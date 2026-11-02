"""L1.5 (U-S1-JL-03): formato y validador del dataset G4, sin contenido real.

L2.7 (G4): integridad del dataset congelado ``g4-v1`` y del smoke de CI.
"""

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from tests.fixtures.g4.schema import G4Item
from tests.fixtures.g4.validate import DatasetFormatError, load, main, validate_dataset
from tools.eval.dataset import (
    Dataset,
    DatasetError,
    content_sha256,
    file_sha256,
    fts_config,
    load_dataset,
    write_manifest,
)

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


# --- L2.7 frozen g4-v1 and smoke dataset checks ---

DATASETS = Path(__file__).resolve().parents[1] / "fixtures" / "g4" / "datasets"
V1 = DATASETS / "v1"
SMOKE = DATASETS / "smoke-v1"


@pytest.fixture(scope="module")
def v1() -> Dataset:
    return load_dataset(V1)


@pytest.fixture(scope="module")
def smoke() -> Dataset:
    return load_dataset(SMOKE)


def _g4_copy_dataset(source: Path, destination: Path) -> Path:
    root = destination / source.name
    shutil.copytree(source, root)
    return root


def _g4_read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _g4_write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def test_g4_v1_has_50_test_items_and_15_unanswerable_per_language(v1: Dataset) -> None:
    """Every enabled language carries exactly 50 test items, 35 answerable and 15 not."""
    for language in LANGUAGES:
        test_items = v1.test_items(language)
        assert len(test_items) == 50
        assert sum(1 for item in test_items if item.answerable) == 35
        assert sum(1 for item in test_items if not item.answerable) == 15
    assert v1.is_official_size()
    assert fts_config("fr") == "simple"
    assert fts_config("es") == "spanish"
    assert fts_config("en") == "english"


def test_g4_v1_has_five_development_items_per_language(v1: Dataset) -> None:
    """The development partition is small, separate and never scored."""
    for language in LANGUAGES:
        development = v1.development_items(language)
        assert len(development) == 5
        assert sum(1 for item in development if item.answerable) == 4
        assert sum(1 for item in development if not item.answerable) == 1
        assert all(item.partition == "development" for item in development)


def test_g4_v1_every_evidence_span_matches_its_segment_text(v1: Dataset) -> None:
    """Each ``cita_textual`` is the exact Unicode slice of its corpus segment."""
    checked = 0
    for language in LANGUAGES:
        for item in v1.items[language]:
            for evidence in item.expected_evidence:
                corpus_class = v1.classes[evidence.class_ref]
                segment = corpus_class.segment(evidence.segment_ordinal)
                assert segment is not None
                assert 0 <= evidence.inicio_char < evidence.fin_char <= len(segment.text)
                assert (
                    segment.text[evidence.inicio_char : evidence.fin_char] == evidence.cita_textual
                )
                checked += 1
    assert checked >= 100


def test_g4_v1_every_evidence_class_exists_and_version_matches(v1: Dataset) -> None:
    """Evidence points to a seeded class of the same language and transcript version."""
    for language in LANGUAGES:
        for item in v1.items[language]:
            assert item.id.startswith(f"{language}-")
            for evidence in item.expected_evidence:
                corpus_class = v1.classes[evidence.class_ref]
                assert corpus_class.language == language
                assert evidence.transcript_version == corpus_class.transcript_version


def test_g4_v1_manifest_hashes_match_files(v1: Dataset) -> None:
    """Every declared file hash and the aggregate ``content_sha256`` match the bytes."""
    manifest = v1.manifest
    assert set(manifest.files) == {
        f"{kind}/{language}.jsonl" for kind in ("corpus", "items") for language in LANGUAGES
    }
    for relative, digest in manifest.files.items():
        assert digest == f"sha256:{file_sha256(V1 / relative)}"
    assert manifest.content_sha256 == content_sha256(manifest.files)
    assert v1.content_sha256 == manifest.content_sha256


def test_g4_v1_rejects_a_changed_test_partition_hash(tmp_path: Path) -> None:
    """Editing the frozen items without a new version is detected before scoring."""
    root = _g4_copy_dataset(V1, tmp_path)
    items_path = root / "items" / "es.jsonl"
    records = _g4_read_jsonl(items_path)
    records[0]["question"] = "¿Pregunta alterada fuera de la versión congelada?"
    _g4_write_jsonl(items_path, records)

    with pytest.raises(DatasetError, match="hash distinto para items/es.jsonl"):
        load_dataset(root)


def test_g4_v1_ids_are_language_prefixed_and_unique(v1: Dataset) -> None:
    """IDs are stable, language-prefixed and unique across the whole dataset."""
    seen: set[str] = set()
    for language in LANGUAGES:
        for item in v1.items[language]:
            assert item.id.startswith(f"{language}-")
            assert item.id not in seen
            seen.add(item.id)
    assert len(seen) == len(LANGUAGES) * 55


def test_g4_v1_questions_are_unique_per_language(v1: Dataset) -> None:
    """No normalized question is repeated inside one language."""
    for language in LANGUAGES:
        questions = [" ".join(item.question.split()).casefold() for item in v1.items[language]]
        assert len(questions) == len(set(questions))


def test_g4_loader_rejects_an_unanswerable_item_with_evidence(tmp_path: Path) -> None:
    """An unanswerable item cannot smuggle expected evidence past the frozen schema."""
    root = _g4_copy_dataset(V1, tmp_path)
    items_path = root / "items" / "es.jsonl"
    records = _g4_read_jsonl(items_path)
    answerable = next(record for record in records if record["answerable"])
    unanswerable = next(record for record in records if not record["answerable"])
    unanswerable["expected_evidence"] = answerable["expected_evidence"]
    _g4_write_jsonl(items_path, records)

    manifest = load_dataset(V1).manifest
    write_manifest(
        root,
        dataset_version=manifest.dataset_version,
        created_at=manifest.created_at,
        provenance=manifest.provenance,
        languages=manifest.languages,
        test=manifest.test,
        development=manifest.development,
    )

    with pytest.raises(DatasetError, match="no lleva evidencia"):
        load_dataset(root)


def test_g4_smoke_dataset_is_small_and_valid(smoke: Dataset) -> None:
    """The CI smoke dataset is valid, small and explicitly not official-size."""
    assert not smoke.is_official_size()
    for language in LANGUAGES:
        assert len(smoke.test_items(language)) == 4
        assert sum(1 for item in smoke.test_items(language) if not item.answerable) == 1
        assert len(smoke.development_items(language)) == 1
    with pytest.raises(DatasetError, match="mínimo G4"):
        load_dataset(SMOKE, require_official=True)

"""Versioned G4 dataset loading, hashing and integrity checks (L2.7).

The dataset is the frozen input of the evaluation: the ``test`` partition must not change
after a report cites it, and every evidence span must be verifiable against the synthetic
class transcript it points to. This module performs those checks before any database or
provider call.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from tests.fixtures.g4.schema import LANGUAGE_PATTERN, SLUG_PATTERN, G4Item
from tests.fixtures.g4.validate import DatasetFormatError
from tests.fixtures.g4.validate import load as load_items
from tests.fixtures.g4.validate import validate_dataset as validate_frozen_dataset

_FTS_BY_LANGUAGE = {"en": "english", "es": "spanish", "fr": "simple"}
_HASH_PREFIX = "sha256:"


class DatasetError(ValueError):
    """The dataset cannot be used; ``problems`` has one message per finding."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = tuple(problems)
        super().__init__("\n".join(self.problems))


class CorpusSegment(BaseModel):
    """One ordered segment of a synthetic class transcript."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    ordinal: int = Field(ge=1)
    text: str = Field(min_length=1)


class CorpusClass(BaseModel):
    """One synthetic class: metadata plus its ordered transcript segments."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    class_ref: str = Field(pattern=SLUG_PATTERN)
    language: str = Field(pattern=LANGUAGE_PATTERN)
    subject: str = Field(min_length=1)
    class_date: date
    transcript_version: int = Field(default=1, ge=1)
    segments: list[CorpusSegment] = Field(min_length=1)

    @model_validator(mode="after")
    def _ordinals_are_contiguous(self) -> Self:
        ordinals = [segment.ordinal for segment in self.segments]
        if ordinals != list(range(1, len(ordinals) + 1)):
            raise ValueError("los ordinales de segmento deben ser contiguos desde 1")
        return self

    @property
    def text(self) -> str:
        """Transcript text exactly as it will be persisted (segments joined by newlines)."""
        return "\n".join(segment.text for segment in self.segments)

    def segment(self, ordinal: int) -> CorpusSegment | None:
        """Return the segment with ``ordinal`` or ``None``."""
        return next((segment for segment in self.segments if segment.ordinal == ordinal), None)


class PartitionSpec(BaseModel):
    """Declared item counts for one partition."""

    model_config = ConfigDict(extra="forbid")

    per_language: int = Field(ge=1)
    answerable: int = Field(ge=0)
    unanswerable: int = Field(ge=1)

    @model_validator(mode="after")
    def _counts_add_up(self) -> Self:
        if self.answerable + self.unanswerable != self.per_language:
            raise ValueError("answerable + unanswerable debe igualar per_language")
        return self


class DatasetManifest(BaseModel):
    """Pinned metadata and file hashes of one dataset version."""

    model_config = ConfigDict(extra="forbid")

    dataset_version: str = Field(pattern=r"^g4-[a-z0-9-]{1,32}$")
    created_at: date
    provenance: str = Field(min_length=1)
    languages: list[str] = Field(min_length=1)
    test: PartitionSpec
    development: PartitionSpec
    files: dict[str, str]
    content_sha256: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def _languages_are_unique(self) -> Self:
        if len(set(self.languages)) != len(self.languages):
            raise ValueError("los idiomas no pueden repetirse")
        return self


@dataclass(frozen=True)
class Dataset:
    """A fully validated dataset, ready for seeding and scoring."""

    path: Path
    manifest: DatasetManifest
    corpus: Mapping[str, tuple[CorpusClass, ...]]
    items: Mapping[str, tuple[G4Item, ...]]
    classes: Mapping[str, CorpusClass]
    content_sha256: str

    @property
    def version(self) -> str:
        """Dataset version, also used in the generation version."""
        return self.manifest.dataset_version

    def test_items(self, language: str) -> tuple[G4Item, ...]:
        """Items in the frozen test partition for one language."""
        return tuple(item for item in self.items[language] if item.partition == "test")

    def development_items(self, language: str) -> tuple[G4Item, ...]:
        """Items reserved for smoke runs and prompt/harness development."""
        return tuple(item for item in self.items[language] if item.partition == "development")

    def partition_items(self, language: str, partition: str) -> tuple[G4Item, ...]:
        """Return the frozen items of one partition, in dataset order."""
        if partition == "test":
            return self.test_items(language)
        if partition == "development":
            return self.development_items(language)
        raise ValueError(f"partición desconocida: {partition}")

    def is_official_size(self) -> bool:
        """True when the declared test partition meets the G4 minimum counts."""
        return self.manifest.test.per_language >= 50 and self.manifest.test.unanswerable >= 15


def fts_config(language: str) -> str:
    """PostgreSQL text-search configuration used by the seeded transcript.

    ``search._FTS_CONFIGS`` only queries ``spanish``/``english``/``simple``. French therefore
    uses ``simple`` (no stemming); this limitation is reported with every G4 result.
    """
    try:
        return _FTS_BY_LANGUAGE[language]
    except KeyError as exc:
        raise DatasetError([f"idioma sin configuración FTS: {language}"]) from exc


def file_sha256(path: Path) -> str:
    """SHA-256 of a file, read in bounded chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def content_sha256(files: Mapping[str, str]) -> str:
    """Deterministic hash over the manifest's relative path -> digest map."""
    lines = "".join(f"{name}:{digest}\n" for name, digest in sorted(files.items()))
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


def write_manifest(
    path: Path,
    *,
    dataset_version: str,
    created_at: date,
    provenance: str,
    languages: Sequence[str],
    test: PartitionSpec,
    development: PartitionSpec,
) -> DatasetManifest:
    """Compute file hashes, write ``manifest.json`` and return the pinned manifest."""
    files: dict[str, str] = {}
    for language in languages:
        for relative in (f"corpus/{language}.jsonl", f"items/{language}.jsonl"):
            files[relative] = f"{_HASH_PREFIX}{file_sha256(path / relative)}"
    manifest = DatasetManifest(
        dataset_version=dataset_version,
        created_at=created_at,
        provenance=provenance,
        languages=list(languages),
        test=test,
        development=development,
        files=files,
        content_sha256=content_sha256(files),
    )
    target = path / "manifest.json"
    target.write_text(
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def load_dataset(
    path: Path,
    *,
    languages: Sequence[str] | None = None,
    require_official: bool = False,
) -> Dataset:
    """Load and validate a dataset directory; raise :class:`DatasetError` on any problem."""
    root = path.resolve()
    manifest = _load_manifest(root / "manifest.json")
    enabled = tuple(languages) if languages is not None else tuple(manifest.languages)
    problems: list[str] = []

    if not enabled:
        problems.append("no hay idiomas habilitados")
    for language in enabled:
        if language not in manifest.languages:
            problems.append(f"idioma no declarado en el manifiesto: {language}")
    _check_manifest_files(root, manifest, problems)
    if require_official and not (
        manifest.test.per_language >= 50 and manifest.test.unanswerable >= 15
    ):
        problems.append(
            "la partición test no cumple el mínimo G4 "
            f"({manifest.test.per_language} ítems, {manifest.test.unanswerable} no respondibles)"
        )
    if problems:
        raise DatasetError(problems)

    corpus: dict[str, tuple[CorpusClass, ...]] = {}
    classes: dict[str, CorpusClass] = {}
    for language in manifest.languages:
        loaded = _load_corpus(root / "corpus" / f"{language}.jsonl")
        corpus[language] = loaded
        for item in loaded:
            if item.language != language:
                problems.append(
                    f"corpus/{language}.jsonl: {item.class_ref} declara idioma {item.language}"
                )
            if item.class_ref in classes:
                problems.append(f"class_ref duplicado: {item.class_ref}")
            classes[item.class_ref] = item

    items: dict[str, tuple[G4Item, ...]] = {}
    for language in enabled:
        items[language] = _load_items(root / "items" / f"{language}.jsonl")
    if problems:
        raise DatasetError(problems)

    all_items = [item for language in enabled for item in items[language]]
    problems.extend(
        validate_frozen_dataset(
            all_items,
            languages=enabled,
            min_per_language=manifest.test.per_language + manifest.development.per_language,
            min_unanswerable=manifest.test.unanswerable + manifest.development.unanswerable,
        )
    )
    problems.extend(_check_partitions(items, manifest))
    problems.extend(_check_evidence(all_items, classes))
    seen_ids: dict[str, str] = {}
    for g4_item in all_items:
        if g4_item.id in seen_ids:
            problems.append(f"id duplicado entre idiomas: {g4_item.id}")
        seen_ids[g4_item.id] = g4_item.language
    if problems:
        raise DatasetError(problems)

    return Dataset(
        path=root,
        manifest=manifest,
        corpus=corpus,
        items=items,
        classes=classes,
        content_sha256=manifest.content_sha256,
    )


def _load_manifest(path: Path) -> DatasetManifest:
    if not path.is_file():
        raise DatasetError([f"falta el manifiesto: {path}"])
    try:
        return DatasetManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValidationError, OSError) as exc:
        raise DatasetError([f"manifiesto inválido: {_describe(exc)}"]) from exc


def _load_corpus(path: Path) -> tuple[CorpusClass, ...]:
    if not path.is_file():
        raise DatasetError([f"falta el corpus: {path}"])
    records: list[CorpusClass] = []
    problems: list[str] = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                records.append(CorpusClass.model_validate_json(line))
            except ValidationError as exc:
                problems.extend(f"{path.name}:{number}: {p}" for p in _describe(exc))
    if problems:
        raise DatasetError(problems)
    if not records:
        raise DatasetError([f"{path.name}: corpus vacío"])
    return tuple(records)


def _load_items(path: Path) -> tuple[G4Item, ...]:
    if not path.is_file():
        raise DatasetError([f"falta el fichero de ítems: {path}"])
    try:
        return tuple(load_items(path))
    except DatasetFormatError as exc:
        raise DatasetError([f"{path.name}: {p}" for p in exc.problems]) from exc


def _check_manifest_files(root: Path, manifest: DatasetManifest, problems: list[str]) -> None:
    expected = {f"corpus/{language}.jsonl" for language in manifest.languages} | {
        f"items/{language}.jsonl" for language in manifest.languages
    }
    declared = set(manifest.files)
    for missing in sorted(expected - declared):
        problems.append(f"el manifiesto no declara {missing}")
    for extra in sorted(declared - expected):
        problems.append(f"el manifiesto declara un fichero inesperado: {extra}")
    for relative, digest in sorted(manifest.files.items()):
        target = root / relative
        if not target.is_file():
            problems.append(f"falta el fichero declarado: {relative}")
            continue
        actual = f"{_HASH_PREFIX}{file_sha256(target)}"
        if actual != digest:
            problems.append(f"hash distinto para {relative}")
    if content_sha256(manifest.files) != manifest.content_sha256:
        problems.append("content_sha256 no coincide con los hashes declarados")
    for language in manifest.languages:
        if language not in _FTS_BY_LANGUAGE:
            problems.append(f"idioma sin configuración FTS: {language}")


def _check_partitions(
    items: Mapping[str, tuple[G4Item, ...]], manifest: DatasetManifest
) -> list[str]:
    problems: list[str] = []
    for language, group in items.items():
        test = [item for item in group if item.partition == "test"]
        development = [item for item in group if item.partition == "development"]
        unanswerable_test = sum(1 for item in test if not item.answerable)
        unanswerable_dev = sum(1 for item in development if not item.answerable)
        if len(test) != manifest.test.per_language:
            problems.append(
                f"{language}: {len(test)} ítems test, esperados {manifest.test.per_language}"
            )
        if unanswerable_test != manifest.test.unanswerable:
            problems.append(
                f"{language}: {unanswerable_test} no respondibles test, "
                f"esperados {manifest.test.unanswerable}"
            )
        if len(development) != manifest.development.per_language:
            problems.append(
                f"{language}: {len(development)} ítems development, "
                f"esperados {manifest.development.per_language}"
            )
        if unanswerable_dev != manifest.development.unanswerable:
            problems.append(
                f"{language}: {unanswerable_dev} no respondibles development, "
                f"esperados {manifest.development.unanswerable}"
            )
    return problems


def _check_evidence(items: Sequence[G4Item], classes: Mapping[str, CorpusClass]) -> list[str]:
    problems: list[str] = []
    for item in items:
        if item.answerable and not item.reference_answer:
            problems.append(f"{item.id}: ítem respondible sin reference_answer")
        for evidence in item.expected_evidence:
            corpus_class = classes.get(evidence.class_ref)
            if corpus_class is None:
                problems.append(f"{item.id}: class_ref inexistente {evidence.class_ref}")
                continue
            if corpus_class.language != item.language:
                problems.append(f"{item.id}: evidencia de otro idioma ({evidence.class_ref})")
            if evidence.transcript_version != corpus_class.transcript_version:
                problems.append(
                    f"{item.id}: transcript_version {evidence.transcript_version} "
                    f"no coincide con {corpus_class.class_ref}"
                )
            segment = corpus_class.segment(evidence.segment_ordinal)
            if segment is None:
                problems.append(
                    f"{item.id}: segmento inexistente {evidence.segment_ordinal} "
                    f"en {corpus_class.class_ref}"
                )
                continue
            if not (0 <= evidence.inicio_char < evidence.fin_char <= len(segment.text)):
                problems.append(f"{item.id}: offsets fuera de rango en {corpus_class.class_ref}")
                continue
            actual = segment.text[evidence.inicio_char : evidence.fin_char]
            if actual != evidence.cita_textual:
                problems.append(
                    f"{item.id}: cita_textual no coincide con el segmento "
                    f"{corpus_class.class_ref}#{segment.ordinal}"
                )
    return problems


def _describe(exc: ValidationError | OSError) -> str:
    if isinstance(exc, OSError):
        return str(exc)
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc']) or '(ítem)'}: {error['msg']}"
        for error in exc.errors(include_input=False, include_url=False)
    )

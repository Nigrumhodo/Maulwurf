"""G4 orchestration: prepare, run, resume, score and process exit codes (L2.7).

The harness is an offline tool. It never exposes HTTP routes and never falls back to a fake
provider: ``cli.py`` injects the configured real providers, while integration tests call
these functions with deterministic fakes. Artifacts live under ``var/eval/g4/<run-id>/``;
the only published output is the redacted directory written by :mod:`tools.eval.report`.
"""

from __future__ import annotations

import json
import platform
import subprocess
import unicodedata
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.embeddings import EmbeddingProvider
from app.services.llm import ChatMessage, LLMProvider
from app.services.rag import ABSTENTION_TEXT, PROMPT_VERSION
from tools.eval.chat import AnswerOutcome, answer_from_dict, answer_to_dict, run_answers
from tools.eval.corpus import (
    CorpusError,
    prepare_dataset,
    prepared_index_from_dict,
    prepared_index_to_dict,
)
from tools.eval.dataset import Dataset, DatasetError, load_dataset
from tools.eval.metrics import (
    LanguageMetrics,
    OverallVerdict,
    compute_language_metrics,
    metrics_to_dict,
    overall_verdict,
)
from tools.eval.report import (
    AdjudicationError,
    build_review_rows,
    load_adjudication,
    reviews_from_adjudication,
    validate_adjudication,
    write_publication,
    write_review_sheet,
)
from tools.eval.retrieval import RetrievalOutcome, run_retrieval

EXIT_PASS = 0
EXIT_GATE_FAIL = 1
EXIT_CONFIG = 2
EXIT_ADJUDICATION = 3
EXIT_NOT_EVALUATED = 4

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_VAR_DIR = REPO_ROOT / "var" / "eval" / "g4"

Status = Literal["prepared", "complete", "incomplete", "scored"]


class EvalExit(Exception):
    """A controlled CLI failure; ``exit_code`` is the frozen process contract."""

    def __init__(self, exit_code: int, message: str) -> None:
        self.exit_code = exit_code
        self.message = message
        super().__init__(message)


class BudgetExceeded(EvalExit):
    def __init__(self, calls: int, limit: int) -> None:
        super().__init__(
            EXIT_CONFIG,
            f"presupuesto de llamadas al proveedor agotado ({calls} > {limit}); "
            "usa --max-provider-calls para autorizar un run mayor",
        )


@dataclass
class _Budget:
    limit: int
    calls: int = 0

    def consume(self) -> None:
        self.calls += 1
        if self.calls > self.limit:
            raise BudgetExceeded(self.calls, self.limit)


class _CountingEmbedding:
    """Count one provider call per ``embed`` batch, preserving the protocol."""

    def __init__(self, provider: EmbeddingProvider, budget: _Budget) -> None:
        self._provider = provider
        self._budget = budget

    @property
    def model(self) -> str:
        return self._provider.model

    @property
    def dimension(self) -> int:
        return self._provider.dimension

    @property
    def batch_size(self) -> int:
        return self._provider.batch_size

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self._budget.consume()
        return await self._provider.embed(texts)


class _CountingLLM:
    """Count one provider call per streamed answer, preserving the protocol."""

    def __init__(self, provider: LLMProvider, budget: _Budget) -> None:
        self._provider = provider
        self._budget = budget

    @property
    def model(self) -> str:
        return self._provider.model

    @property
    def context_window(self) -> int:
        return self._provider.context_window

    async def stream(
        self, messages: Sequence[ChatMessage], *, max_tokens: int
    ) -> AsyncIterator[str]:
        self._budget.consume()
        async for chunk in self._provider.stream(messages, max_tokens=max_tokens):
            yield chunk


class BudgetRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_provider_calls: int = Field(ge=1)
    provider_calls: int = Field(default=0, ge=0)
    prepare_calls: int = Field(default=0, ge=0)
    run_calls: int = Field(default=0, ge=0)


class RunManifest(BaseModel):
    """Durable metadata for one evaluation run; raw answers never enter this file."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    status: Status
    created_at: str
    updated_at: str
    dataset_version: str
    dataset_hash: str
    dataset_path: str
    partition: Literal["test", "development"]
    languages: list[str]
    git_commit: str | None = None
    git_dirty: bool | None = None
    environment: dict[str, str]
    embedding: dict[str, Any]
    llm: dict[str, Any]
    prompt_version: str
    budget: BudgetRecord
    prepared: dict[str, Any]
    items: dict[str, int]
    completed_items: list[str] = Field(default_factory=list)
    retry_generation: int = Field(default=0, ge=0)
    notes: list[str] = Field(default_factory=list)


class _RetrievalRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str
    language: str
    answerable: bool
    expected: int = Field(ge=0)
    matched: int = Field(ge=0)
    took_ms: float = Field(ge=0)
    top_chunk_ids: list[str]


@dataclass(frozen=True)
class ScoreOutcome:
    exit_code: int
    verdict: OverallVerdict
    metrics: tuple[LanguageMetrics, ...]
    publish_dir: Path


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _git_metadata() -> tuple[str | None, bool | None]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607 - trusted local tool
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],  # noqa: S607 - trusted local tool
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None, None
    return (
        commit.stdout.strip() or None if commit.returncode == 0 else None,
        bool(dirty.stdout.strip()) if dirty.returncode == 0 else None,
    )


def _environment() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "implementation": platform.python_implementation(),
    }


def _validate_languages(dataset: Dataset, languages: Sequence[str]) -> tuple[str, ...]:
    enabled = tuple(languages)
    if not enabled:
        raise EvalExit(EXIT_CONFIG, "no hay idiomas habilitados")
    if len(set(enabled)) != len(enabled):
        raise EvalExit(EXIT_CONFIG, "los idiomas no pueden repetirse")
    unknown = sorted(set(enabled) - set(dataset.manifest.languages))
    if unknown:
        raise EvalExit(EXIT_CONFIG, f"idiomas no declarados en el dataset: {', '.join(unknown)}")
    for language in enabled:
        if not dataset.corpus.get(language):
            raise EvalExit(EXIT_CONFIG, f"sin corpus para el idioma {language}")
    return enabled


def _provider_metadata(
    embedding_provider: EmbeddingProvider, llm_provider: LLMProvider
) -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        {
            "provider": type(embedding_provider).__name__,
            "model": embedding_provider.model,
            "dimension": embedding_provider.dimension,
        },
        {
            "provider": type(llm_provider).__name__,
            "model": llm_provider.model,
            "context_window": llm_provider.context_window,
        },
    )


def _check_provider_compatibility(
    run: RunManifest,
    embedding_provider: EmbeddingProvider,
    llm_provider: LLMProvider,
) -> None:
    if run.embedding.get("model") != embedding_provider.model:
        raise EvalExit(
            EXIT_CONFIG,
            f"el modelo de embeddings {embedding_provider.model!r} no coincide con el run "
            f"({run.embedding.get('model')!r})",
        )
    if run.embedding.get("dimension") != embedding_provider.dimension:
        raise EvalExit(
            EXIT_CONFIG,
            f"la dimensión {embedding_provider.dimension} no coincide con el run "
            f"({run.embedding.get('dimension')})",
        )
    if run.llm.get("model") != llm_provider.model:
        raise EvalExit(
            EXIT_CONFIG,
            f"el modelo LLM {llm_provider.model!r} no coincide con el run "
            f"({run.llm.get('model')!r})",
        )


def write_run(run_dir: Path, run: RunManifest) -> None:
    """Write ``run.json`` with a stable, human-readable representation."""
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run.json").write_text(
        json.dumps(run.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_run(run_dir: Path) -> RunManifest:
    path = run_dir / "run.json"
    if not path.is_file():
        raise EvalExit(EXIT_CONFIG, f"no existe el manifiesto del run: {path}")
    try:
        return RunManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise EvalExit(EXIT_CONFIG, f"run.json inválido: {exc}") from exc


async def prepare_run(
    session_factory: async_sessionmaker[AsyncSession],
    dataset: Dataset,
    *,
    languages: Sequence[str],
    embedding_provider: EmbeddingProvider,
    llm_provider: LLMProvider,
    run_dir: Path,
    run_id: str,
    reset: bool = False,
    max_provider_calls: int = 400,
    require_nontrivial: bool = True,
) -> RunManifest:
    """Seed the isolated tenants and persist the prepared index metadata."""
    enabled = _validate_languages(dataset, languages)
    if max_provider_calls < 1:
        raise EvalExit(EXIT_CONFIG, "--max-provider-calls debe ser positivo")
    run_dir.mkdir(parents=True, exist_ok=True)

    existing_path = run_dir / "run.json"
    if existing_path.is_file() and not reset:
        existing = load_run(run_dir)
        if (
            existing.dataset_hash == dataset.content_sha256
            and tuple(existing.languages) == enabled
            and existing.prepared
        ):
            return existing
        if existing.dataset_hash != dataset.content_sha256:
            raise EvalExit(
                EXIT_CONFIG,
                "el run existente pertenece a otro hash del dataset; usa --reset o un run-id nuevo",
            )

    if reset:
        _clear_run_artifacts(run_dir)

    budget = _Budget(limit=max_provider_calls)
    counting = _CountingEmbedding(embedding_provider, budget)
    try:
        prepared = await prepare_dataset(
            session_factory,
            dataset,
            enabled,
            provider=counting,
            reset=reset,
            require_nontrivial=require_nontrivial,
        )
    except CorpusError as exc:
        raise EvalExit(EXIT_CONFIG, str(exc)) from exc

    embedding_meta, llm_meta = _provider_metadata(embedding_provider, llm_provider)
    commit, dirty = _git_metadata()
    now = _now()
    run = RunManifest(
        run_id=run_id,
        status="prepared",
        created_at=now,
        updated_at=now,
        dataset_version=dataset.version,
        dataset_hash=dataset.content_sha256,
        dataset_path=str(dataset.path),
        partition="test",
        languages=list(enabled),
        git_commit=commit,
        git_dirty=dirty,
        environment=_environment(),
        embedding=embedding_meta,
        llm=llm_meta,
        prompt_version=PROMPT_VERSION,
        budget=BudgetRecord(
            max_provider_calls=max_provider_calls,
            provider_calls=budget.calls,
            prepare_calls=budget.calls,
            run_calls=0,
        ),
        prepared=prepared_index_to_dict(prepared),
        items={language: len(dataset.test_items(language)) for language in enabled},
    )
    (run_dir / "corpus-map.json").write_text(
        json.dumps(run.prepared, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_run(run_dir, run)
    return run


def _clear_run_artifacts(run_dir: Path) -> None:
    for name in (
        "run.json",
        "corpus-map.json",
        "retrieval.jsonl",
        "answers.jsonl",
        "review.jsonl",
        "review.csv",
        "metrics.json",
        "REPORT.md",
        "adjudication.redacted.jsonl",
    ):
        path = run_dir / name
        if path.is_file():
            path.unlink()


def _write_jsonl(path: Path, records: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(dict(record), ensure_ascii=False) + "\n")


def _read_jsonl(path: Path, *, what: str) -> list[dict[str, Any]]:
    if not path.is_file():
        raise EvalExit(EXIT_NOT_EVALUATED, f"falta el artefacto {what}: {path}")
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise EvalExit(EXIT_CONFIG, f"{what} línea {number}: JSON inválido") from exc
            if not isinstance(payload, dict):
                raise EvalExit(EXIT_CONFIG, f"{what} línea {number}: se esperaba un objeto")
            records.append(payload)
    return records


def load_retrieval(path: Path) -> tuple[RetrievalOutcome, ...]:
    outcomes: list[RetrievalOutcome] = []
    for number, record in enumerate(_read_jsonl(path, what="retrieval.jsonl"), start=1):
        try:
            item = _RetrievalRecord.model_validate(record)
        except ValidationError as exc:
            raise EvalExit(EXIT_CONFIG, f"retrieval.jsonl fila {number}: {exc}") from exc
        if item.matched > item.expected:
            raise EvalExit(EXIT_CONFIG, f"retrieval.jsonl fila {number}: matched > expected")
        outcomes.append(
            RetrievalOutcome(
                item_id=item.item_id,
                language=item.language,
                answerable=item.answerable,
                expected=item.expected,
                matched=item.matched,
                took_ms=item.took_ms,
                top_chunk_ids=tuple(item.top_chunk_ids),
            )
        )
    return tuple(outcomes)


def load_answers(path: Path) -> tuple[AnswerOutcome, ...]:
    answers: list[AnswerOutcome] = []
    for number, record in enumerate(_read_jsonl(path, what="answers.jsonl"), start=1):
        try:
            answers.append(answer_from_dict(record))
        except ValidationError as exc:
            raise EvalExit(EXIT_CONFIG, f"answers.jsonl fila {number}: {exc}") from exc
    return tuple(answers)


async def execute_run(
    session_factory: async_sessionmaker[AsyncSession],
    dataset: Dataset,
    *,
    run_dir: Path,
    embedding_provider: EmbeddingProvider,
    llm_provider: LLMProvider,
    partition: Literal["test", "development"] = "test",
    resume: bool = False,
    max_provider_calls: int | None = None,
    require_official: bool = True,
) -> RunManifest:
    """Retrieve and answer the selected partition, writing raw artifacts and review rows."""
    run = load_run(run_dir)
    if run.dataset_hash != dataset.content_sha256:
        raise EvalExit(EXIT_CONFIG, "el hash del dataset cambió desde prepare; no se reutiliza")
    _check_provider_compatibility(run, embedding_provider, llm_provider)
    prepared = prepared_index_from_dict(run.prepared)
    missing = sorted(set(run.languages) - set(prepared.languages))
    if missing:
        raise EvalExit(EXIT_CONFIG, f"el índice preparado no contiene: {', '.join(missing)}")
    if require_official and partition == "test" and not dataset.is_official_size():
        raise EvalExit(
            EXIT_NOT_EVALUATED,
            "el dataset no alcanza el tamaño oficial G4; solo puede producirse un smoke report",
        )

    expected_items = [
        item for language in run.languages for item in dataset.partition_items(language, partition)
    ]
    expected_by_id = {item.id: item for item in expected_items}
    if len(expected_by_id) != len(expected_items):
        raise EvalExit(EXIT_CONFIG, "el dataset contiene ids duplicados")

    limit = max_provider_calls if max_provider_calls is not None else run.budget.max_provider_calls
    budget = _Budget(limit=limit, calls=run.budget.provider_calls)
    counting_embedding = _CountingEmbedding(embedding_provider, budget)
    counting_llm = _CountingLLM(llm_provider, budget)

    existing_retrieval: dict[str, RetrievalOutcome] = {}
    existing_answers: dict[str, AnswerOutcome] = {}
    if resume:
        retrieval_path = run_dir / "retrieval.jsonl"
        answers_path = run_dir / "answers.jsonl"
        if retrieval_path.is_file():
            existing_retrieval = {row.item_id: row for row in load_retrieval(retrieval_path)}
        if answers_path.is_file():
            existing_answers = {row.item_id: row for row in load_answers(answers_path)}
    completed = {
        item_id
        for item_id, answer in existing_answers.items()
        if answer.status == "completed" and answer.error is None
    }
    attempt = run.retry_generation + 1 if resume else 0

    new_retrieval: dict[str, RetrievalOutcome] = {}
    new_answers: dict[str, AnswerOutcome] = {}
    for language in run.languages:
        index = prepared.languages[language]
        skip_retrieval = set(existing_retrieval) & set(expected_by_id)
        skip_answers = completed & set(expected_by_id)
        for retrieval_outcome in await run_retrieval(
            session_factory,
            dataset,
            language,
            index,
            provider=counting_embedding,
            partition=partition,
            skip_item_ids=skip_retrieval,
        ):
            new_retrieval[retrieval_outcome.item_id] = retrieval_outcome
        for answer_outcome in await run_answers(
            session_factory,
            dataset,
            language,
            index,
            embedding_provider=counting_embedding,
            llm_provider=counting_llm,
            run_id=run.run_id,
            partition=partition,
            skip_item_ids=skip_answers,
            attempt=attempt,
        ):
            new_answers[answer_outcome.item_id] = answer_outcome

    retrieval = [
        existing_retrieval.get(item.id) or new_retrieval.get(item.id) for item in expected_items
    ]
    answers = [existing_answers.get(item.id) or new_answers.get(item.id) for item in expected_items]
    if any(row is None for row in retrieval) or any(row is None for row in answers):
        raise EvalExit(EXIT_CONFIG, "no se produjeron artefactos para todos los ítems esperados")

    retrieval_rows = [retrieval_row_to_dict(row) for row in retrieval if row is not None]
    answer_rows = [answer_to_dict(row) for row in answers if row is not None]
    _write_jsonl(run_dir / "retrieval.jsonl", retrieval_rows)
    _write_jsonl(run_dir / "answers.jsonl", answer_rows)
    review_rows = build_review_rows([row for row in answers if row is not None])
    write_review_sheet(run_dir / "review.jsonl", review_rows)

    complete = (
        len(answer_rows) == len(expected_items)
        and len(retrieval_rows) == len(expected_items)
        and all(
            row.status == "completed" and row.error is None for row in answers if row is not None
        )
    )
    now = _now()
    run.updated_at = now
    run.status = "complete" if complete else "incomplete"
    run.partition = partition
    run.budget.max_provider_calls = limit
    run.budget.provider_calls = budget.calls
    run.budget.run_calls = budget.calls - run.budget.prepare_calls
    run.completed_items = [
        row.item_id for row in answers if row is not None and row.status == "completed"
    ]
    run.retry_generation = attempt
    if partition == "development":
        run.notes = [note for note in run.notes if note != "development partition: smoke only"]
        run.notes.append("development partition: smoke only")
    if not complete:
        run.notes = [note for note in run.notes if note != "run incomplete: score will exit 4"]
        run.notes.append("run incomplete: score will exit 4")
    write_run(run_dir, run)
    return run


def retrieval_row_to_dict(outcome: RetrievalOutcome) -> dict[str, Any]:
    return {
        "item_id": outcome.item_id,
        "language": outcome.language,
        "answerable": outcome.answerable,
        "expected": outcome.expected,
        "matched": outcome.matched,
        "took_ms": outcome.took_ms,
        "top_chunk_ids": list(outcome.top_chunk_ids),
    }


def run_exit_code(run: RunManifest) -> int:
    if run.status == "complete":
        return EXIT_PASS
    return EXIT_NOT_EVALUATED


def _index_by_id(rows: Sequence[Any], *, what: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for row in rows:
        if row.item_id in result:
            raise EvalExit(EXIT_CONFIG, f"{what} tiene un item_id duplicado: {row.item_id}")
        result[row.item_id] = row
    return result


def _exact_official_partition(dataset: Dataset) -> None:
    spec = dataset.manifest.test
    if spec.per_language != 50 or spec.answerable != 35 or spec.unanswerable != 15:
        raise EvalExit(
            EXIT_NOT_EVALUATED,
            "la partición test debe declarar exactamente 50 ítems (35 respondibles, "
            f"15 no respondibles); declara {spec.per_language}/"
            f"{spec.answerable}/{spec.unanswerable}",
        )


def _looks_abstained(answer: str, citations: int) -> bool:
    if citations:
        return False
    text = _fold(" ".join(answer.split()))
    if not text:
        return True
    markers = (
        _fold(ABSTENTION_TEXT),
        "no encuentro evidencia",
        "no puedo responder",
        "no tengo suficiente",
        "no hay informacion",
        "no se menciona",
        "i cannot answer",
        "i can't answer",
        "i don't have enough",
        "not enough information",
        "no information",
        "je ne peux pas repondre",
        "je ne trouve pas",
        "pas assez d'information",
        "aucune information",
    )
    return any(marker in text for marker in markers)


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(character for character in decomposed if not unicodedata.combining(character))


def score_run(
    dataset: Dataset,
    *,
    run_dir: Path,
    adjudication_path: Path | None = None,
    publish_dir: Path | None = None,
    require_official: bool = True,
) -> ScoreOutcome:
    """Validate completeness and adjudication, compute the gate and publish a report."""
    run = load_run(run_dir)
    if run.dataset_hash != dataset.content_sha256:
        raise EvalExit(EXIT_CONFIG, "el hash del dataset no coincide con run.json")
    if run.partition != "test":
        raise EvalExit(EXIT_NOT_EVALUATED, "solo la partición test puede emitir un veredicto G4")
    if run.status not in {"complete", "scored"}:
        raise EvalExit(EXIT_NOT_EVALUATED, f"el run está {run.status}; no se puntúa un run parcial")
    if require_official:
        _exact_official_partition(dataset)

    expected_items = [item for language in run.languages for item in dataset.test_items(language)]
    expected_by_id = {item.id: item for item in expected_items}
    if len(expected_by_id) != len(expected_items):
        raise EvalExit(EXIT_CONFIG, "el dataset contiene ids duplicados")

    retrieval = _index_by_id(load_retrieval(run_dir / "retrieval.jsonl"), what="retrieval.jsonl")
    answers = _index_by_id(load_answers(run_dir / "answers.jsonl"), what="answers.jsonl")
    missing_retrieval = sorted(set(expected_by_id) - set(retrieval))
    missing_answers = sorted(set(expected_by_id) - set(answers))
    extra_retrieval = sorted(set(retrieval) - set(expected_by_id))
    extra_answers = sorted(set(answers) - set(expected_by_id))
    if missing_retrieval or missing_answers or extra_retrieval or extra_answers:
        raise EvalExit(
            EXIT_NOT_EVALUATED,
            "el run no cubre exactamente la partición test "
            f"(faltan retrieval={len(missing_retrieval)}, answers={len(missing_answers)}; "
            f"sobran retrieval={len(extra_retrieval)}, answers={len(extra_answers)})",
        )
    failed = [
        item_id
        for item_id, answer in answers.items()
        if answer.status != "completed" or answer.error
    ]
    if failed:
        raise EvalExit(
            EXIT_NOT_EVALUATED,
            f"{len(failed)} ítem(s) terminaron con error: {', '.join(sorted(failed)[:5])}",
        )

    ordered_answers = [answers[item.id] for item in expected_items]
    ordered_retrieval = [retrieval[item.id] for item in expected_items]
    review_rows = build_review_rows(ordered_answers)
    adjudication_file = adjudication_path or (run_dir / "review.jsonl")
    try:
        adjudication = load_adjudication(adjudication_file)
        validate_adjudication(adjudication, review_rows)
    except AdjudicationError as exc:
        raise EvalExit(EXIT_ADJUDICATION, str(exc)) from exc

    citations, abstentions = reviews_from_adjudication(ordered_answers, adjudication)
    metrics: list[LanguageMetrics] = []
    for language in run.languages:
        language_answers = [answer for answer in ordered_answers if answer.language == language]
        language_retrieval = [row for row in ordered_retrieval if row.language == language]
        unauthorized = sum(answer.unauthorized for answer in language_answers)
        answerable_without_citations = sum(
            1
            for answer in language_answers
            if answer.answerable and _looks_abstained(answer.answer, len(answer.citations))
        )
        metrics.append(
            compute_language_metrics(
                language,
                [row.as_result() for row in language_retrieval],
                [review for review in citations if review.language == language],
                [review for review in abstentions if review.language == language],
                unauthorized=unauthorized,
                answerable_abstained=answerable_without_citations,
            )
        )
    verdict = overall_verdict(metrics)
    target = publish_dir or (run_dir / "published")
    write_publication(
        target,
        run=run.model_dump(mode="json"),
        dataset_version=dataset.version,
        dataset_hash=dataset.content_sha256,
        metrics=metrics,
        verdict=verdict,
        adjudication=adjudication,
        review_rows=review_rows,
    )
    run.status = "scored"
    run.updated_at = _now()
    run.completed_items = [answer.item_id for answer in ordered_answers]
    write_run(run_dir, run)
    exit_code = EXIT_PASS if verdict.passed else EXIT_GATE_FAIL
    return ScoreOutcome(
        exit_code=exit_code,
        verdict=verdict,
        metrics=tuple(metrics),
        publish_dir=target,
    )


def load_dataset_for_run(path: str | Path, run: RunManifest) -> Dataset:
    """Load a dataset and fail with exit 2 when it is invalid for this run."""
    try:
        dataset = load_dataset(Path(path), languages=run.languages, require_official=False)
    except DatasetError as exc:
        raise EvalExit(EXIT_CONFIG, str(exc)) from exc
    if dataset.content_sha256 != run.dataset_hash:
        raise EvalExit(EXIT_CONFIG, "el hash del dataset no coincide con run.json")
    return dataset


def metrics_payload(metrics: Sequence[LanguageMetrics]) -> dict[str, Any]:
    """Small helper used by tests and ``--print-metrics`` style callers."""
    return metrics_to_dict(metrics)


__all__ = [
    "DEFAULT_VAR_DIR",
    "EXIT_ADJUDICATION",
    "EXIT_CONFIG",
    "EXIT_GATE_FAIL",
    "EXIT_NOT_EVALUATED",
    "EXIT_PASS",
    "EvalExit",
    "BudgetExceeded",
    "RunManifest",
    "ScoreOutcome",
    "execute_run",
    "load_answers",
    "load_dataset_for_run",
    "load_retrieval",
    "load_run",
    "metrics_payload",
    "prepare_run",
    "run_exit_code",
    "score_run",
    "write_run",
]

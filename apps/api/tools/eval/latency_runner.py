"""L2.8 orchestration: measure retrieval and first-token latency and publish a redacted report.

The harness is offline: it reuses a prepared G4 run (tenants, index and manifest) and calls
the real ``SearchService``/``RAGService`` paths in-process. It never exposes an HTTP route,
never falls back to a fake provider in the CLI and never copies question, answer, quote or
transcript text into the published directory.

Process exit codes are the contract:

* ``0`` every enabled language passes both p95 targets; a ``PASS`` report is published;
* ``1`` the measurement completed but a language failed; the real ``FAIL`` numbers are
  published;
* ``2`` configuration, dataset/hash, provider compatibility or budget error; nothing is
  published;
* ``4`` not evaluated: fake/smoke run, non-official dataset, development partition, missing
  sample or inactive generation; nothing is published.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import sys
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.db import SessionFactory, bind_user
from app.models import Conversation
from app.services.embeddings import (
    EmbeddingNotConfigured,
    EmbeddingProvider,
    HttpEmbeddingProvider,
)
from app.services.indexing import active_generation
from app.services.llm import HttpChatProvider, LLMNotConfigured, LLMProvider
from app.services.rag import RAGService
from app.services.search import SearchService
from tools.eval.chat import parse_sse
from tools.eval.corpus import (
    CorpusError,
    LanguageIndex,
    PreparedIndex,
    prepared_index_from_dict,
)
from tools.eval.dataset import Dataset, DatasetError, load_dataset
from tools.eval.latency import (
    DEFAULT_CONCURRENCY,
    FIRST_TOKEN_P95_TARGET_MS,
    RETRIEVAL_P95_TARGET_MS,
    FirstTokenSample,
    FirstTokenSource,
    LanguageLatency,
    LatencyMetadata,
    LatencyVerdict,
    RetrievalSample,
    build_payload,
    default_limitations,
    overall_verdict,
    render_report,
)
from tools.eval.retrieval import TOP_K
from tools.eval.runner import (
    DEFAULT_VAR_DIR,
    EXIT_CONFIG,
    EXIT_GATE_FAIL,
    EXIT_NOT_EVALUATED,
    EXIT_PASS,
    EvalExit,
    RunManifest,
    _Budget,
    _check_provider_compatibility,
    _CountingEmbedding,
    _CountingLLM,
    _environment,
    _git_metadata,
    load_run,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_PUBLISH_ROOT = REPO_ROOT / "docs" / "eval" / "latency"
DEFAULT_MAX_PROVIDER_CALLS = 600
SMOKE_STATUS = "SMOKE"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LatencyOutcome:
    """Measured gate decision plus the exact redacted payload that was (or was not) published."""

    exit_code: int
    payload: Mapping[str, Any]
    publish_dir: Path | None


@dataclass(frozen=True)
class _FirstToken:
    """One first-delta timing and whether the first text was an abstention."""

    took_ms: float
    source: FirstTokenSource
    close_failed: bool = False


@dataclass(frozen=True)
class _LanguageMeasurement:
    """One language's samples plus the limitations discovered while measuring it."""

    latency: LanguageLatency
    limitations: tuple[str, ...]
    cold_start_fallback: bool


def _split_languages(raw: str) -> tuple[str, ...]:
    languages = tuple(part.strip() for part in raw.split(",") if part.strip())
    if not languages:
        raise EvalExit(EXIT_CONFIG, "--languages no puede estar vacío")
    return languages


def _enabled_languages(
    run: RunManifest, requested: Sequence[str] | None
) -> tuple[str, ...]:
    """Validate the requested languages against the prepared run."""
    enabled = tuple(requested) if requested else tuple(run.languages)
    if not enabled:
        raise EvalExit(EXIT_CONFIG, "no hay idiomas habilitados")
    if len(set(enabled)) != len(enabled):
        raise EvalExit(EXIT_CONFIG, "los idiomas no pueden repetirse")
    unknown = sorted(set(enabled) - set(run.languages))
    if unknown:
        raise EvalExit(
            EXIT_CONFIG,
            f"idiomas no preparados en el run: {', '.join(unknown)}",
        )
    return enabled


def _load_dataset(path: Path, languages: Sequence[str]) -> Dataset:
    try:
        return load_dataset(path, languages=languages, require_official=False)
    except DatasetError as exc:
        raise EvalExit(EXIT_CONFIG, f"dataset inválido: {exc}") from exc


def _verify_run_matches_dataset(run: RunManifest, dataset: Dataset) -> None:
    if run.dataset_hash != dataset.content_sha256:
        raise EvalExit(EXIT_CONFIG, "el hash del dataset no coincide con run.json")
    if run.dataset_version != dataset.version:
        raise EvalExit(
            EXIT_CONFIG,
            f"la versión {dataset.version!r} no coincide con el run {run.dataset_version!r}",
        )


def _require_official_dataset(dataset: Dataset) -> None:
    """The official gate only accepts the frozen 50/35/15 test partition."""
    spec = dataset.manifest.test
    if spec.per_language != 50 or spec.answerable != 35 or spec.unanswerable != 15:
        raise EvalExit(
            EXIT_NOT_EVALUATED,
            "la partición test no es la oficial 50/35/15 ("
            f"{spec.per_language}/{spec.answerable}/{spec.unanswerable}); "
            "solo puede emitirse un informe SMOKE",
        )


def _require_official_providers(
    run: RunManifest,
    embedding_provider: EmbeddingProvider,
    llm_provider: LLMProvider,
) -> None:
    """Reject fake providers in both the live run and the metadata that built the index."""
    problems: list[str] = []
    if run.embedding.get("provider") != "HttpEmbeddingProvider":
        problems.append("el índice se preparó con embeddings no oficiales")
    if run.llm.get("provider") != "HttpChatProvider":
        problems.append("el run se preparó con un LLM no oficial")
    if not isinstance(embedding_provider, HttpEmbeddingProvider):
        problems.append("el proveedor de embeddings activo no es HttpEmbeddingProvider")
    if not isinstance(llm_provider, HttpChatProvider):
        problems.append("el proveedor LLM activo no es HttpChatProvider")
    if problems:
        raise EvalExit(
            EXIT_NOT_EVALUATED,
            "medición oficial rechazada: " + "; ".join(problems),
        )


def _prepared_index(run: RunManifest, languages: Sequence[str]) -> PreparedIndex:
    try:
        prepared = prepared_index_from_dict(run.prepared)
    except ValidationError as exc:
        raise EvalExit(EXIT_CONFIG, f"índice preparado inválido: {exc}") from exc
    if prepared.dataset_hash != run.dataset_hash:
        raise EvalExit(EXIT_CONFIG, "el índice preparado pertenece a otro dataset")
    missing = sorted(set(languages) - set(prepared.languages))
    if missing:
        raise EvalExit(
            EXIT_CONFIG,
            f"el índice preparado no contiene: {', '.join(missing)}",
        )
    return prepared


async def _require_active_generations(
    session_factory: async_sessionmaker[AsyncSession],
    prepared: PreparedIndex,
    languages: Sequence[str],
) -> None:
    """A retired generation cannot produce a latency sample; abort before measuring."""
    for language in languages:
        index = prepared.languages[language]
        bind_user(index.user_id)
        async with session_factory() as db:
            current = await active_generation(db, index.user_id)
        if current is None or current.version != index.generation:
            raise EvalExit(
                EXIT_NOT_EVALUATED,
                f"{language}: la generación preparada {index.generation} ya no está activa",
            )


def _measurement_error(
    language: str,
    item_id: str,
    what: str,
    exc: BaseException,
) -> EvalExit:
    # Only the exception type is published: item text, prompts and provider payloads stay out.
    return EvalExit(
        EXIT_NOT_EVALUATED,
        f"{language}: {what} falló en {item_id} ({type(exc).__name__})",
    )


async def _measure_retrieval(
    session_factory: async_sessionmaker[AsyncSession],
    index: LanguageIndex,
    *,
    item_id: str,
    question: str,
    provider: EmbeddingProvider,
) -> float:
    """Time session checkout, query embedding, both branches, fusion and hydration."""
    bind_user(index.user_id)
    started = perf_counter()
    try:
        async with session_factory() as db:
            hits = await SearchService(db, provider=provider).retrieve(
                user_id=index.user_id,
                query=question,
                top_k=TOP_K,
            )
    except EvalExit:
        raise
    except Exception as exc:  # noqa: BLE001 - a failed sample makes the measurement incomplete
        raise _measurement_error(index.language, item_id, "recuperación", exc) from exc
    took_ms = (perf_counter() - started) * 1000.0
    if not hits:
        raise EvalExit(
            EXIT_NOT_EVALUATED,
            f"{index.language}: la recuperación no devolvió resultados en {item_id}",
        )
    return took_ms


async def _create_conversation(
    session_factory: async_sessionmaker[AsyncSession],
    index: LanguageIndex,
    item_id: str,
) -> uuid.UUID:
    """Create the isolated conversation; conversation creation is not part of the timing."""
    async with session_factory() as db:
        conversation = Conversation(
            user_id=index.user_id,
            mode="all",
            title=f"L2.8 {item_id}",
        )
        db.add(conversation)
        await db.flush()
        conversation_id = conversation.id
        await db.commit()
    return conversation_id


async def _measure_first_token(
    session_factory: async_sessionmaker[AsyncSession],
    service: RAGService,
    index: LanguageIndex,
    *,
    item_id: str,
    question: str,
    run_id: str,
) -> _FirstToken:
    """Time ``prepare`` plus the SSE stream up to the first non-empty delta."""
    bind_user(index.user_id)
    try:
        conversation_id = await _create_conversation(session_factory, index, item_id)
        started = perf_counter()
        prepared = await service.prepare(
            user_id=index.user_id,
            conversation_id=conversation_id,
            content=question,
            client_message_id=f"l2.8:{run_id}:{item_id}:{uuid.uuid4().hex[:8]}",
        )
        if prepared.error is not None:
            raise EvalExit(
                EXIT_NOT_EVALUATED,
                f"{index.language}: prepare devolvió {prepared.error.code} en {item_id}",
            )
        source: FirstTokenSource = (
            "abstention" if prepared.abstention_text is not None else "llm"
        )
        stream = service.stream_events(prepared)
        measured_ms: float | None = None
        close_failed = False
        try:
            async for frame in stream:
                event = parse_sse(frame)
                if event is None:
                    continue
                if event.name == "delta":
                    text = event.data.get("text")
                    if isinstance(text, str) and text:
                        measured_ms = (perf_counter() - started) * 1000.0
                        break
                elif event.name == "error":
                    raise EvalExit(
                        EXIT_NOT_EVALUATED,
                        f"{index.language}: el stream devolvió un error en {item_id}",
                    )
                elif event.name == "done":
                    break
        finally:
            # Stop the producer and persist the isolated cancelled partial immediately.
            # A close failure after a valid delta must not discard an already measured sample.
            try:
                await stream.aclose()
            except Exception:  # noqa: BLE001 - cleanup must not hide a valid first delta
                if measured_ms is not None:
                    close_failed = True
        if measured_ms is None:
            raise EvalExit(
                EXIT_NOT_EVALUATED,
                f"{index.language}: el stream terminó sin primer delta en {item_id}",
            )
        return _FirstToken(took_ms=measured_ms, source=source, close_failed=close_failed)
    except EvalExit:
        raise
    except Exception as exc:  # noqa: BLE001 - a failed sample makes the measurement incomplete
        raise _measurement_error(index.language, item_id, "primer token", exc) from exc


async def _measure_language(
    session_factory: async_sessionmaker[AsyncSession],
    dataset: Dataset,
    index: LanguageIndex,
    *,
    embedding_provider: EmbeddingProvider,
    llm_provider: LLMProvider,
    run_id: str,
    retrieval_target_ms: float,
    first_token_target_ms: float,
) -> _LanguageMeasurement:
    """Measure every test item of one language and return its samples plus limitations."""
    language = index.language
    bind_user(index.user_id)
    test_items = dataset.test_items(language)
    if not test_items:
        raise EvalExit(EXIT_NOT_EVALUATED, f"{language}: la partición test está vacía")

    development_items = dataset.development_items(language)
    warm_item = development_items[0] if development_items else test_items[0]
    warm_in_samples = not development_items

    service = RAGService(
        session_factory=session_factory,
        embedding_provider=embedding_provider,
        llm_provider=llm_provider,
    )
    cold_retrieval_ms = await _measure_retrieval(
        session_factory,
        index,
        item_id=warm_item.id,
        question=warm_item.question,
        provider=embedding_provider,
    )
    cold_first_token = await _measure_first_token(
        session_factory,
        service,
        index,
        item_id=warm_item.id,
        question=warm_item.question,
        run_id=run_id,
    )
    close_failures = 1 if cold_first_token.close_failed else 0

    retrieval_samples: list[RetrievalSample] = []
    first_token_samples: list[FirstTokenSample] = []
    if warm_in_samples:
        retrieval_samples.append(
            RetrievalSample(
                item_id=warm_item.id,
                language=language,
                took_ms=cold_retrieval_ms,
            )
        )
        first_token_samples.append(
            FirstTokenSample(
                item_id=warm_item.id,
                language=language,
                first_token_ms=cold_first_token.took_ms,
                source=cold_first_token.source,
            )
        )

    for item in test_items:
        if warm_in_samples and item.id == warm_item.id:
            continue
        retrieval_samples.append(
            RetrievalSample(
                item_id=item.id,
                language=language,
                took_ms=await _measure_retrieval(
                    session_factory,
                    index,
                    item_id=item.id,
                    question=item.question,
                    provider=embedding_provider,
                ),
            )
        )
        first_token = await _measure_first_token(
            session_factory,
            service,
            index,
            item_id=item.id,
            question=item.question,
            run_id=run_id,
        )
        close_failures += 1 if first_token.close_failed else 0
        first_token_samples.append(
            FirstTokenSample(
                item_id=item.id,
                language=language,
                first_token_ms=first_token.took_ms,
                source=first_token.source,
            )
        )

    limitations: list[str] = []
    if warm_in_samples:
        limitations.append(
            f"{language}: no development item was available; the first test item served as "
            "cold start and remains in the p95 distribution."
        )
    if close_failures:
        limitations.append(
            f"{language}: {close_failures} chat stream(s) failed to close after the first "
            "delta; those samples were kept."
        )
    latency = LanguageLatency(
        language=language,
        expected_items=len(test_items),
        expected_item_ids=tuple(item.id for item in test_items),
        retrieval=tuple(retrieval_samples),
        first_token=tuple(first_token_samples),
        cold_retrieval_ms=cold_retrieval_ms,
        cold_first_token_ms=cold_first_token.took_ms,
        retrieval_target_ms=retrieval_target_ms,
        first_token_target_ms=first_token_target_ms,
    )
    return _LanguageMeasurement(
        latency=latency,
        limitations=tuple(limitations),
        cold_start_fallback=warm_in_samples,
    )


def _host_memory_mb() -> str:
    """Best-effort total host memory; never include process, host or user identifiers."""
    sysconf = getattr(os, "sysconf", None)
    if sysconf is not None:
        try:
            pages = int(sysconf("SC_PHYS_PAGES"))
            page_size = int(sysconf("SC_PAGE_SIZE"))
            if pages > 0 and page_size > 0:
                return str(round(pages * page_size / (1024 * 1024)))
        except (ValueError, OSError):
            pass
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                return str(round(int(line.split()[1]) / 1024))
    except (OSError, ValueError, IndexError):
        pass
    return "unknown"


async def _database_environment(
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, str]:
    """Record database context without exposing the DSN or any connection secret."""
    environment = {"postgres": "unknown", "pgvector": "unknown"}
    try:
        async with session_factory() as db:
            version = await db.scalar(text("SELECT version()"))
            if version:
                environment["postgres"] = str(version).split(" on ")[0].strip()
            extension = await db.scalar(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            )
            if extension:
                environment["pgvector"] = str(extension)
    except Exception as exc:  # noqa: BLE001 - best-effort metadata; never publish the DSN
        logger.warning("l2.8.environment.database_metadata_failed error=%s", type(exc).__name__)
    return environment


def _metadata(
    run: RunManifest,
    dataset: Dataset,
    prepared: PreparedIndex,
    languages: Sequence[str],
    *,
    limitations: Sequence[str],
    cold_start: str,
    database_environment: Mapping[str, str],
) -> LatencyMetadata:
    environment = dict(_environment())
    environment["cpu_count"] = str(os.cpu_count() or 0)
    environment["memory_total_mb"] = _host_memory_mb()
    environment.update(database_environment)
    environment["redis"] = "not used by the in-process measurement"
    commit, dirty = _git_metadata()
    sample_sizes = {
        language: len(dataset.test_items(language)) for language in languages
    }
    return LatencyMetadata(
        measured_at=datetime.now(UTC).isoformat(timespec="seconds"),
        run_id=run.run_id,
        dataset_version=dataset.version,
        dataset_hash=dataset.content_sha256,
        embedding=dict(run.embedding),
        llm=dict(run.llm),
        prompt_version=run.prompt_version,
        environment=environment,
        git_commit=commit,
        git_dirty=dirty,
        partition="test",
        concurrency=DEFAULT_CONCURRENCY,
        language_metadata={
            language: {
                "classes": len(dataset.corpus[language]),
                "chunks": prepared.languages[language].chunks,
            }
            for language in languages
        },
        limitations=default_limitations(sample_sizes=sample_sizes) + tuple(limitations),
        cold_start=cold_start,
    )


def _require_clean_tree() -> None:
    """Official evidence must be reproducible from the exact committed tree."""
    _, dirty = _git_metadata()
    if dirty is not False:
        detail = "no se pudo determinar" if dirty is None else "hay cambios sin commitear"
        raise EvalExit(
            EXIT_CONFIG,
            f"medición oficial rechazada: árbol de trabajo sucio ({detail})",
        )


def publish_latency(publish_dir: Path, payload: Mapping[str, Any]) -> None:
    """Publish both artifacts through a sibling directory swap.

    A reader never sees a half-written ``latency.json`` or ``REPORT.md``: both files are
    written to a temporary sibling first and the complete directory is renamed into place.
    """
    parent = publish_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    temporary = parent / f".{publish_dir.name}.tmp-{uuid.uuid4().hex}"
    backup = parent / f".{publish_dir.name}.old-{uuid.uuid4().hex}"
    report = render_report(payload)
    try:
        temporary.mkdir()
        (temporary / "latency.json").write_text(
            json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (temporary / "REPORT.md").write_text(report, encoding="utf-8")
        if publish_dir.exists():
            os.replace(publish_dir, backup)
        os.replace(temporary, publish_dir)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        if backup.exists() and not publish_dir.exists():
            os.replace(backup, publish_dir)
        raise
    finally:
        shutil.rmtree(backup, ignore_errors=True)


async def measure_latency(
    session_factory: async_sessionmaker[AsyncSession],
    dataset: Dataset,
    *,
    run_dir: Path,
    embedding_provider: EmbeddingProvider,
    llm_provider: LLMProvider,
    languages: Sequence[str] | None = None,
    retrieval_target_ms: float = RETRIEVAL_P95_TARGET_MS,
    first_token_target_ms: float = FIRST_TOKEN_P95_TARGET_MS,
    max_provider_calls: int = DEFAULT_MAX_PROVIDER_CALLS,
    publish_dir: Path | None = None,
    allow_smoke: bool = False,
) -> LatencyOutcome:
    """Measure the latency gate on a prepared run.

    ``allow_smoke`` exists only for deterministic tests: it accepts fake providers and the
    ``smoke-v1`` dataset, marks the payload ``SMOKE`` and always returns exit ``4``. The CLI
    never sets it, so a smoke run can never be published as L2.8 evidence.
    """
    run = load_run(run_dir)
    enabled = _enabled_languages(run, languages)
    _verify_run_matches_dataset(run, dataset)
    if max_provider_calls < 1:
        raise EvalExit(EXIT_CONFIG, "--max-provider-calls debe ser positivo")
    if retrieval_target_ms <= 0 or first_token_target_ms <= 0:
        raise EvalExit(EXIT_CONFIG, "los umbrales de latencia deben ser positivos")

    if not allow_smoke:
        if run.partition != "test":
            raise EvalExit(
                EXIT_NOT_EVALUATED,
                "solo la partición test puede emitir un veredicto L2.8",
            )
        _require_official_dataset(dataset)
        _require_official_providers(run, embedding_provider, llm_provider)
        _require_clean_tree()

    _check_provider_compatibility(run, embedding_provider, llm_provider)
    prepared = _prepared_index(run, enabled)
    await _require_active_generations(session_factory, prepared, enabled)
    database_environment = await _database_environment(session_factory)

    budget = _Budget(limit=max_provider_calls, calls=run.budget.provider_calls)
    counting_embedding = _CountingEmbedding(embedding_provider, budget)
    counting_llm = _CountingLLM(llm_provider, budget)

    measurements: list[_LanguageMeasurement] = []
    limitations: list[str] = []
    for language in enabled:
        measurement = await _measure_language(
            session_factory,
            dataset,
            prepared.languages[language],
            embedding_provider=counting_embedding,
            llm_provider=counting_llm,
            run_id=run.run_id,
            retrieval_target_ms=retrieval_target_ms,
            first_token_target_ms=first_token_target_ms,
        )
        measurements.append(measurement)
        limitations.extend(measurement.limitations)

    language_latencies = [measurement.latency for measurement in measurements]
    fallback_languages = [
        measurement.latency.language
        for measurement in measurements
        if measurement.cold_start_fallback
    ]
    if fallback_languages:
        cold_start = (
            "At least one language had no development item; its first test item served as "
            "cold start and remains in the p95 distribution: "
            + ", ".join(fallback_languages)
            + "."
        )
    else:
        cold_start = (
            "One warm-up retrieval and one warm-up chat per language from the development "
            "partition; excluded from the p95 distributions."
        )

    verdict: LatencyVerdict = overall_verdict(language_latencies)
    payload = build_payload(
        _metadata(
            run,
            dataset,
            prepared,
            enabled,
            limitations=limitations,
            cold_start=cold_start,
            database_environment=database_environment,
        ),
        language_latencies,
        verdict,
    )
    if allow_smoke:
        payload["status"] = SMOKE_STATUS

    if publish_dir is not None:
        publish_latency(publish_dir, payload)

    if allow_smoke:
        exit_code = EXIT_NOT_EVALUATED
    else:
        exit_code = EXIT_PASS if verdict.passed else EXIT_GATE_FAIL
    return LatencyOutcome(
        exit_code=exit_code,
        payload=payload,
        publish_dir=publish_dir,
    )


def _real_providers() -> tuple[HttpEmbeddingProvider, HttpChatProvider]:
    try:
        embedding = HttpEmbeddingProvider.from_settings(settings)
        llm = HttpChatProvider.from_settings(settings)
    except (EmbeddingNotConfigured, LLMNotConfigured) as exc:
        raise EvalExit(
            EXIT_CONFIG,
            f"proveedores reales no configurados; L2.8 queda NOT MEASURED: {exc}",
        ) from exc
    return embedding, llm


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/load/latency_rag.py",
        description=(
            "Mide la latencia de recuperación y del primer token de chat sobre un run G4 "
            "preparado y publica un informe redactado."
        ),
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--languages", help="lista separada por comas")
    parser.add_argument("--publish", type=Path)
    parser.add_argument(
        "--retrieval-threshold-ms",
        type=float,
        default=RETRIEVAL_P95_TARGET_MS,
    )
    parser.add_argument(
        "--first-token-threshold-ms",
        type=float,
        default=FIRST_TOKEN_P95_TARGET_MS,
    )
    parser.add_argument(
        "--max-provider-calls",
        type=int,
        default=DEFAULT_MAX_PROVIDER_CALLS,
    )
    return parser


def _print_outcome(outcome: LatencyOutcome) -> None:
    payload = outcome.payload
    print(f"L2.8 latencia: {payload.get('status', 'NOT_EVALUATED')}")
    languages = payload.get("languages", {})
    if isinstance(languages, Mapping):
        for language, data in languages.items():
            retrieval = data.get("retrieval", {})
            first_token = data.get("first_token", {})
            print(
                f"  {language}: retrieval p95={retrieval.get('p95_ms')} ms "
                f"({'PASS' if retrieval.get('passed') else 'FAIL'}); "
                f"primer token p95={first_token.get('p95_ms')} ms "
                f"({'PASS' if first_token.get('passed') else 'FAIL'})"
            )
    reasons = payload.get("verdict_reasons", [])
    if isinstance(reasons, Sequence):
        for reason in reasons:
            print(f"  fallo: {reason}")
    if outcome.publish_dir is not None:
        print(f"publicado: {outcome.publish_dir}")


def _run(args: argparse.Namespace) -> int:
    run_dir = args.run_dir or (DEFAULT_VAR_DIR / args.run_id)
    run = load_run(run_dir)
    requested = _split_languages(args.languages) if args.languages else None
    enabled = _enabled_languages(run, requested)
    dataset_path = args.dataset or Path(run.dataset_path)
    dataset = _load_dataset(dataset_path, enabled)
    publish = args.publish or (DEFAULT_PUBLISH_ROOT / args.run_id)
    embedding, llm = _real_providers()
    outcome = asyncio.run(
        measure_latency(
            SessionFactory,
            dataset,
            run_dir=run_dir,
            embedding_provider=embedding,
            llm_provider=llm,
            languages=enabled,
            retrieval_target_ms=args.retrieval_threshold_ms,
            first_token_target_ms=args.first_token_threshold_ms,
            max_provider_calls=args.max_provider_calls,
            publish_dir=publish,
        )
    )
    _print_outcome(outcome)
    return outcome.exit_code


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return _run(args)
    except EvalExit as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        return exc.exit_code
    except DatasetError as exc:
        for problem in exc.problems:
            print(f"dataset: {problem}", file=sys.stderr)
        return EXIT_CONFIG
    except CorpusError as exc:
        for problem in exc.problems:
            print(f"corpus: {problem}", file=sys.stderr)
        return EXIT_CONFIG
    except ValidationError as exc:
        print(f"error de configuración: {exc}", file=sys.stderr)
        return EXIT_NOT_EVALUATED


__all__ = [
    "DEFAULT_MAX_PROVIDER_CALLS",
    "DEFAULT_PUBLISH_ROOT",
    "LatencyOutcome",
    "measure_latency",
    "publish_latency",
]


if __name__ == "__main__":
    raise SystemExit(main())

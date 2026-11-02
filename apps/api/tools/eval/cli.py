"""``validate``/``prepare``/``run``/``score`` CLI for the G4 harness (L2.7).

The process exit codes are the contract:

* ``0`` all enabled languages pass with complete human adjudication;
* ``1`` gate failure (threshold, unauthorized citation or zero citations);
* ``2`` configuration, dataset, hash, provider or preparation error;
* ``3`` incomplete or invalid human adjudication;
* ``4`` not evaluated (fake providers unavailable in the CLI, partial run, errors, dev partition).

The CLI never falls back to a fake provider. Integration tests call the runner functions with
deterministic fakes; only the real, configured providers are accepted here.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from app.core.config import settings
from app.core.db import SessionFactory
from app.services.embeddings import EmbeddingNotConfigured, HttpEmbeddingProvider
from app.services.llm import HttpChatProvider, LLMNotConfigured
from tools.eval.corpus import CorpusError
from tools.eval.dataset import Dataset, DatasetError, load_dataset
from tools.eval.runner import (
    DEFAULT_VAR_DIR,
    EXIT_CONFIG,
    EXIT_NOT_EVALUATED,
    EvalExit,
    execute_run,
    load_dataset_for_run,
    load_run,
    prepare_run,
    run_exit_code,
    score_run,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.eval.cli",
        description="Harness offline de evaluación G4 (dataset, retrieval, RAG y revisión).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="valida el dataset sin BD ni proveedor")
    validate.add_argument("--dataset", type=Path, required=True)
    validate.add_argument("--languages", help="lista separada por comas")
    validate.add_argument(
        "--official",
        action="store_true",
        help="exige el tamaño oficial G4 (50/35/15 por idioma)",
    )

    prepare = subparsers.add_parser("prepare", help="siembra tenants e índice de evaluación")
    prepare.add_argument("--dataset", type=Path, required=True)
    prepare.add_argument("--languages", help="lista separada por comas; por defecto, la de ingesta")
    prepare.add_argument("--run-id")
    prepare.add_argument("--run-dir", type=Path)
    prepare.add_argument("--reset", action="store_true", help="reconstruye el tenant y el índice")
    prepare.add_argument("--max-provider-calls", type=int, default=400)

    run = subparsers.add_parser("run", help="ejecuta retrieval y respuestas sobre una partición")
    run.add_argument("--dataset", type=Path)
    run.add_argument("--run-id", required=True)
    run.add_argument("--run-dir", type=Path)
    run.add_argument("--languages", help="debe coincidir con los idiomas del run preparado")
    run.add_argument("--partition", choices=("test", "development"), default="test")
    run.add_argument("--resume", action="store_true")
    run.add_argument("--max-provider-calls", type=int)

    score = subparsers.add_parser("score", help="valida la revisión humana y publica el informe")
    score.add_argument("--dataset", type=Path)
    score.add_argument("--run-id", required=True)
    score.add_argument("--run-dir", type=Path)
    score.add_argument("--adjudication", type=Path)
    score.add_argument("--publish", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            return _validate(args)
        if args.command == "prepare":
            return _prepare(args)
        if args.command == "run":
            return _run(args)
        if args.command == "score":
            return _score(args)
        raise EvalExit(EXIT_CONFIG, f"comando desconocido: {args.command}")
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


def _validate(args: argparse.Namespace) -> int:
    enabled = _enabled_languages(args.languages)
    dataset = _load_dataset(args.dataset, languages=args.languages, official=args.official)
    print(
        f"OK: dataset {dataset.version} ({dataset.content_sha256}), "
        f"{len(enabled)} idiomas, "
        f"{sum(len(dataset.items[language]) for language in enabled)} ítems"
    )
    return 0


def _prepare(args: argparse.Namespace) -> int:
    enabled = _enabled_languages(args.languages)
    dataset = _load_dataset(args.dataset, languages=args.languages, official=False)
    run_id = args.run_id or (
        f"{dataset.version}-{dataset.content_sha256[:8]}-"
        f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    )
    run_dir = args.run_dir or (DEFAULT_VAR_DIR / run_id)
    embedding, llm = _real_providers()
    run = asyncio.run(
        prepare_run(
            SessionFactory,
            dataset,
            languages=enabled,
            embedding_provider=embedding,
            llm_provider=llm,
            run_dir=run_dir,
            run_id=run_id,
            reset=args.reset,
            max_provider_calls=args.max_provider_calls,
        )
    )
    print(f"prepared: {run.run_id} -> {run_dir}")
    print(f"dataset: {run.dataset_version} sha256:{run.dataset_hash}")
    for index in run.prepared.get("languages", []):
        print(f"  {index['language']}: tenant {index['user_id']}, generación {index['generation']}")
    return 0


def _run(args: argparse.Namespace) -> int:
    run_dir = args.run_dir or (DEFAULT_VAR_DIR / args.run_id)
    run = load_run(run_dir)
    dataset_path = args.dataset or Path(run.dataset_path)
    dataset = load_dataset_for_run(dataset_path, run)
    if args.languages:
        requested = _split_languages(args.languages)
        if requested != tuple(run.languages):
            raise EvalExit(
                EXIT_CONFIG,
                f"--languages {','.join(requested)} no coincide con el run "
                f"({','.join(run.languages)})",
            )
    embedding, llm = _real_providers()
    updated = asyncio.run(
        execute_run(
            SessionFactory,
            dataset,
            run_dir=run_dir,
            embedding_provider=embedding,
            llm_provider=llm,
            partition=args.partition,
            resume=args.resume,
            max_provider_calls=args.max_provider_calls,
        )
    )
    print(f"run: {updated.run_id} status={updated.status}")
    print(f"artefactos: {run_dir}")
    if updated.status == "complete":
        print(f"revisión humana: {run_dir / 'review.jsonl'} y {run_dir / 'review.csv'}")
    return run_exit_code(updated)


def _score(args: argparse.Namespace) -> int:
    run_dir = args.run_dir or (DEFAULT_VAR_DIR / args.run_id)
    run = load_run(run_dir)
    dataset_path = args.dataset or Path(run.dataset_path)
    dataset = load_dataset_for_run(dataset_path, run)
    adjudication = args.adjudication or (run_dir / "review.jsonl")
    publish = args.publish or (run_dir / "published")
    outcome = score_run(
        dataset,
        run_dir=run_dir,
        adjudication_path=adjudication,
        publish_dir=publish,
    )
    print(f"gate: {'PASS' if outcome.verdict.passed else 'FAIL'}")
    for metrics in outcome.metrics:
        print(
            f"  {metrics.language}: Recall@8 "
            f"{metrics.recall_at_8.numerator}/{metrics.recall_at_8.denominator}, "
            f"citas {metrics.citation_precision.numerator}/"
            f"{metrics.citation_precision.denominator}, "
            f"abstención {metrics.abstention.numerator}/{metrics.abstention.denominator}, "
            f"no autorizadas {metrics.unauthorized}"
        )
    print(f"publicado: {publish}")
    return outcome.exit_code


def _load_dataset(path: Path, *, languages: str | None, official: bool) -> Dataset:
    enabled = _enabled_languages(languages)
    return load_dataset(path, languages=enabled, require_official=official)


def _enabled_languages(raw: str | None) -> tuple[str, ...]:
    if raw:
        return _split_languages(raw)
    return tuple(settings.ingest_languages)


def _split_languages(raw: str) -> tuple[str, ...]:
    languages = tuple(part.strip() for part in raw.split(",") if part.strip())
    if not languages:
        raise EvalExit(EXIT_CONFIG, "--languages no puede estar vacío")
    return languages


def _real_providers() -> tuple[HttpEmbeddingProvider, HttpChatProvider]:
    try:
        embedding = HttpEmbeddingProvider.from_settings(settings)
        llm = HttpChatProvider.from_settings(settings)
    except (EmbeddingNotConfigured, LLMNotConfigured) as exc:
        raise EvalExit(
            EXIT_NOT_EVALUATED,
            f"proveedores reales no configurados; G4 queda NOT EVALUATED: {exc}",
        ) from exc
    return embedding, llm


if __name__ == "__main__":
    raise SystemExit(main())

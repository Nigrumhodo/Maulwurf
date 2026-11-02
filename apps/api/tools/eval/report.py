"""Review sheets, redacted adjudication and G4 report publication (L2.7).

Citation precision and abstention are human judgements, so the harness never infers them from
the answer text. It emits one review row per emitted citation and one per unanswerable item;
``score`` refuses to publish until every row has exactly one verdict with a reviewer and date.

The review sheet under ``var/eval/`` contains question, answer and quote text and stays local.
The published directory contains only aggregates and identifiers: ``REPORT.md``,
``metrics.json`` and ``adjudication.redacted.jsonl``.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from tools.eval.chat import AnswerOutcome
from tools.eval.metrics import (
    AbstentionReview,
    CitationReview,
    LanguageMetrics,
    OverallVerdict,
    metrics_to_dict,
)

CITATION = "citation"
ABSTENTION = "abstention"
CITATION_VERDICTS = frozenset({"correct", "incorrect"})
ABSTENTION_VERDICTS = frozenset({"abstained", "answered"})

CSV_FIELDS = (
    "row_id",
    "kind",
    "item_id",
    "language",
    "answerable",
    "citation_index",
    "chunk_id",
    "segment_id",
    "audio_id",
    "transcript_version",
    "quote",
    "question",
    "answer",
    "citations",
    "answer_excerpt",
    "verdict",
    "reviewer",
    "reviewed_at",
    "notes",
)

REVIEW_FIELDS = frozenset(
    {
        "row_id",
        "kind",
        "item_id",
        "language",
        "answerable",
        "citation_index",
        "chunk_id",
        "segment_id",
        "audio_id",
        "transcript_version",
        "quote",
        "question",
        "answer",
        "citations",
        "answer_excerpt",
    }
)


class AdjudicationError(ValueError):
    """The human review file is missing, incomplete or inconsistent."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = tuple(problems)
        super().__init__("\n".join(self.problems))


class AdjudicationRow(BaseModel):
    """One reviewed row; the reviewer fills ``verdict``, ``reviewer`` and ``reviewed_at``."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    row_id: str = Field(min_length=1)
    kind: Literal["citation", "abstention"]
    verdict: str = Field(min_length=1)
    reviewer: str = Field(min_length=1)
    reviewed_at: datetime
    notes: str | None = None

    @model_validator(mode="after")
    def _verdict_matches_kind(self) -> AdjudicationRow:
        allowed = CITATION_VERDICTS if self.kind == CITATION else ABSTENTION_VERDICTS
        if self.verdict not in allowed:
            raise ValueError(
                f"verdict {self.verdict!r} no válido para {self.kind}; "
                f"usa {', '.join(sorted(allowed))}"
            )
        return self


@dataclass(frozen=True)
class Adjudication:
    """Validated verdicts keyed by row id, with the reviewer set in order."""

    rows: Mapping[str, AdjudicationRow]

    @property
    def reviewers(self) -> tuple[str, ...]:
        return tuple(sorted({row.reviewer for row in self.rows.values()}))


def answer_excerpt(answer: str, *, limit: int = 320) -> str:
    """Short single-line excerpt for human review; never used in published output."""
    collapsed = " ".join(answer.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


def citation_row_id(item_id: str, index: int, occurrence: int) -> str:
    """Stable id for one emitted citation, including repeated markers of the same source."""
    return f"{item_id}:citation:{index}:{occurrence}"


def abstention_row_id(item_id: str) -> str:
    return f"{item_id}:abstention"


def build_review_rows(answers: Sequence[AnswerOutcome]) -> list[dict[str, Any]]:
    """One row per emitted citation and one per unanswerable item, in answer order."""
    rows: list[dict[str, Any]] = []
    for answer in answers:
        occurrences: dict[int, int] = {}
        for citation in answer.citations:
            occurrence = occurrences.get(citation.index, 0)
            occurrences[citation.index] = occurrence + 1
            rows.append(
                {
                    "row_id": citation_row_id(answer.item_id, citation.index, occurrence),
                    "kind": CITATION,
                    "item_id": answer.item_id,
                    "language": answer.language,
                    "answerable": answer.answerable,
                    "citation_index": citation.index,
                    "chunk_id": citation.chunk_id,
                    "segment_id": citation.segment_id,
                    "audio_id": citation.audio_id,
                    "transcript_version": citation.transcript_version,
                    "quote": citation.quote,
                    "question": None,
                    "answer": None,
                    "citations": None,
                    "answer_excerpt": answer_excerpt(answer.answer),
                    "verdict": None,
                    "reviewer": None,
                    "reviewed_at": None,
                    "notes": None,
                }
            )
        if not answer.answerable:
            rows.append(
                {
                    "row_id": abstention_row_id(answer.item_id),
                    "kind": ABSTENTION,
                    "item_id": answer.item_id,
                    "language": answer.language,
                    "answerable": answer.answerable,
                    "citation_index": None,
                    "chunk_id": None,
                    "segment_id": None,
                    "audio_id": None,
                    "transcript_version": None,
                    "quote": None,
                    "question": answer.question,
                    "answer": answer.answer,
                    "citations": len(answer.citations),
                    "answer_excerpt": None,
                    "verdict": None,
                    "reviewer": None,
                    "reviewed_at": None,
                    "notes": None,
                }
            )
    return rows


def write_review_sheet(path: Path, rows: Sequence[Mapping[str, Any]]) -> Path:
    """Write ``review.jsonl`` plus the spreadsheet copy ``review.csv``; return the CSV path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False) + "\n")
    csv_path = path.with_suffix(".csv")
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {field: "" if row.get(field) is None else row.get(field) for field in CSV_FIELDS}
            )
    return csv_path


def load_adjudication(path: Path) -> Adjudication:
    """Load review.jsonl or review.csv and validate every row."""
    if not path.is_file():
        raise AdjudicationError([f"no existe el fichero de adjudicación: {path}"])
    suffix = path.suffix.lower()
    if suffix == ".csv":
        records = _read_csv(path)
    elif suffix in {".jsonl", ".json"}:
        records = _read_jsonl(path)
    else:
        raise AdjudicationError(
            [f"formato de adjudicación no soportado: {suffix or '(sin extensión)'}"]
        )
    rows: dict[str, AdjudicationRow] = {}
    problems: list[str] = []
    for number, record in enumerate(records, start=1):
        try:
            row = AdjudicationRow.model_validate(record)
        except ValidationError as exc:
            problems.extend(
                f"fila {number}: {'.'.join(str(part) for part in error['loc']) or 'fila'}: "
                f"{error['msg']}"
                for error in exc.errors(include_input=False, include_url=False)
            )
            continue
        if row.row_id in rows:
            problems.append(f"fila {number}: row_id duplicado {row.row_id}")
        else:
            rows[row.row_id] = row
    if problems:
        raise AdjudicationError(problems)
    if not rows:
        raise AdjudicationError(["la adjudicación no tiene filas"])
    return Adjudication(rows=rows)


def validate_adjudication(adjudication: Adjudication, rows: Sequence[Mapping[str, Any]]) -> None:
    """Require one verdict for every expected row and no extra identifiers."""
    expected = {str(row["row_id"]): row for row in rows}
    missing = sorted(set(expected) - set(adjudication.rows))
    extra = sorted(set(adjudication.rows) - set(expected))
    problems: list[str] = []
    if missing:
        problems.append(f"faltan {len(missing)} veredictos: {', '.join(missing[:5])}")
    if extra:
        problems.append(f"hay {len(extra)} filas no esperadas: {', '.join(extra[:5])}")
    for row_id, row in adjudication.rows.items():
        expected_row = expected.get(row_id)
        if expected_row is None:
            continue
        if row.kind != expected_row["kind"]:
            problems.append(f"{row_id}: kind {row.kind!r} != {expected_row['kind']!r}")
    if problems:
        raise AdjudicationError(problems)


def reviews_from_adjudication(
    answers: Sequence[AnswerOutcome], adjudication: Adjudication
) -> tuple[tuple[CitationReview, ...], tuple[AbstentionReview, ...]]:
    """Project verdicts to the pure metric inputs, in answer order."""
    citations: list[CitationReview] = []
    abstentions: list[AbstentionReview] = []
    for answer in answers:
        occurrences: dict[int, int] = {}
        for citation in answer.citations:
            occurrence = occurrences.get(citation.index, 0)
            occurrences[citation.index] = occurrence + 1
            row = adjudication.rows[citation_row_id(answer.item_id, citation.index, occurrence)]
            citations.append(
                CitationReview(
                    item_id=answer.item_id,
                    language=answer.language,
                    correct=row.verdict == "correct",
                )
            )
        if not answer.answerable:
            row = adjudication.rows[abstention_row_id(answer.item_id)]
            abstentions.append(
                AbstentionReview(
                    item_id=answer.item_id,
                    language=answer.language,
                    abstained=row.verdict == "abstained",
                    citations=len(answer.citations),
                )
            )
    return tuple(citations), tuple(abstentions)


def write_publication(
    publish_dir: Path,
    *,
    run: Mapping[str, Any],
    dataset_version: str,
    dataset_hash: str,
    metrics: Sequence[LanguageMetrics],
    verdict: OverallVerdict,
    adjudication: Adjudication,
    review_rows: Sequence[Mapping[str, Any]],
) -> None:
    """Write the redacted publication: report, metrics and adjudication without text."""
    publish_dir.mkdir(parents=True, exist_ok=True)
    payload = metrics_to_dict(metrics)
    payload.update(
        {
            "gate": "G4",
            "status": "PASS" if verdict.passed else "FAIL",
            "verdict_reasons": list(verdict.reasons),
            "dataset_version": dataset_version,
            "dataset_hash": f"sha256:{dataset_hash}",
            "partition": "test",
            "run_id": run.get("run_id"),
            "created_at": run.get("created_at"),
            "scored_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "git_commit": run.get("git_commit"),
            "environment": run.get("environment", {}),
            "embedding": run.get("embedding", {}),
            "llm": run.get("llm", {}),
            "prompt_version": run.get("prompt_version"),
            "reviewers": list(adjudication.reviewers),
            "limitations": limitations(),
        }
    )
    (publish_dir / "metrics.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    by_row = {str(row["row_id"]): row for row in review_rows}
    redacted_path = publish_dir / "adjudication.redacted.jsonl"
    with redacted_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row_id in sorted(adjudication.rows):
            source = by_row.get(row_id)
            row = adjudication.rows[row_id]
            handle.write(
                json.dumps(
                    {
                        "row_id": row_id,
                        "item_id": None if source is None else source.get("item_id"),
                        "language": None if source is None else source.get("language"),
                        "kind": row.kind,
                        "verdict": row.verdict,
                        "reviewer": row.reviewer,
                        "reviewed_at": row.reviewed_at.isoformat(),
                        "notes": row.notes,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    (publish_dir / "REPORT.md").write_text(
        render_report(
            run=run,
            dataset_version=dataset_version,
            dataset_hash=dataset_hash,
            metrics=metrics,
            verdict=verdict,
            reviewers=adjudication.reviewers,
        ),
        encoding="utf-8",
    )


def render_report(
    *,
    run: Mapping[str, Any],
    dataset_version: str,
    dataset_hash: str,
    metrics: Sequence[LanguageMetrics],
    verdict: OverallVerdict,
    reviewers: Sequence[str],
) -> str:
    """Markdown report with aggregates only; no question, answer or quote text."""
    status = "PASS" if verdict.passed else "FAIL"
    embedding = run.get("embedding", {})
    llm = run.get("llm", {})
    environment = run.get("environment", {})
    lines = [
        f"# G4 evaluation report — {run.get('run_id', '(sin run id)')}",
        "",
        f"**Gate status:** **{status}**",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Dataset | `{dataset_version}` (`sha256:{dataset_hash}`) |",
        "| Partition | `test` |",
        f"| Run id | `{run.get('run_id', '')}` |",
        f"| Created at | `{run.get('created_at', '')}` |",
        f"| Git commit | `{run.get('git_commit') or 'unknown'}` |",
        f"| Embedding model | `{embedding.get('model', 'unknown')}` "
        f"(dimension {embedding.get('dimension', '?')}) |",
        f"| LLM model | `{llm.get('model', 'unknown')}` |",
        f"| Prompt version | `{run.get('prompt_version', 'unknown')}` |",
        f"| Environment | `{environment.get('python', '?')}` / "
        f"`{environment.get('platform', '?')}` |",
        f"| Reviewer(s) | {', '.join(reviewers) if reviewers else '(none)'} |",
        "",
        "## Verdict",
        "",
        "| Language | Recall@8 | Citation precision | Abstention | Unauthorized | Pass |",
        "|---|---|---|---|---|---|",
    ]
    for item in metrics:
        lines.append(
            "| {language} | {recall} | {precision} | {abstention} | {unauthorized} | "
            "{passed} |".format(
                language=item.language,
                recall=_fraction(item.recall_at_8.numerator, item.recall_at_8.denominator),
                precision=_fraction(
                    item.citation_precision.numerator, item.citation_precision.denominator
                ),
                abstention=_fraction(item.abstention.numerator, item.abstention.denominator),
                unauthorized=item.unauthorized,
                passed="yes" if _language_passes(item) else "no",
            )
        )
    if verdict.reasons:
        lines.extend(["", "**Failed conditions:**", ""])
        lines.extend(f"- {reason}" for reason in verdict.reasons)
    lines.extend(
        [
            "",
            "## Per-language detail",
            "",
            "| Language | Answerables | Recall@8 (n/N) | Recall 95% CI | Span recall | "
            "Citations | Precision (n/N) | Precision 95% CI | Unanswerables | "
            "Abstained (n/N) | Abstention 95% CI | Unauthorized | "
            "Answerables without citation | Retrieval p50/p95 (ms) |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for item in metrics:
        lines.append(
            "| {language} | {answerable} | {recall} | {recall_ci} | {span} | {citations} | "
            "{precision} | {precision_ci} | {unanswerable} | {abstention} | {abstention_ci} | "
            "{unauthorized} | {without} | {p50} / {p95} |".format(
                language=item.language,
                answerable=item.answerable,
                recall=_fraction(item.recall_at_8.numerator, item.recall_at_8.denominator),
                recall_ci=_ci(item.recall_ci),
                span=_fraction(item.span_recall.numerator, item.span_recall.denominator),
                citations=item.citations,
                precision=_fraction(
                    item.citation_precision.numerator, item.citation_precision.denominator
                ),
                precision_ci=_ci(item.citation_ci),
                unanswerable=item.unanswerable,
                abstention=_fraction(item.abstention.numerator, item.abstention.denominator),
                abstention_ci=_ci(item.abstention_ci),
                unauthorized=item.unauthorized,
                without=item.answerable_abstained,
                p50=_ms(item.p50_ms),
                p95=_ms(item.p95_ms),
            )
        )
    lines.extend(
        [
            "",
            "## Formulas and thresholds",
            "",
            "- `Recall@8 = |{answerable questions with >=1 expected evidence in the top 8}| / "
            "|answerable questions|`; pass `>= 0.85` per language.",
            "- `citation_precision = |{reviewed citations marked correct}| / "
            "|reviewed citations|`; pass `>= 0.95` per language and at least one citation.",
            "- `abstention = |{unanswerable items marked abstained with zero citations}| / "
            "|unanswerable items|`; pass `>= 0.90` per language.",
            "- Any unauthorized citation ID is a hard failure. Every language must pass on its "
            "own; a macro average never compensates a failed language.",
            "- Thresholds use integer arithmetic (`numerator * 100 >= threshold * denominator`). "
            "Wilson 95% intervals are diagnostic only.",
            "",
            "## Limitations",
            "",
        ]
    )
    lines.extend(f"- {limitation}" for limitation in limitations())
    lines.extend(
        [
            "",
            "## Reproduction",
            "",
            "```bash",
            "cd apps/api",
            f"uv run python -m tools.eval.cli score --run-id {run.get('run_id', '<run-id>')} "
            "--adjudication var/eval/g4/<run-id>/review.jsonl "
            "--publish docs/eval/g4/<run-id>",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def limitations() -> tuple[str, ...]:
    return (
        "Synthetic text corpus: no real classes, audio or personally identifiable data.",
        "Small sample (N=35 answerable and N=15 unanswerable per language); the Wilson 95% "
        "intervals are wide and the gate uses the point estimate.",
        "Citation precision and abstention are single-reviewer human judgements; "
        "inter-reviewer agreement is not measured.",
        "Provider outputs are nondeterministic; a later run may produce different numbers.",
        "French uses PostgreSQL `simple` FTS (no stemming); this may lower Recall@8 for French.",
        "`ABSTENTION_TEXT` is Spanish-only in the current product; refusals in English and "
        "French are counted from human labels, not from that constant.",
        "This report is not a G4 closure unless it comes from an authorized real-provider run "
        "with a completed human review.",
    )


def _language_passes(item: LanguageMetrics) -> bool:
    # The report is only rendered after `language_verdict`; keep the column derived from the
    # same exact rules without importing the private helper.
    return (
        item.answerable > 0
        and item.unanswerable > 0
        and item.recall_at_8.passes(85)
        and item.citations > 0
        and item.citation_precision.passes(95)
        and item.abstention.passes(90)
        and item.unauthorized == 0
    )


def _fraction(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return f"{numerator}/0 (n/a)"
    return f"{numerator}/{denominator} ({numerator / denominator:.1%})"


def _ci(interval: tuple[float, float] | None) -> str:
    if interval is None:
        return "n/a"
    return f"[{interval[0]:.3f}, {interval[1]:.3f}]"


def _ms(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    problems: list[str] = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                problems.append(f"fila {number}: JSON inválido ({exc.msg})")
                continue
            if not isinstance(payload, dict):
                problems.append(f"fila {number}: se esperaba un objeto JSON")
                continue
            records.append(payload)
    if problems:
        raise AdjudicationError(problems)
    return records


def _read_csv(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise AdjudicationError([f"{path.name}: falta la cabecera"])
            for row in reader:
                record: dict[str, Any] = {}
                for key, value in row.items():
                    if key is None:
                        continue
                    if value is None or value == "":
                        record[key] = None
                    else:
                        record[key] = value
                records.append(record)
    except OSError as exc:
        raise AdjudicationError([f"{path.name}: {exc}"]) from exc
    return records

"""Pure L2.8 latency statistics, thresholds and redacted report rendering.

No database, network or filesystem access lives here. The runner feeds measured samples in;
this module validates them, computes the nearest-rank p50/p95, applies the per-language gate
and renders the aggregates that are safe to publish. Question, answer, quote and transcript
text never enter these types.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from tools.eval.metrics import percentile

LATENCY_GATE = "L2.8"
RETRIEVAL_P95_TARGET_MS = 500.0
FIRST_TOKEN_P95_TARGET_MS = 5000.0
DEFAULT_CONCURRENCY = 1

FirstTokenSource = Literal["llm", "abstention"]


def _validated_ms(value: float, *, what: str) -> float:
    """Reject negative, NaN and infinite timings instead of silently skewing a p95."""
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{what} debe ser un número finito no negativo")
    return number


@dataclass(frozen=True)
class RetrievalSample:
    """One timed retrieval call for one frozen dataset item."""

    item_id: str
    language: str
    took_ms: float

    def __post_init__(self) -> None:
        if not self.item_id or not self.language:
            raise ValueError("item_id y language son obligatorios")
        object.__setattr__(self, "took_ms", _validated_ms(self.took_ms, what="took_ms"))


@dataclass(frozen=True)
class FirstTokenSample:
    """One timed chat preparation up to the first non-empty delta."""

    item_id: str
    language: str
    first_token_ms: float
    source: FirstTokenSource

    def __post_init__(self) -> None:
        if not self.item_id or not self.language:
            raise ValueError("item_id y language son obligatorios")
        if self.source not in ("llm", "abstention"):
            raise ValueError("source debe ser 'llm' o 'abstention'")
        object.__setattr__(
            self,
            "first_token_ms",
            _validated_ms(self.first_token_ms, what="first_token_ms"),
        )


@dataclass(frozen=True)
class LanguageLatency:
    """All latency samples and the cold-start observation for one language."""

    language: str
    expected_items: int
    expected_item_ids: tuple[str, ...] = ()
    retrieval: tuple[RetrievalSample, ...] = ()
    first_token: tuple[FirstTokenSample, ...] = ()
    cold_retrieval_ms: float | None = None
    cold_first_token_ms: float | None = None
    retrieval_target_ms: float = RETRIEVAL_P95_TARGET_MS
    first_token_target_ms: float = FIRST_TOKEN_P95_TARGET_MS

    def __post_init__(self) -> None:
        if not self.language:
            raise ValueError("language es obligatorio")
        if self.expected_items < 0:
            raise ValueError("expected_items no puede ser negativo")
        if self.retrieval_target_ms <= 0 or self.first_token_target_ms <= 0:
            raise ValueError("los umbrales deben ser positivos")
        if self.expected_item_ids:
            if len(set(self.expected_item_ids)) != len(self.expected_item_ids):
                raise ValueError("expected_item_ids contiene ids duplicados")
            if self.expected_items != len(self.expected_item_ids):
                raise ValueError("expected_items no coincide con expected_item_ids")
        for retrieval_sample in self.retrieval:
            if retrieval_sample.language != self.language:
                raise ValueError("una muestra de recuperación pertenece a otro idioma")
        for first_token_sample in self.first_token:
            if first_token_sample.language != self.language:
                raise ValueError("una muestra de primer token pertenece a otro idioma")
        if self.cold_retrieval_ms is not None:
            object.__setattr__(
                self,
                "cold_retrieval_ms",
                _validated_ms(self.cold_retrieval_ms, what="cold_retrieval_ms"),
            )
        if self.cold_first_token_ms is not None:
            object.__setattr__(
                self,
                "cold_first_token_ms",
                _validated_ms(self.cold_first_token_ms, what="cold_first_token_ms"),
            )
        self._validate_sample_ids("recuperación", [sample.item_id for sample in self.retrieval])
        self._validate_sample_ids(
            "primer token", [sample.item_id for sample in self.first_token]
        )

    def _validate_sample_ids(self, what: str, item_ids: Sequence[str]) -> None:
        """A duplicate or an out-of-corpus id cannot count as a complete sample set."""
        if len(set(item_ids)) != len(item_ids):
            raise ValueError(f"{what} contiene item_id duplicados")
        if not self.expected_item_ids:
            return
        expected = set(self.expected_item_ids)
        actual = set(item_ids)
        if actual != expected:
            missing = len(expected - actual)
            extra = len(actual - expected)
            raise ValueError(
                f"{what} no cubre exactamente los ítems esperados "
                f"(faltan {missing}, sobran {extra})"
            )

    @property
    def retrieval_values(self) -> tuple[float, ...]:
        return tuple(sample.took_ms for sample in self.retrieval)

    @property
    def first_token_values(self) -> tuple[float, ...]:
        return tuple(sample.first_token_ms for sample in self.first_token)

    @property
    def llm_first_token_values(self) -> tuple[float, ...]:
        return tuple(
            sample.first_token_ms for sample in self.first_token if sample.source == "llm"
        )

    @property
    def retrieval_p50_ms(self) -> float | None:
        return percentile(self.retrieval_values, 50)

    @property
    def retrieval_p95_ms(self) -> float | None:
        return percentile(self.retrieval_values, 95)

    @property
    def retrieval_max_ms(self) -> float | None:
        return max(self.retrieval_values) if self.retrieval_values else None

    @property
    def first_token_p50_ms(self) -> float | None:
        return percentile(self.first_token_values, 50)

    @property
    def first_token_p95_ms(self) -> float | None:
        return percentile(self.first_token_values, 95)

    @property
    def first_token_max_ms(self) -> float | None:
        return max(self.first_token_values) if self.first_token_values else None

    @property
    def first_token_llm_p95_ms(self) -> float | None:
        """Diagnostic for LLM-backed responses; never replaces the user-facing gate."""
        return percentile(self.llm_first_token_values, 95)

    @property
    def retrieval_passed(self) -> bool:
        value = self.retrieval_p95_ms
        return value is not None and value <= self.retrieval_target_ms

    @property
    def first_token_passed(self) -> bool:
        value = self.first_token_p95_ms
        return value is not None and value <= self.first_token_target_ms

    @property
    def evaluated(self) -> bool:
        return (
            self.expected_items > 0
            and len(self.retrieval) == self.expected_items
            and len(self.first_token) == self.expected_items
        )

    @property
    def passed(self) -> bool:
        return self.evaluated and self.retrieval_passed and self.first_token_passed

    @property
    def reasons(self) -> tuple[str, ...]:
        reasons: list[str] = []
        if self.expected_items <= 0:
            reasons.append("sin ítems esperados")
        if not self.retrieval:
            reasons.append("sin muestras de recuperación")
        elif len(self.retrieval) != self.expected_items:
            reasons.append(
                f"recuperación incompleta ({len(self.retrieval)}/{self.expected_items})"
            )
        if not self.first_token:
            reasons.append("sin muestras de primer token")
        elif len(self.first_token) != self.expected_items:
            reasons.append(
                f"primer token incompleto ({len(self.first_token)}/{self.expected_items})"
            )
        retrieval_p95 = self.retrieval_p95_ms
        if retrieval_p95 is not None and retrieval_p95 > self.retrieval_target_ms:
            reasons.append(
                f"p95 de recuperación {retrieval_p95:.2f} ms > "
                f"{self.retrieval_target_ms:.2f} ms"
            )
        first_token_p95 = self.first_token_p95_ms
        if first_token_p95 is not None and first_token_p95 > self.first_token_target_ms:
            reasons.append(
                f"p95 de primer token {first_token_p95:.2f} ms > "
                f"{self.first_token_target_ms:.2f} ms"
            )
        return tuple(reasons)


@dataclass(frozen=True)
class LatencyVerdict:
    """Overall gate decision; a failed language is never compensated by an average."""

    passed: bool
    evaluated: bool
    reasons: tuple[str, ...]


def overall_verdict(languages: Sequence[LanguageLatency]) -> LatencyVerdict:
    """Every enabled language must be complete and pass both p95 targets."""
    if not languages:
        return LatencyVerdict(
            passed=False,
            evaluated=False,
            reasons=("sin idiomas evaluados",),
        )
    reasons: list[str] = []
    for language in languages:
        if not language.passed:
            reasons.append(f"{language.language}: " + "; ".join(language.reasons))
    evaluated = all(language.evaluated for language in languages)
    return LatencyVerdict(
        passed=evaluated and not reasons,
        evaluated=evaluated,
        reasons=tuple(reasons),
    )


@dataclass(frozen=True)
class LatencyMetadata:
    """Publication metadata; deliberately excludes dataset text and tenant secrets."""

    measured_at: str
    run_id: str
    dataset_version: str
    dataset_hash: str
    embedding: Mapping[str, Any]
    llm: Mapping[str, Any]
    prompt_version: str
    environment: Mapping[str, Any] = field(default_factory=dict)
    git_commit: str | None = None
    git_dirty: bool | None = None
    partition: str = "test"
    concurrency: int = DEFAULT_CONCURRENCY
    language_metadata: Mapping[str, Mapping[str, int]] = field(default_factory=dict)
    cold_start: str = (
        "One warm-up retrieval and one warm-up chat per language, reported separately and "
        "excluded from the p95 distributions unless no development item exists, in which "
        "case the first test item remains in the distribution."
    )
    limitations: tuple[str, ...] = ()


def _sample_size_limitation(sample_sizes: Mapping[str, int] | None) -> str:
    if not sample_sizes:
        detail = "N is reported per language"
    else:
        sizes = {language: int(count) for language, count in sample_sizes.items()}
        distinct = sorted(set(sizes.values()))
        if len(distinct) == 1:
            detail = f"N={distinct[0]} per language"
        else:
            rendered = ", ".join(
                f"{language}={count}" for language, count in sorted(sizes.items())
            )
            detail = f"N varies by language ({rendered})"
    return (
        f"The p95 uses nearest-rank over the frozen test partition ({detail}); the result "
        "is measured, not an SLA or a provider guarantee."
    )


def default_limitations(
    *, sample_sizes: Mapping[str, int] | None = None
) -> tuple[str, ...]:
    """Limitations that must accompany every published latency measurement.

    ``sample_sizes`` keeps the report honest for smoke runs and partial language sets: the
    text must never claim the official N=50 when the measured sample is smaller.
    """
    return (
        "In-process measurement: it excludes HTTP, Caddy, authentication, network and "
        "provider quota enforcement.",
        "Concurrency is 1; this is a reference measurement, not a load test.",
        "Cold start is one warm-up item per language, reported separately; it is not a "
        "process-restart benchmark. When no development item exists, the first test item "
        "serves as cold start and remains in the p95 distribution.",
        "Chat first-token timing stops at the first non-empty delta; closing the stream early "
        "persists an isolated cancelled partial for that item's conversation.",
        _sample_size_limitation(sample_sizes),
        "Synthetic text corpus only: no real classes, audio or personally identifiable data.",
        "Provider nondeterminism can change a later run.",
        "Every enabled language must pass on its own; a macro average never compensates a "
        "failed language.",
    )


def _status(verdict: LatencyVerdict) -> str:
    if not verdict.evaluated:
        return "NOT_EVALUATED"
    return "PASS" if verdict.passed else "FAIL"


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 3)


def _language_payload(
    language: LanguageLatency,
    metadata: LatencyMetadata,
) -> dict[str, Any]:
    counts = metadata.language_metadata.get(language.language, {})
    return {
        "expected_items": language.expected_items,
        "classes": int(counts.get("classes", 0)),
        "chunks": int(counts.get("chunks", 0)),
        "retrieval": {
            "n": len(language.retrieval),
            "p50_ms": _round(language.retrieval_p50_ms),
            "p95_ms": _round(language.retrieval_p95_ms),
            "max_ms": _round(language.retrieval_max_ms),
            "target_ms": language.retrieval_target_ms,
            "passed": language.retrieval_passed,
        },
        "first_token": {
            "n": len(language.first_token),
            "llm_n": len(language.llm_first_token_values),
            "abstention_n": len(language.first_token) - len(language.llm_first_token_values),
            "p50_ms": _round(language.first_token_p50_ms),
            "p95_ms": _round(language.first_token_p95_ms),
            "max_ms": _round(language.first_token_max_ms),
            "target_ms": language.first_token_target_ms,
            "passed": language.first_token_passed,
        },
        "first_token_llm_only_p95_ms": _round(language.first_token_llm_p95_ms),
        "cold_start": {
            "retrieval_ms": _round(language.cold_retrieval_ms),
            "first_token_ms": _round(language.cold_first_token_ms),
        },
    }


def _consistent_thresholds(
    languages: Sequence[LanguageLatency],
) -> tuple[float, float]:
    """The global payload cannot silently publish the first language's targets."""
    if not languages:
        return RETRIEVAL_P95_TARGET_MS, FIRST_TOKEN_P95_TARGET_MS
    retrieval = {language.retrieval_target_ms for language in languages}
    first_token = {language.first_token_target_ms for language in languages}
    if len(retrieval) != 1 or len(first_token) != 1:
        raise ValueError("todos los idiomas deben usar los mismos umbrales de latencia")
    return retrieval.pop(), first_token.pop()


def build_payload(
    metadata: LatencyMetadata,
    languages: Sequence[LanguageLatency],
    verdict: LatencyVerdict,
) -> dict[str, Any]:
    """JSON-safe, redacted publication payload."""
    retrieval_target, first_token_target = _consistent_thresholds(languages)
    return {
        "gate": LATENCY_GATE,
        "status": _status(verdict),
        "measured_at": metadata.measured_at,
        "run_id": metadata.run_id,
        "git_commit": metadata.git_commit,
        "git_dirty": metadata.git_dirty,
        "environment": dict(metadata.environment),
        "concurrency": metadata.concurrency,
        "dataset_version": metadata.dataset_version,
        "dataset_hash": f"sha256:{metadata.dataset_hash}",
        "partition": metadata.partition,
        "embedding": dict(metadata.embedding),
        "llm": dict(metadata.llm),
        "prompt_version": metadata.prompt_version,
        "thresholds": {
            "retrieval_p95_ms": retrieval_target,
            "first_token_p95_ms": first_token_target,
        },
        "languages": {
            language.language: _language_payload(language, metadata) for language in languages
        },
        "verdict_reasons": list(verdict.reasons),
        "cold_start": metadata.cold_start,
        "limitations": list(metadata.limitations or default_limitations()),
    }


def _format_ms(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "n/a"


def _yes_no(value: Any) -> str:
    return "yes" if value is True else "no"


def render_report(payload: Mapping[str, Any]) -> str:
    """Markdown report built from the same redacted payload written to JSON."""
    status = str(payload.get("status", "NOT_EVALUATED"))
    languages = payload.get("languages", {})
    lines = [
        f"# L2.8 latency report — {payload.get('run_id', '(sin run id)')}",
        "",
        f"**Gate status:** **{status}**",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Measured at | `{payload.get('measured_at', '')}` |",
        f"| Dataset | `{payload.get('dataset_version', '')}` "
        f"(`{payload.get('dataset_hash', '')}`) |",
        f"| Partition | `{payload.get('partition', '')}` |",
        f"| Git commit | `{payload.get('git_commit') or 'unknown'}` |",
        f"| Git dirty | `{payload.get('git_dirty')}` |",
        f"| Concurrency | {payload.get('concurrency', 1)} |",
        f"| Embedding | `{_mapping_value(payload.get('embedding'), 'provider')}` / "
        f"`{_mapping_value(payload.get('embedding'), 'model')}` "
        f"(dimension {_mapping_value(payload.get('embedding'), 'dimension')}) |",
        f"| LLM | `{_mapping_value(payload.get('llm'), 'provider')}` / "
        f"`{_mapping_value(payload.get('llm'), 'model')}` |",
        f"| Prompt version | `{payload.get('prompt_version', '')}` |",
        f"| Environment | `{_mapping_value(payload.get('environment'), 'python')}` / "
        f"`{_mapping_value(payload.get('environment'), 'platform')}` |",
        "",
        "## Verdict",
        "",
        "| Language | Retrieval N | Retrieval p50/p95/max (ms) | Retrieval pass | "
        "First-token N | First-token p50/p95/max (ms) | First-token pass | "
        "LLM-only p95 (ms) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for language, item in languages.items():
        retrieval = item.get("retrieval", {})
        first_token = item.get("first_token", {})
        lines.append(
            f"| {language} | {retrieval.get('n', 0)} | "
            f"{_format_ms(retrieval.get('p50_ms'))} / {_format_ms(retrieval.get('p95_ms'))} / "
            f"{_format_ms(retrieval.get('max_ms'))} | {_yes_no(retrieval.get('passed'))} | "
            f"{first_token.get('n', 0)} | "
            f"{_format_ms(first_token.get('p50_ms'))} / "
            f"{_format_ms(first_token.get('p95_ms'))} / "
            f"{_format_ms(first_token.get('max_ms'))} | "
            f"{_yes_no(first_token.get('passed'))} | "
            f"{_format_ms(item.get('first_token_llm_only_p95_ms'))} |"
        )
    reasons = payload.get("verdict_reasons", [])
    if reasons:
        lines.extend(["", "**Failed conditions:**", ""])
        lines.extend(f"- {reason}" for reason in reasons)

    lines.extend(
        [
            "",
            "## Cold start (excluded from p95)",
            "",
            "| Language | Retrieval (ms) | First token (ms) |",
            "|---|---|---|",
        ]
    )
    for language, item in languages.items():
        cold = item.get("cold_start", {})
        lines.append(
            f"| {language} | {_format_ms(cold.get('retrieval_ms'))} | "
            f"{_format_ms(cold.get('first_token_ms'))} |"
        )

    lines.extend(
        [
            "",
            "## Method",
            "",
            "- Retrieval p95 times `SearchService.retrieve(top_k=8)` in-process, one fresh "
            "database session per item: session checkout, query embedding, both branches, "
            "RRF fusion and hydration.",
            "- Chat first-token p95 times `RAGService.prepare` plus the SSE stream up to the "
            "first non-empty `delta`. It includes embedding, retrieval, prompt assembly, the "
            "turn persistence and the provider's first delta.",
            "- p95 is nearest-rank over the frozen `test` partition; no interpolation and no "
            "rounding before the comparison.",
            "- The gate passes only when every enabled language is complete and both p95 "
            "values are `<=` their target. The LLM-only p95 is a diagnostic, not the gate.",
            "",
            "## Limitations",
            "",
        ]
    )
    limitations = payload.get("limitations", [])
    lines.extend(f"- {limitation}" for limitation in limitations)
    lines.extend(
        [
            "",
            "## Reproduction",
            "",
            "```bash",
            "cd apps/api",
            "uv run python -m tools.eval.cli prepare \\",
            "  --dataset tests/fixtures/g4/datasets/v1 --languages es,en,fr --reset",
            "cd ../..",
            "python scripts/load/latency_rag.py \\",
            f"  --run-id {payload.get('run_id', '<run-id>')} --languages es,en,fr \\",
            f"  --publish docs/eval/latency/{payload.get('run_id', '<run-id>')}",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def _mapping_value(mapping: Any, key: str) -> Any:
    if isinstance(mapping, Mapping):
        return mapping.get(key, "unknown")
    return "unknown"


__all__ = [
    "DEFAULT_CONCURRENCY",
    "FIRST_TOKEN_P95_TARGET_MS",
    "LATENCY_GATE",
    "RETRIEVAL_P95_TARGET_MS",
    "FirstTokenSample",
    "LanguageLatency",
    "LatencyMetadata",
    "LatencyVerdict",
    "RetrievalSample",
    "build_payload",
    "default_limitations",
    "overall_verdict",
    "render_report",
]

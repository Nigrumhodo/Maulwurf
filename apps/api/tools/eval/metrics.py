"""Pure G4 metrics and thresholds (L2.7).

No database, network or filesystem access lives here: the same functions are used by the
CLI and by unit tests that pin the exact 0.85/0.95/0.90 boundaries. Threshold comparisons
use integers (``hits * 100 >= percent * total``) so a value such as 30/35 cannot fail
because of floating-point representation.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

RECALL_THRESHOLD = 85
CITATION_PRECISION_THRESHOLD = 95
ABSTENTION_THRESHOLD = 90


@dataclass(frozen=True)
class Fraction:
    """A metric with its numerator and denominator, never a bare percentage."""

    numerator: int
    denominator: int

    @property
    def value(self) -> float | None:
        """Metric value, or ``None`` when the denominator is zero."""
        if self.denominator == 0:
            return None
        return self.numerator / self.denominator

    def passes(self, threshold_percent: int) -> bool:
        """Exact integer comparison against a two-decimal threshold."""
        return self.denominator > 0 and self.numerator * 100 >= threshold_percent * self.denominator

    def as_dict(self) -> dict[str, int | float | None]:
        """JSON-safe representation with a rounded value."""
        value = self.value
        return {
            "numerator": self.numerator,
            "denominator": self.denominator,
            "value": None if value is None else round(value, 4),
        }


@dataclass(frozen=True)
class RetrievalResult:
    """One question's retrieval outcome; ``matched`` counts expected spans found."""

    item_id: str
    language: str
    answerable: bool
    expected: int
    matched: int
    took_ms: float


@dataclass(frozen=True)
class CitationReview:
    """One reviewed citation verdict."""

    item_id: str
    language: str
    correct: bool


@dataclass(frozen=True)
class AbstentionReview:
    """One reviewed unanswerable item."""

    item_id: str
    language: str
    abstained: bool
    citations: int


@dataclass(frozen=True)
class LanguageMetrics:
    """All reported metrics for one language."""

    language: str
    answerable: int
    recall_at_8: Fraction
    span_recall: Fraction
    citations: int
    citation_precision: Fraction
    unanswerable: int
    abstention: Fraction
    unauthorized: int
    answerable_abstained: int
    citation_coverage: Fraction
    p50_ms: float | None
    p95_ms: float | None
    recall_ci: tuple[float, float] | None
    citation_ci: tuple[float, float] | None
    abstention_ci: tuple[float, float] | None


@dataclass(frozen=True)
class LanguageVerdict:
    """Pass/fail decision for one language, with every failed condition listed."""

    language: str
    passed: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class OverallVerdict:
    """Pass/fail decision across all enabled languages."""

    passed: bool
    reasons: tuple[str, ...]


def wilson_interval(
    numerator: int, denominator: int, *, z: float = 1.96
) -> tuple[float, float] | None:
    """Wilson score interval for a proportion; ``None`` when undefined."""
    if denominator <= 0 or numerator < 0 or numerator > denominator:
        return None
    proportion = numerator / denominator
    squared = z * z
    divisor = 1.0 + squared / denominator
    center = (proportion + squared / (2.0 * denominator)) / divisor
    margin = (
        z
        * math.sqrt((proportion * (1.0 - proportion) + squared / (4.0 * denominator)) / denominator)
        / divisor
    )
    return (max(0.0, center - margin), min(1.0, center + margin))


def percentile(values: Sequence[float], percent: float) -> float | None:
    """Nearest-rank percentile; ``None`` for an empty sample."""
    if not values:
        return None
    ordered = sorted(values)
    if percent <= 0:
        return ordered[0]
    if percent >= 100:
        return ordered[-1]
    rank = math.ceil(percent / 100.0 * len(ordered))
    return ordered[max(0, rank - 1)]


def compute_language_metrics(
    language: str,
    retrieval: Sequence[RetrievalResult],
    citations: Sequence[CitationReview],
    abstentions: Sequence[AbstentionReview],
    *,
    unauthorized: int = 0,
    answerable_abstained: int = 0,
) -> LanguageMetrics:
    """Compute all G4 metrics for one language from reviewed outcomes."""
    answerable = [result for result in retrieval if result.answerable]
    unanswerable = [result for result in retrieval if not result.answerable]
    recall = Fraction(
        sum(1 for result in answerable if result.matched > 0),
        len(answerable),
    )
    span_recall = Fraction(
        sum(result.matched for result in retrieval),
        sum(result.expected for result in retrieval),
    )
    citation_precision = Fraction(
        sum(1 for review in citations if review.correct),
        len(citations),
    )
    abstention = Fraction(
        sum(1 for review in abstentions if review.abstained and review.citations == 0),
        len(abstentions),
    )
    cited_items = {review.item_id for review in citations}
    citation_coverage = Fraction(
        sum(1 for result in answerable if result.item_id in cited_items),
        len(answerable),
    )
    timings = [result.took_ms for result in retrieval]
    return LanguageMetrics(
        language=language,
        answerable=len(answerable),
        recall_at_8=recall,
        span_recall=span_recall,
        citations=len(citations),
        citation_precision=citation_precision,
        unanswerable=len(unanswerable),
        abstention=abstention,
        unauthorized=unauthorized,
        answerable_abstained=answerable_abstained,
        citation_coverage=citation_coverage,
        p50_ms=percentile(timings, 50),
        p95_ms=percentile(timings, 95),
        recall_ci=wilson_interval(recall.numerator, recall.denominator),
        citation_ci=wilson_interval(citation_precision.numerator, citation_precision.denominator),
        abstention_ci=wilson_interval(abstention.numerator, abstention.denominator),
    )


def language_verdict(
    metrics: LanguageMetrics,
    *,
    recall_threshold: int = RECALL_THRESHOLD,
    citation_threshold: int = CITATION_PRECISION_THRESHOLD,
    abstention_threshold: int = ABSTENTION_THRESHOLD,
) -> LanguageVerdict:
    """Apply every G4 condition to one language; no global average is involved."""
    reasons: list[str] = []
    if metrics.answerable == 0:
        reasons.append("sin preguntas respondibles")
    if metrics.unanswerable == 0:
        reasons.append("sin preguntas no respondibles")
    if not metrics.recall_at_8.passes(recall_threshold):
        reasons.append(
            f"Recall@8 {metrics.recall_at_8.numerator}/{metrics.recall_at_8.denominator}"
            f" < {recall_threshold / 100:.2f}"
        )
    if metrics.citations == 0:
        reasons.append("sin citas revisadas (precisión indefinida)")
    elif not metrics.citation_precision.passes(citation_threshold):
        reasons.append(
            f"precisión de citas {metrics.citation_precision.numerator}/"
            f"{metrics.citation_precision.denominator} < {citation_threshold / 100:.2f}"
        )
    if not metrics.abstention.passes(abstention_threshold):
        reasons.append(
            f"abstención {metrics.abstention.numerator}/{metrics.abstention.denominator}"
            f" < {abstention_threshold / 100:.2f}"
        )
    if metrics.unauthorized > 0:
        reasons.append(f"{metrics.unauthorized} cita(s) no autorizada(s)")
    return LanguageVerdict(language=metrics.language, passed=not reasons, reasons=tuple(reasons))


def overall_verdict(metrics: Sequence[LanguageMetrics]) -> OverallVerdict:
    """Every enabled language must pass; the macro average is never a substitute."""
    if not metrics:
        return OverallVerdict(passed=False, reasons=("sin idiomas evaluados",))
    reasons: list[str] = []
    for language in metrics:
        verdict = language_verdict(language)
        if not verdict.passed:
            reasons.append(f"{language.language}: " + "; ".join(verdict.reasons))
    return OverallVerdict(passed=not reasons, reasons=tuple(reasons))


def metrics_to_dict(metrics: Sequence[LanguageMetrics]) -> dict[str, Any]:
    """JSON-safe report payload with per-language values and confidence intervals."""
    return {
        "languages": {
            item.language: {
                "answerable": item.answerable,
                "recall_at_8": item.recall_at_8.as_dict(),
                "recall_at_8_ci95": _ci(item.recall_ci),
                "span_recall": item.span_recall.as_dict(),
                "citations": item.citations,
                "citation_precision": item.citation_precision.as_dict(),
                "citation_precision_ci95": _ci(item.citation_ci),
                "unanswerable": item.unanswerable,
                "abstention": item.abstention.as_dict(),
                "abstention_ci95": _ci(item.abstention_ci),
                "unauthorized_citations": item.unauthorized,
                "answerable_abstained": item.answerable_abstained,
                "citation_coverage": item.citation_coverage.as_dict(),
                "retrieval_p50_ms": _round(item.p50_ms),
                "retrieval_p95_ms": _round(item.p95_ms),
            }
            for item in metrics
        },
        "thresholds": {
            "recall_at_8": RECALL_THRESHOLD / 100,
            "citation_precision": CITATION_PRECISION_THRESHOLD / 100,
            "abstention": ABSTENTION_THRESHOLD / 100,
        },
    }


def _ci(interval: tuple[float, float] | None) -> list[float] | None:
    if interval is None:
        return None
    return [round(interval[0], 4), round(interval[1], 4)]


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 2)

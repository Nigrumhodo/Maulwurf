"""G4 metric formulas, exact thresholds and verdicts (L2.7).

The G4 gate is per language: Recall@8 on answerable questions, human-reviewed citation
precision, human-reviewed abstention and zero unauthorized citations. These tests pin the
pure metric layer (no database, provider or filesystem) so the boundaries cannot drift:
``30/35`` and ``14/15`` pass, ``29/35`` and ``13/15`` fail, and a failed language is never
compensated by the macro average.
"""

from __future__ import annotations

import pytest

from tools.eval.metrics import (
    ABSTENTION_THRESHOLD,
    CITATION_PRECISION_THRESHOLD,
    RECALL_THRESHOLD,
    AbstentionReview,
    CitationReview,
    Fraction,
    RetrievalResult,
    compute_language_metrics,
    language_verdict,
    metrics_to_dict,
    overall_verdict,
    percentile,
    wilson_interval,
)
from tools.eval.report import Adjudication, AdjudicationError, validate_adjudication


def _result(
    item_id: str,
    *,
    language: str = "es",
    answerable: bool = True,
    expected: int = 1,
    matched: int = 1,
    took_ms: float = 10.0,
) -> RetrievalResult:
    return RetrievalResult(
        item_id=item_id,
        language=language,
        answerable=answerable,
        expected=expected,
        matched=matched,
        took_ms=took_ms,
    )


def _citations(language: str, correct: int, incorrect: int = 0) -> list[CitationReview]:
    return [
        CitationReview(item_id=f"{language}-c{number}", language=language, correct=True)
        for number in range(correct)
    ] + [
        CitationReview(item_id=f"{language}-x{number}", language=language, correct=False)
        for number in range(incorrect)
    ]


def _abstentions(
    language: str, abstained: int, answered: int = 0, *, with_citations: int = 0
) -> list[AbstentionReview]:
    rows = [
        AbstentionReview(
            item_id=f"{language}-a{number}", language=language, abstained=True, citations=0
        )
        for number in range(abstained)
    ]
    rows.extend(
        AbstentionReview(
            item_id=f"{language}-b{number}", language=language, abstained=True, citations=1
        )
        for number in range(with_citations)
    )
    rows.extend(
        AbstentionReview(
            item_id=f"{language}-c{number}", language=language, abstained=False, citations=1
        )
        for number in range(answered)
    )
    return rows


def _language_results(
    language: str, *, matched_answerables: int, answerables: int = 35
) -> list[RetrievalResult]:
    return [
        _result(
            f"{language}-{number:03d}",
            language=language,
            matched=1 if number < matched_answerables else 0,
        )
        for number in range(answerables)
    ] + [
        _result(
            f"{language}-u{number:03d}",
            language=language,
            answerable=False,
            expected=0,
            matched=0,
        )
        for number in range(15)
    ]


def test_g4_recall_at_8_counts_question_when_any_expected_evidence_is_retrieved() -> None:
    """One matching expected span in the top 8 is enough to count the question."""
    retrieval = [
        _result("es-001", expected=2, matched=1),
        _result("es-002", expected=2, matched=0),
    ]
    metrics = compute_language_metrics("es", retrieval, _citations("es", 2), _abstentions("es", 1))

    assert metrics.recall_at_8.numerator == 1
    assert metrics.recall_at_8.denominator == 2
    assert metrics.recall_at_8.value == 0.5


def test_g4_recall_at_8_ignores_rank_nine_and_duplicate_chunks() -> None:
    """Only the eight retained chunks count; a repeated chunk cannot inflate the numerator."""
    # ``matched`` already reflects the top-8 window: a hit at rank 9 is a zero, and a
    # duplicate chunk matching twice is still one matching question.
    retrieval = [
        _result("es-001", expected=3, matched=1),
        _result("es-002", expected=1, matched=0),
    ]
    metrics = compute_language_metrics("es", retrieval, _citations("es", 2), _abstentions("es", 1))

    assert metrics.recall_at_8.numerator == 1
    assert metrics.recall_at_8.denominator == 2
    assert metrics.span_recall.numerator == 1
    assert metrics.span_recall.denominator == 4


def test_g4_span_recall_is_secondary_and_does_not_change_question_recall() -> None:
    """Question recall stays binary even when only one of two expected spans is retrieved."""
    one_span = compute_language_metrics(
        "es",
        [_result("es-001", expected=2, matched=1)],
        _citations("es", 1),
        _abstentions("es", 1),
    )
    two_spans = compute_language_metrics(
        "es",
        [_result("es-001", expected=2, matched=2)],
        _citations("es", 1),
        _abstentions("es", 1),
    )

    assert one_span.recall_at_8 == two_spans.recall_at_8 == Fraction(1, 1)
    assert one_span.span_recall == Fraction(1, 2)
    assert two_spans.span_recall == Fraction(2, 2)


def test_g4_citation_precision_counts_correct_over_all_reviewed_citations() -> None:
    """Every emitted citation is reviewed; precision is correct / reviewed."""
    metrics = compute_language_metrics(
        "es",
        [_result("es-001")],
        _citations("es", correct=19, incorrect=1),
        _abstentions("es", 1),
    )

    assert metrics.citations == 20
    assert metrics.citation_precision == Fraction(19, 20)
    assert metrics.citation_precision.passes(CITATION_PRECISION_THRESHOLD)


def test_g4_citation_precision_fails_when_no_citations_exist() -> None:
    """Zero citations make precision undefined, not a perfect score."""
    metrics = compute_language_metrics(
        "es",
        [_result("es-001")],
        [],
        _abstentions("es", 1),
    )
    verdict = language_verdict(metrics)

    assert metrics.citations == 0
    assert metrics.citation_precision.value is None
    assert not verdict.passed
    assert any("sin citas revisadas" in reason for reason in verdict.reasons)


def test_g4_abstention_requires_reviewed_refusal_and_no_citations() -> None:
    """A refusal with citations, or an answer, is not an abstention."""
    metrics = compute_language_metrics(
        "es",
        [
            _result("es-001", answerable=False, expected=0, matched=0),
            _result("es-002", answerable=False, expected=0, matched=0),
            _result("es-003", answerable=False, expected=0, matched=0),
        ],
        _citations("es", 2),
        _abstentions("es", abstained=1, answered=1, with_citations=1),
    )

    assert metrics.unanswerable == 3
    assert metrics.abstention == Fraction(1, 3)


def test_g4_thresholds_use_exact_integer_boundaries() -> None:
    """The documented boundaries pass or fail without floating-point drift."""
    assert Fraction(30, 35).passes(RECALL_THRESHOLD)
    assert not Fraction(29, 35).passes(RECALL_THRESHOLD)
    assert Fraction(14, 15).passes(ABSTENTION_THRESHOLD)
    assert not Fraction(13, 15).passes(ABSTENTION_THRESHOLD)
    assert Fraction(38, 40).passes(CITATION_PRECISION_THRESHOLD)
    assert not Fraction(37, 40).passes(CITATION_PRECISION_THRESHOLD)
    assert Fraction(19, 20).passes(CITATION_PRECISION_THRESHOLD)
    assert not Fraction(18, 20).passes(CITATION_PRECISION_THRESHOLD)


def test_g4_failed_language_cannot_be_compensated_by_macro_average() -> None:
    """A strong average across languages never rescues a language below its threshold."""
    es = compute_language_metrics(
        "es",
        _language_results("es", matched_answerables=29),
        _citations("es", 30),
        _abstentions("es", 15),
    )
    en = compute_language_metrics(
        "en",
        _language_results("en", matched_answerables=35),
        _citations("en", 30),
        _abstentions("en", 15),
    )
    fr = compute_language_metrics(
        "fr",
        _language_results("fr", matched_answerables=35),
        _citations("fr", 30),
        _abstentions("fr", 15),
    )

    macro_recall = sum(item.recall_at_8.value or 0.0 for item in (es, en, fr)) / 3
    verdict = overall_verdict([es, en, fr])

    assert macro_recall >= 0.85
    assert not verdict.passed
    assert any(reason.startswith("es:") for reason in verdict.reasons)
    assert language_verdict(en).passed
    assert language_verdict(fr).passed


def test_g4_unauthorized_citation_is_a_hard_failure() -> None:
    """One unauthorized citation fails the language even with every other metric perfect."""
    metrics = compute_language_metrics(
        "es",
        [_result(f"es-{number:03d}") for number in range(35)],
        _citations("es", 40),
        _abstentions("es", 15),
        unauthorized=1,
    )
    verdict = language_verdict(metrics)

    assert not verdict.passed
    assert any("no autorizada" in reason for reason in verdict.reasons)
    assert not overall_verdict([metrics]).passed


def test_g4_wilson_interval_is_reported_for_small_samples() -> None:
    """Diagnostic Wilson bounds are present in the JSON payload, never used as the gate."""
    metrics = compute_language_metrics(
        "es",
        [_result(f"es-{number:03d}", matched=1 if number < 30 else 0) for number in range(35)],
        _citations("es", 19, 1),
        _abstentions("es", 14, 1),
    )
    payload = metrics_to_dict([metrics])["languages"]["es"]

    assert metrics.recall_ci is not None
    assert metrics.citation_ci is not None
    assert metrics.abstention_ci is not None
    for interval in (metrics.recall_ci, metrics.citation_ci, metrics.abstention_ci):
        low, high = interval
        assert 0.0 <= low <= high <= 1.0
    assert payload["recall_at_8_ci95"] is not None
    assert payload["citation_precision_ci95"] is not None
    assert payload["abstention_ci95"] is not None
    assert wilson_interval(0, 0) is None
    assert percentile([], 95) is None


def test_g4_verdict_requires_complete_adjudication() -> None:
    """A verdict without one row per citation/unanswerable is rejected before scoring."""
    expected = [
        {"row_id": "es-001:citation:1:0", "kind": "citation"},
        {"row_id": "es-002:abstention", "kind": "abstention"},
    ]
    empty = Adjudication(rows={})

    with pytest.raises(AdjudicationError, match="faltan 2 veredictos"):
        validate_adjudication(empty, expected)

    from tools.eval.report import AdjudicationRow

    only_one = Adjudication(
        rows={
            "es-001:citation:1:0": AdjudicationRow(
                row_id="es-001:citation:1:0",
                kind="citation",
                verdict="correct",
                reviewer="tester",
                reviewed_at="2026-10-30T00:00:00Z",
            )
        }
    )
    with pytest.raises(AdjudicationError, match="faltan 1 veredictos"):
        validate_adjudication(only_one, expected)

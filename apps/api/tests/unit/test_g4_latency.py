"""G4 unit tests for the pure L2.8 latency statistics and report rendering.

These tests use fixed values and never touch a database, a provider or a clock. They cover
the nearest-rank p95, the exact ``<=`` thresholds, incomplete samples, per-language
independence, redaction and publication.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tools.eval.latency import (
    FIRST_TOKEN_P95_TARGET_MS,
    RETRIEVAL_P95_TARGET_MS,
    FirstTokenSample,
    FirstTokenSource,
    LanguageLatency,
    LatencyMetadata,
    RetrievalSample,
    build_payload,
    overall_verdict,
)
from tools.eval.latency_runner import publish_latency


def _language(
    values: Sequence[float],
    *,
    language: str = "es",
    first_values: Sequence[float] | None = None,
    sources: Sequence[FirstTokenSource] | None = None,
    expected_items: int | None = None,
    cold_retrieval_ms: float | None = None,
    cold_first_token_ms: float | None = None,
) -> LanguageLatency:
    first = tuple(first_values) if first_values is not None else tuple(values)
    default_source: FirstTokenSource = "llm"
    origins: tuple[FirstTokenSource, ...] = (
        tuple(sources) if sources is not None else (default_source,) * len(first)
    )
    if len(origins) != len(first):
        raise AssertionError("sources y first_values deben tener la misma longitud")
    return LanguageLatency(
        language=language,
        expected_items=len(values) if expected_items is None else expected_items,
        retrieval=tuple(
            RetrievalSample(item_id=f"r{position}", language=language, took_ms=value)
            for position, value in enumerate(values, start=1)
        ),
        first_token=tuple(
            FirstTokenSample(
                item_id=f"r{position}",
                language=language,
                first_token_ms=value,
                source=origin,
            )
            for position, (value, origin) in enumerate(
                zip(first, origins, strict=True), start=1
            )
        ),
        cold_retrieval_ms=cold_retrieval_ms,
        cold_first_token_ms=cold_first_token_ms,
    )


def _metadata(*, run_id: str = "g4-v1-20261031") -> LatencyMetadata:
    return LatencyMetadata(
        measured_at="2026-10-31T12:00:00+00:00",
        run_id=run_id,
        dataset_version="g4-v1",
        dataset_hash="a" * 64,
        embedding={
            "provider": "HttpEmbeddingProvider",
            "model": "text-embedding-3-small",
            "dimension": 1536,
        },
        llm={"provider": "HttpChatProvider", "model": "gpt-4o-mini"},
        prompt_version="v1",
        environment={"python": "3.12.0", "platform": "test", "cpu_count": "8"},
        git_commit="f" * 40,
        git_dirty=False,
        language_metadata={"es": {"classes": 10, "chunks": 120}},
    )


def _keys(value: Any) -> set[str]:
    if isinstance(value, Mapping):
        found: set[str] = {str(key) for key in value}
        for item in value.values():
            found |= _keys(item)
        return found
    if isinstance(value, list):
        found = set()
        for item in value:
            found |= _keys(item)
        return found
    return set()


def test_g4_latency_p95_is_nearest_rank_and_reports_n() -> None:
    latency = _language([float(value) for value in range(1, 101)])

    assert latency.retrieval_p50_ms == 50.0
    assert latency.retrieval_p95_ms == 95.0
    assert len(latency.retrieval) == 100
    assert latency.evaluated


def test_g4_latency_exact_thresholds_pass() -> None:
    latency = _language(
        [RETRIEVAL_P95_TARGET_MS] * 20,
        first_values=[FIRST_TOKEN_P95_TARGET_MS] * 20,
    )

    assert latency.retrieval_p95_ms == RETRIEVAL_P95_TARGET_MS
    assert latency.first_token_p95_ms == FIRST_TOKEN_P95_TARGET_MS
    assert latency.retrieval_passed
    assert latency.first_token_passed
    assert latency.passed


def test_g4_latency_retrieval_above_target_fails() -> None:
    latency = _language([100.0] * 18 + [RETRIEVAL_P95_TARGET_MS + 0.01] * 2)

    assert latency.retrieval_p95_ms == RETRIEVAL_P95_TARGET_MS + 0.01
    assert not latency.retrieval_passed
    verdict = overall_verdict([latency])
    assert not verdict.passed
    assert any("recuperación" in reason for reason in verdict.reasons)


def test_g4_latency_first_token_above_target_fails() -> None:
    latency = _language(
        [100.0] * 20,
        first_values=[1000.0] * 18 + [FIRST_TOKEN_P95_TARGET_MS + 0.01] * 2,
    )

    assert latency.retrieval_passed
    assert not latency.first_token_passed
    verdict = overall_verdict([latency])
    assert not verdict.passed
    assert any("primer token" in reason for reason in verdict.reasons)


def test_g4_latency_empty_samples_are_not_evaluated() -> None:
    latency = LanguageLatency(language="es", expected_items=5)

    assert latency.retrieval_p95_ms is None
    assert latency.first_token_p95_ms is None
    assert not latency.evaluated
    assert not latency.passed
    verdict = overall_verdict([latency])
    assert not verdict.evaluated
    assert not verdict.passed
    assert build_payload(_metadata(), [latency], verdict)["status"] == "NOT_EVALUATED"


def test_g4_latency_failed_language_cannot_be_compensated() -> None:
    good = _language([1.0] * 20, language="es")
    failed = _language(
        [100.0] * 18 + [RETRIEVAL_P95_TARGET_MS + 0.01] * 2,
        language="en",
    )

    verdict = overall_verdict([good, failed])

    assert verdict.evaluated
    assert not verdict.passed
    assert verdict.reasons
    assert verdict.reasons[0].startswith("en:")


def test_g4_latency_payload_has_date_environment_n_and_no_text() -> None:
    latency = _language([10.0] * 20)
    payload = build_payload(_metadata(), [latency], overall_verdict([latency]))

    assert payload["measured_at"] == "2026-10-31T12:00:00+00:00"
    assert payload["environment"]["python"] == "3.12.0"
    assert payload["languages"]["es"]["retrieval"]["n"] == 20
    keys = _keys(payload)
    assert "question" not in keys
    assert "answer" not in keys
    assert "quote" not in keys
    assert "content" not in keys
    assert "transcript" not in keys


def test_g4_latency_payload_publishes_the_measured_thresholds() -> None:
    latency = replace(
        _language([10.0] * 20),
        retrieval_target_ms=600.0,
        first_token_target_ms=6000.0,
    )
    payload = build_payload(_metadata(), [latency], overall_verdict([latency]))

    assert payload["thresholds"] == {
        "retrieval_p95_ms": 600.0,
        "first_token_p95_ms": 6000.0,
    }
    assert payload["languages"]["es"]["retrieval"]["target_ms"] == 600.0
    assert payload["languages"]["es"]["first_token"]["target_ms"] == 6000.0


def test_g4_latency_cold_start_is_reported_separately() -> None:
    latency = _language(
        [10.0] * 20,
        cold_retrieval_ms=9999.0,
        cold_first_token_ms=19999.0,
    )
    payload = build_payload(_metadata(), [latency], overall_verdict([latency]))

    assert latency.retrieval_p95_ms == 10.0
    assert latency.first_token_p95_ms == 10.0
    cold = payload["languages"]["es"]["cold_start"]
    assert cold["retrieval_ms"] == 9999.0
    assert cold["first_token_ms"] == 19999.0
    assert payload["languages"]["es"]["retrieval"]["p95_ms"] == 10.0


@pytest.mark.parametrize("value", [-1.0, math.nan, math.inf, -math.inf])
def test_g4_latency_rejects_negative_or_non_finite_samples(value: float) -> None:
    with pytest.raises(ValueError, match="finito no negativo"):
        RetrievalSample(item_id="r1", language="es", took_ms=value)
    with pytest.raises(ValueError, match="finito no negativo"):
        FirstTokenSample(
            item_id="r1",
            language="es",
            first_token_ms=value,
            source="llm",
        )


def test_g4_latency_llm_only_p95_is_diagnostic() -> None:
    sources: tuple[FirstTokenSource, ...] = ("llm",) * 10 + ("abstention",) * 10
    latency = _language(
        [10.0] * 20,
        first_values=[100.0] * 10 + [6000.0] * 10,
        sources=sources,
    )
    payload = build_payload(_metadata(), [latency], overall_verdict([latency]))

    assert latency.first_token_llm_p95_ms == 100.0
    assert latency.first_token_p95_ms == 6000.0
    assert not latency.first_token_passed
    assert payload["languages"]["es"]["first_token_llm_only_p95_ms"] == 100.0
    assert payload["languages"]["es"]["first_token"]["p95_ms"] == 6000.0


def test_g4_latency_publish_writes_redacted_json_and_markdown(tmp_path: Path) -> None:
    latency = _language([10.0] * 20)
    payload = build_payload(_metadata(), [latency], overall_verdict([latency]))

    publish_latency(tmp_path, payload)

    data = json.loads((tmp_path / "latency.json").read_text(encoding="utf-8"))
    report = (tmp_path / "REPORT.md").read_text(encoding="utf-8")
    assert data["status"] == "PASS"
    assert data["languages"]["es"]["retrieval"]["p95_ms"] == 10.0
    assert "L2.8 latency report" in report
    assert "10.00" in report
    assert "question" not in _keys(data)
    assert "answer" not in _keys(data)
    assert "quote" not in _keys(data)

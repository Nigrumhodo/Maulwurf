"""J4.8 / C-S4-JF-10: the dependency-free budget alert check parses the internal endpoint.

The script is deliberately outside the application package so it can run from a cron or an
operator shell without importing FastAPI. It only reads the Prometheus text and prints
provider/unit/usage/limit values.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import urllib.error
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "scripts" / "ops" / "budget_check.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("budget_check", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


budget_check = _load()


def _metrics(
    *,
    llm=(900.0, 1_000.0, 0.9, True, False),
    asr=(12.0, None, None, False, False),
    google=(3.0, 10.0, 0.3, False, False),
) -> str:
    lines = []
    for provider, (usage, limit, ratio, warning, exhausted) in {
        "llm": llm,
        "asr": asr,
        "google": google,
    }.items():
        unit = {"llm": "tokens", "asr": "minutes", "google": "operations"}[provider]
        lines.append(f'maulwurf_budget_usage{{provider="{provider}",unit="{unit}"}} {usage}')
        lines.append(
            f'maulwurf_budget_configured{{provider="{provider}"}} '
            f"{1.0 if limit is not None else 0.0}"
        )
        if limit is not None:
            lines.extend(
                [
                    f'maulwurf_budget_limit{{provider="{provider}",unit="{unit}"}} {limit}',
                    f'maulwurf_budget_ratio{{provider="{provider}"}} {ratio}',
                    f'maulwurf_budget_warning{{provider="{provider}"}} '
                    f"{1.0 if warning else 0.0}",
                    f'maulwurf_budget_exhausted{{provider="{provider}"}} '
                    f"{1.0 if exhausted else 0.0}",
                ]
            )
    return "\n".join(lines) + "\n"


def test_parses_the_labels_and_values_of_the_endpoint() -> None:
    samples = budget_check.parse_metrics(_metrics())

    assert samples[0].name == "maulwurf_budget_usage"
    assert samples[0].labels == {"provider": "llm", "unit": "tokens"}
    assert samples[0].value == 900.0


def test_ignores_help_type_lines_and_blank_lines() -> None:
    text = "# HELP maulwurf_budget_usage usage\n# TYPE maulwurf_budget_usage gauge\n\n" + _metrics()

    assert len(budget_check.parse_metrics(text)) == len(budget_check.parse_metrics(_metrics()))


def test_joins_the_series_into_one_budget_per_provider() -> None:
    budgets = budget_check.evaluate(budget_check.parse_metrics(_metrics()))

    by_provider = {budget.provider: budget for budget in budgets}
    assert by_provider["llm"].state == "warning"
    assert by_provider["llm"].unit == "tokens"
    assert by_provider["llm"].ratio == pytest.approx(0.9)
    assert by_provider["asr"].state == "unconfigured"
    assert by_provider["asr"].limit is None
    assert by_provider["google"].state == "ok"


def test_exit_codes_are_warning_then_exhausted_then_unconfigured() -> None:
    budgets = budget_check.evaluate(budget_check.parse_metrics(_metrics()))
    assert budget_check.exit_code(budgets, require_configured=False) == 1
    assert budget_check.exit_code(budgets, require_configured=True) == 1

    exhausted = budget_check.evaluate(
        budget_check.parse_metrics(
            _metrics(google=(10.0, 10.0, 1.0, False, True))
        )
    )
    assert budget_check.exit_code(exhausted, require_configured=False) == 2

    ok = budget_check.evaluate(
        budget_check.parse_metrics(
            _metrics(
                llm=(10.0, 1_000.0, 0.01, False, False),
                asr=(1.0, 10.0, 0.1, False, False),
                google=(1.0, 10.0, 0.1, False, False),
            )
        )
    )
    assert budget_check.exit_code(ok, require_configured=False) == 0
    assert budget_check.exit_code(ok, require_configured=True) == 0


def test_a_configured_budget_without_limit_is_invalid() -> None:
    text = _metrics().replace('maulwurf_budget_limit{provider="llm",unit="tokens"} 1000.0\n', "")

    with pytest.raises(budget_check.MetricsError, match="limit/ratio"):
        budget_check.evaluate(budget_check.parse_metrics(text))


def test_a_missing_provider_is_invalid() -> None:
    text = "\n".join(
        line for line in _metrics().splitlines() if 'provider="asr"' not in line
    )

    with pytest.raises(budget_check.MetricsError, match="missing budget usage"):
        budget_check.evaluate(budget_check.parse_metrics(text))


@pytest.mark.parametrize(
    "text",
    [
        "maulwurf_budget_usage{provider=\"llm\"} not-a-number",
        "not a metric line",
        "",
    ],
)
def test_malformed_text_is_rejected(text: str) -> None:
    with pytest.raises(budget_check.MetricsError):
        budget_check.parse_metrics(text)


def test_main_prints_json_and_returns_the_state_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        budget_check,
        "_fetch",
        lambda url, timeout: _metrics(llm=(1_000.0, 1_000.0, 1.0, False, True)),
    )

    code = budget_check.main(["--url", "http://internal.test/metrics", "--format", "json"])

    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert {row["provider"] for row in payload} == {"llm", "asr", "google"}
    assert next(row for row in payload if row["provider"] == "llm")["state"] == "exhausted"


def test_main_reports_a_transport_error_without_a_stack_trace(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _fail(url: str, timeout: float) -> str:
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(budget_check, "_fetch", _fail)

    code = budget_check.main([])

    assert code == 3
    assert "budget_check: URLError" in capsys.readouterr().err

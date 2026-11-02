"""J4.8 / C-S4-JF-10: provider budgets are pure thresholds over measured usage.

The budget is an operator-provided monthly cap for an already measured unit:
LLM tokens, ASR source minutes and recorded Google operations. No provider price or
capacity limit is invented here; an unset cap reports usage with ``configured=0`` and
never alarms.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.services.provider_budget import (
    PROVIDERS,
    UNITS,
    BudgetStatus,
    budget_samples,
    evaluate_budget,
    monthly_bounds,
    statuses_from_settings,
)

SECRETS = {
    "env": "local",
    "secret_key": "budget-secret-key-0123456789",
    "encryption_key": "budget-encryption-key-0123456789",
    "oauth_state_secret": "budget-oauth-state-key-0123456789",
}


def _samples(statuses: list[BudgetStatus]) -> dict[tuple[str, str], float]:
    return {
        (sample.name, sample.labels.get("provider", "")): float(sample.value)
        for sample in budget_samples(statuses)
    }


def test_a_month_is_the_utc_calendar_month() -> None:
    start, end = monthly_bounds(datetime(2026, 11, 1, 0, 0, tzinfo=UTC))

    assert start == datetime(2026, 11, 1, tzinfo=UTC)
    assert end == datetime(2026, 12, 1, tzinfo=UTC)


def test_december_rolls_over_to_the_next_year() -> None:
    start, end = monthly_bounds(datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC))

    assert start == datetime(2026, 12, 1, tzinfo=UTC)
    assert end == datetime(2027, 1, 1, tzinfo=UTC)


def test_an_aware_datetime_is_required() -> None:
    with pytest.raises(ValueError, match="timezone"):
        monthly_bounds(datetime(2026, 11, 1))


def test_unset_cap_reports_usage_without_an_alert() -> None:
    status = evaluate_budget("llm", 12_345.0, None, warning_ratio=0.8)

    assert status.configured is False
    assert status.state == "unconfigured"
    assert status.ratio is None
    assert status.warning is False
    assert status.exhausted is False


def test_usage_below_the_warning_ratio_is_ok() -> None:
    status = evaluate_budget("llm", 799.0, 1_000.0, warning_ratio=0.8)

    assert status.state == "ok"
    assert status.ratio == pytest.approx(0.799)


def test_the_warning_ratio_boundary_is_inclusive() -> None:
    status = evaluate_budget("asr", 80.0, 100.0, warning_ratio=0.8)

    assert status.state == "warning"
    assert status.warning is True
    assert status.exhausted is False


def test_the_cap_boundary_is_exhausted_not_merely_warning() -> None:
    status = evaluate_budget("google", 10.0, 10.0, warning_ratio=0.8)

    assert status.state == "exhausted"
    assert status.warning is False
    assert status.exhausted is True
    assert status.ratio == pytest.approx(1.0)


def test_usage_above_the_cap_stays_exhausted() -> None:
    status = evaluate_budget("asr", 150.0, 100.0, warning_ratio=0.9)

    assert status.state == "exhausted"
    assert status.ratio == pytest.approx(1.5)


@pytest.mark.parametrize("provider", sorted(PROVIDERS))
def test_every_provider_has_one_measured_unit(provider: str) -> None:
    assert UNITS[provider] in {"tokens", "minutes", "operations"}


@pytest.mark.parametrize(
    ("provider", "used", "limit", "warning_ratio"),
    [
        ("unknown", 1.0, 10.0, 0.8),
        ("llm", -1.0, 10.0, 0.8),
        ("llm", 1.0, 0.0, 0.8),
        ("llm", 1.0, 10.0, 0.0),
        ("llm", 1.0, 10.0, 1.1),
        ("llm", float("nan"), 10.0, 0.8),
        ("llm", 1.0, float("inf"), 0.8),
    ],
)
def test_invalid_inputs_fail_loudly(
    provider: str, used: float, limit: float, warning_ratio: float
) -> None:
    with pytest.raises(ValueError):
        evaluate_budget(provider, used, limit, warning_ratio=warning_ratio)


def test_samples_expose_usage_configuration_and_state() -> None:
    statuses = [
        evaluate_budget("llm", 900.0, 1_000.0, warning_ratio=0.8),
        evaluate_budget("asr", 12.0, None, warning_ratio=0.8),
        evaluate_budget("google", 15.0, 10.0, warning_ratio=0.8),
    ]

    samples = _samples(statuses)

    assert samples[("maulwurf_budget_usage", "llm")] == 900.0
    assert samples[("maulwurf_budget_configured", "llm")] == 1.0
    assert samples[("maulwurf_budget_limit", "llm")] == 1_000.0
    assert samples[("maulwurf_budget_ratio", "llm")] == pytest.approx(0.9)
    assert samples[("maulwurf_budget_warning", "llm")] == 1.0
    assert samples[("maulwurf_budget_exhausted", "llm")] == 0.0

    assert samples[("maulwurf_budget_usage", "asr")] == 12.0
    assert samples[("maulwurf_budget_configured", "asr")] == 0.0
    assert ("maulwurf_budget_limit", "asr") not in samples
    assert ("maulwurf_budget_ratio", "asr") not in samples

    assert samples[("maulwurf_budget_exhausted", "google")] == 1.0


def test_samples_carry_the_measured_unit_label() -> None:
    (sample,) = [s for s in budget_samples([evaluate_budget("asr", 1.0, 2.0)]) if s.name ==
                 "maulwurf_budget_usage"]

    assert sample.labels == {"provider": "asr", "unit": "minutes"}


def test_settings_map_each_configured_cap_to_the_current_month() -> None:
    settings = Settings(
        _env_file=None,
        **SECRETS,
        budget_llm_tokens=2_000_000,
        budget_asr_minutes=400.0,
        budget_google_operations=10_000,
        budget_warning_ratio=0.75,
    )

    statuses = statuses_from_settings(settings)
    by_provider = {status.provider: status for status in statuses}

    assert by_provider["llm"].limit == 2_000_000
    assert by_provider["asr"].limit == 400.0
    assert by_provider["google"].limit == 10_000
    assert by_provider["llm"].warning_ratio == 0.75


def test_settings_without_caps_report_unconfigured_budgets() -> None:
    settings = Settings(_env_file=None, **SECRETS)

    assert [status.state for status in statuses_from_settings(settings)] == [
        "unconfigured",
        "unconfigured",
        "unconfigured",
    ]


@pytest.mark.parametrize(
    "overrides",
    [
        {"budget_llm_tokens": 0},
        {"budget_asr_minutes": 0},
        {"budget_google_operations": -1},
        {"budget_warning_ratio": 0},
        {"budget_warning_ratio": 1.01},
    ],
)
def test_settings_reject_impossible_caps(overrides: dict[str, float]) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **SECRETS, **overrides)

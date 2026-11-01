#!/usr/bin/env python3
"""J4.8 / C-S4-JF-10: evaluate provider budgets from `/internal/metrics`.

This is the dependency-free alert path for deployments that do not run Prometheus, and a
manual check for those that do. It reads the internal Prometheus text endpoint, prints one
redacted line per provider and exits non-zero when a configured cap needs attention.

Usage::

    python scripts/ops/budget_check.py
    python scripts/ops/budget_check.py --url http://api:8000/internal/metrics
    python scripts/ops/budget_check.py --require-configured --format json

Exit codes:

* ``0`` — every configured budget is below its warning ratio (unconfigured caps are allowed
  unless ``--require-configured`` is set);
* ``1`` — at least one warning, or a missing cap with ``--require-configured``;
* ``2`` — at least one exhausted cap;
* ``3`` — the endpoint could not be read or its text is not valid Prometheus exposition.

The script never prints a token, key, URL credential, transcript or provider payload. The
only values it emits are the metric numbers and the closed provider/unit labels.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Final

DEFAULT_URL: Final = "http://127.0.0.1:8000/internal/metrics"
PROVIDERS: Final = ("llm", "asr", "google")
_METRIC_LINE: Final = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?P<labels>\{.*\})?\s+(?P<value>\S+)$"
)
_LABEL: Final = re.compile(r'(?P<key>[a-zA-Z_][a-zA-Z0-9_]*)="(?P<value>(?:\\.|[^"\\])*)"')


@dataclass(frozen=True)
class Sample:
    name: str
    labels: dict[str, str]
    value: float


@dataclass(frozen=True)
class Budget:
    provider: str
    unit: str
    usage: float
    limit: float | None
    ratio: float | None
    warning: bool
    exhausted: bool

    @property
    def state(self) -> str:
        if self.limit is None:
            return "unconfigured"
        if self.exhausted:
            return "exhausted"
        if self.warning:
            return "warning"
        return "ok"

    def to_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "unit": self.unit,
            "usage": self.usage,
            "limit": self.limit,
            "ratio": self.ratio,
            "state": self.state,
        }


class MetricsError(ValueError):
    """The endpoint did not return the expected Prometheus text."""


def _unescape(value: str) -> str:
    return value.replace("\\\\", "\\").replace('\\"', '"').replace("\\n", "\n")


def parse_metrics(text: str) -> list[Sample]:
    """Parse the small exposition subset this endpoint emits (no external dependency)."""
    samples: list[Sample] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _METRIC_LINE.match(line)
        if match is None:
            raise MetricsError("invalid metric line")
        labels: dict[str, str] = {}
        label_text = match.group("labels")
        if label_text:
            for label in _LABEL.finditer(label_text):
                labels[label.group("key")] = _unescape(label.group("value"))
        try:
            value = float(match.group("value"))
        except ValueError as exc:
            raise MetricsError("metric value is not a number") from exc
        if not math.isfinite(value):
            raise MetricsError("metric value is not finite")
        samples.append(Sample(match.group("name"), labels, value))
    if not samples:
        raise MetricsError("the endpoint returned no metrics")
    return samples


def _index(samples: list[Sample], name: str) -> dict[str, Sample]:
    return {
        sample.labels.get("provider", ""): sample
        for sample in samples
        if sample.name == name and sample.labels.get("provider") in PROVIDERS
    }


def evaluate(samples: list[Sample]) -> list[Budget]:
    """Join the exported series into one Budget per provider."""
    usage = _index(samples, "maulwurf_budget_usage")
    configured = _index(samples, "maulwurf_budget_configured")
    limits = _index(samples, "maulwurf_budget_limit")
    ratios = _index(samples, "maulwurf_budget_ratio")
    warning = _index(samples, "maulwurf_budget_warning")
    exhausted = _index(samples, "maulwurf_budget_exhausted")
    missing = [provider for provider in PROVIDERS if provider not in usage]
    if missing:
        raise MetricsError(f"missing budget usage for: {', '.join(missing)}")
    budgets: list[Budget] = []
    for provider in PROVIDERS:
        is_configured = configured.get(provider)
        if is_configured is None:
            raise MetricsError(f"missing configured flag for: {provider}")
        usage_sample = usage[provider]
        limit = limits.get(provider)
        ratio = ratios.get(provider)
        limited = is_configured.value == 1.0
        if limited and (limit is None or ratio is None):
            raise MetricsError(f"configured budget lacks limit/ratio: {provider}")
        budgets.append(
            Budget(
                provider=provider,
                unit=usage_sample.labels.get("unit", "unknown"),
                usage=usage_sample.value,
                limit=None if limit is None else limit.value,
                ratio=None if ratio is None else ratio.value,
                warning=bool(warning.get(provider) and warning[provider].value == 1.0),
                exhausted=bool(
                    exhausted.get(provider) and exhausted[provider].value == 1.0
                ),
            )
        )
    return budgets


def exit_code(budgets: list[Budget], *, require_configured: bool) -> int:
    if any(budget.exhausted for budget in budgets):
        return 2
    if any(budget.warning for budget in budgets):
        return 1
    if require_configured and any(budget.limit is None for budget in budgets):
        return 1
    return 0


def _fetch(url: str, timeout: float) -> str:
    if not url.startswith(("http://", "https://")):
        raise MetricsError("the metrics URL must be http(s)")
    request = urllib.request.Request(url, headers={"Accept": "text/plain"})  # noqa: S310
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return response.read().decode("utf-8")


def _format_text(budgets: list[Budget]) -> str:
    lines = []
    for budget in budgets:
        limit = "-" if budget.limit is None else f"{budget.limit:g}"
        ratio = "-" if budget.ratio is None else f"{budget.ratio:.4f}"
        lines.append(
            f"provider={budget.provider} state={budget.state} usage={budget.usage:g} "
            f"unit={budget.unit} limit={limit} ratio={ratio}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL, help="internal metrics URL")
    parser.add_argument("--timeout", type=float, default=5.0, help="HTTP timeout in seconds")
    parser.add_argument(
        "--require-configured",
        action="store_true",
        help="fail when a provider has no monthly cap",
    )
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)
    try:
        budgets = evaluate(parse_metrics(_fetch(args.url, args.timeout)))
    except (MetricsError, OSError, urllib.error.URLError) as exc:
        print(f"budget_check: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    if args.format == "json":
        print(json.dumps([budget.to_dict() for budget in budgets], sort_keys=True))
    else:
        print(_format_text(budgets))
    return exit_code(budgets, require_configured=args.require_configured)


if __name__ == "__main__":
    raise SystemExit(main())

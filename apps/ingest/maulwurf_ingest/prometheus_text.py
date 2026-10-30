"""Prometheus text exposition renderer (J2.5), with no external client library.

Written by hand because the exposition format is small (HELP, TYPE and
`name{labels} value` lines), and it avoids pinning a new dependency in `uv.lock`. Only
gauges and counters the code already computes; labels never carry audio ids or user text.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal

PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
_METRIC_NAME = re.compile(r"[a-zA-Z_:][a-zA-Z0-9_:]*")
_LABEL_NAME = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")


@dataclass(frozen=True)
class Sample:
    """One series: name, type, help text, labels and value."""

    name: str
    kind: Literal["gauge", "counter"]
    help_text: str
    value: float
    labels: Mapping[str, str] = field(default_factory=dict)


def _escape_label_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _format_labels(labels: Mapping[str, str]) -> str:
    if not labels:
        return ""
    parts = []
    for key, value in sorted(labels.items()):
        if not _LABEL_NAME.fullmatch(key):
            raise ValueError(f"nombre de etiqueta inválido: {key!r}")
        parts.append(f'{key}="{_escape_label_value(value)}"')
    return "{" + ",".join(parts) + "}"


def render(samples: Iterable[Sample]) -> str:
    """Exposition text; HELP and TYPE are emitted once per metric name."""
    lines: list[str] = []
    declared: set[str] = set()
    for sample in samples:
        if not _METRIC_NAME.fullmatch(sample.name):
            raise ValueError(f"nombre de métrica inválido: {sample.name!r}")
        if sample.name not in declared:
            declared.add(sample.name)
            lines.append(f"# HELP {sample.name} {sample.help_text}")
            lines.append(f"# TYPE {sample.name} {sample.kind}")
        lines.append(f"{sample.name}{_format_labels(sample.labels)} {sample.value}")
    return "\n".join(lines) + "\n"

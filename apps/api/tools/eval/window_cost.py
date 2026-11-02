"""Offline token-cost measurement for analysis windows (S3.2, G8 input).

The extraction budget needs two numbers per class hour: how many input tokens the
overlapping windows send, and how many provider calls the windowing forces. This tool
measures the first one exactly and the local planning latency; it cannot measure provider
latency without an authorized call, so that datum is reported as ``NO VERIFICADO``.

Run from ``apps/api``::

    uv run python -m tools.eval.window_cost
    uv run python -m tools.eval.window_cost --format json

or from the repository root through ``scripts/eval/window_cost.py``.

The fixtures are authored/redacted classroom text in the F0.1 spike languages. No audio,
real class transcript, student data or provider output is read or written. The projection
to a class hour is linear from the authored spoken duration; it is a budget estimate, not
an observed class measurement.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.analysis_windows import (
    DEFAULT_OVERLAP_TOKENS,
    DEFAULT_TARGET_TOKENS,
    WindowPlan,
    plan_windows,
)
from app.services.chunking import (
    TIKTOKEN_ENCODING_NAME,
    SegmentInput,
    TokenCounter,
    count_tokens_default,
    tokenizer_is_exact,
)

DEFAULT_MANIFEST = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "analysis"
    / "windows"
    / "manifest.json"
)
SUPPORTED_LANGUAGES = frozenset({"es", "en", "fr"})
MAX_REPEATS = 50
_TOKEN_NAMESPACE = uuid.UUID("b6d5a5b1-3f4d-4d6a-9c2e-5f0f2c8f7a11")


@dataclass(frozen=True)
class WindowProfile:
    """One measured window shape; the name is the table/JSON key."""

    name: str
    target_tokens: int
    overlap_tokens: int


DEFAULT_PROFILE = WindowProfile(
    name="default",
    target_tokens=DEFAULT_TARGET_TOKENS,
    overlap_tokens=DEFAULT_OVERLAP_TOKENS,
)
DEFAULT_PROFILES: tuple[WindowProfile, ...] = (
    WindowProfile(name="no-overlap", target_tokens=DEFAULT_TARGET_TOKENS, overlap_tokens=0),
    WindowProfile(
        name="lean",
        target_tokens=DEFAULT_TARGET_TOKENS,
        overlap_tokens=DEFAULT_OVERLAP_TOKENS // 2,
    ),
    DEFAULT_PROFILE,
    WindowProfile(name="small", target_tokens=DEFAULT_TARGET_TOKENS // 2, overlap_tokens=50),
)


@dataclass(frozen=True)
class ClassSpec:
    class_ref: str
    language: str
    file: str
    spoken_seconds: float


@dataclass(frozen=True)
class Manifest:
    version: str
    kind: str
    provenance: str
    classes: tuple[ClassSpec, ...]
    base_dir: Path


@dataclass(frozen=True)
class LoadedClass:
    spec: ClassSpec
    text: str
    segments: tuple[SegmentInput, ...]


@dataclass(frozen=True)
class ClassMeasurement:
    profile: WindowProfile
    class_ref: str
    language: str
    spoken_seconds: float
    segments: int
    transcript_tokens: int
    windows: int
    total_tokens: int
    overlap_tokens: int
    max_window_tokens: int
    amplification: float
    planning_latency_ms: float
    tokens_per_class_hour: float
    windows_per_class_hour: float


@dataclass(frozen=True)
class MeasurementReport:
    manifest: Manifest
    tokenizer_exact: bool
    measurements: tuple[ClassMeasurement, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "fixture_version": self.manifest.version,
            "fixture_kind": self.manifest.kind,
            "provenance": self.manifest.provenance,
            "tokenizer": {
                "encoding": TIKTOKEN_ENCODING_NAME,
                "exact": self.tokenizer_exact,
            },
            "latency_scope": "window planning only",
            "llm_latency_ms": None,
            "limitations": [
                "LLM/provider latency: NO VERIFICADO. No authorized provider call was made "
                "and D3-Text is not closed.",
                "Prompt system/user overhead and output tokens are not included; prompts v1 "
                "(L3.1) do not exist in develop.",
                "The class-hour projection is linear from the authored spoken duration of "
                "each redacted excerpt.",
            ],
            "profiles": [
                {
                    "name": profile.name,
                    "target_tokens": profile.target_tokens,
                    "overlap_tokens": profile.overlap_tokens,
                }
                for profile in _profiles(self.measurements)
            ],
            "classes": [
                {
                    "profile": measurement.profile.name,
                    "class_ref": measurement.class_ref,
                    "language": measurement.language,
                    "spoken_seconds": measurement.spoken_seconds,
                    "segments": measurement.segments,
                    "transcript_tokens": measurement.transcript_tokens,
                    "windows": measurement.windows,
                    "total_tokens": measurement.total_tokens,
                    "overlap_tokens": measurement.overlap_tokens,
                    "max_window_tokens": measurement.max_window_tokens,
                    "amplification": round(measurement.amplification, 4),
                    "planning_latency_ms": round(measurement.planning_latency_ms, 3),
                    "tokens_per_class_hour": round(
                        measurement.tokens_per_class_hour, 1
                    ),
                    "windows_per_class_hour": round(
                        measurement.windows_per_class_hour, 1
                    ),
                }
                for measurement in self.measurements
            ],
        }

    def render_table(self) -> str:
        lines = [
            f"Fixture: {self.manifest.version} ({self.manifest.kind})",
            f"Tokenizer: {TIKTOKEN_ENCODING_NAME} (exact={self.tokenizer_exact})",
            "Latency: window planning only; LLM/provider latency NO VERIFICADO.",
            (
                f"{'profile':<11} {'lang':<4} {'class':<20} {'minutes':>7} {'segs':>5} "
                f"{'tokens':>7} {'wins':>4} {'total':>6} {'ovl':>5} {'ampl':>5} "
                f"{'max_win':>7} {'plan_ms':>8} {'tok/hour':>9}"
            ),
        ]
        for measurement in self.measurements:
            lines.append(
                f"{measurement.profile.name:<11} "
                f"{measurement.language:<4} "
                f"{measurement.class_ref:<20} "
                f"{measurement.spoken_seconds / 60:>7.2f} "
                f"{measurement.segments:>5} "
                f"{measurement.transcript_tokens:>7} "
                f"{measurement.windows:>4} "
                f"{measurement.total_tokens:>6} "
                f"{measurement.overlap_tokens:>5} "
                f"{measurement.amplification:>5.3f} "
                f"{measurement.max_window_tokens:>7} "
                f"{measurement.planning_latency_ms:>8.3f} "
                f"{measurement.tokens_per_class_hour:>9.1f}"
            )
        return "\n".join(lines)


def _profiles(measurements: Sequence[ClassMeasurement]) -> tuple[WindowProfile, ...]:
    seen: dict[str, WindowProfile] = {}
    for measurement in measurements:
        seen.setdefault(measurement.profile.name, measurement.profile)
    return tuple(seen.values())


def load_manifest(path: Path = DEFAULT_MANIFEST) -> Manifest:
    """Load and validate the fixture manifest without reading the fixtures themselves."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"manifest is not readable: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError("manifest is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("manifest must be a JSON object")
    version = payload.get("version")
    kind = payload.get("kind")
    provenance = payload.get("provenance")
    raw_classes = payload.get("classes")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("manifest version is required")
    if not isinstance(kind, str) or not kind.strip():
        raise ValueError("manifest kind is required")
    if not isinstance(provenance, str) or not provenance.strip():
        raise ValueError("manifest provenance is required")
    if not isinstance(raw_classes, list) or not raw_classes:
        raise ValueError("manifest classes must be a non-empty list")
    classes: list[ClassSpec] = []
    seen: set[str] = set()
    for raw in raw_classes:
        if not isinstance(raw, dict):
            raise ValueError("manifest class entries must be objects")
        class_ref = raw.get("class_ref")
        language = raw.get("language")
        file_name = raw.get("file")
        spoken_seconds = raw.get("spoken_seconds")
        if not isinstance(class_ref, str) or not class_ref.strip():
            raise ValueError("class_ref is required")
        if class_ref in seen:
            raise ValueError(f"duplicate class_ref: {class_ref}")
        if not isinstance(language, str) or language not in SUPPORTED_LANGUAGES:
            raise ValueError(f"unsupported language: {language!r}")
        if not isinstance(file_name, str) or not file_name.strip():
            raise ValueError(f"fixture file is required for {class_ref}")
        if not isinstance(spoken_seconds, int | float) or spoken_seconds <= 0:
            raise ValueError(f"spoken_seconds must be positive for {class_ref}")
        seen.add(class_ref)
        classes.append(
            ClassSpec(
                class_ref=class_ref,
                language=language,
                file=file_name,
                spoken_seconds=float(spoken_seconds),
            )
        )
    return Manifest(
        version=version,
        kind=kind,
        provenance=provenance,
        classes=tuple(classes),
        base_dir=path.resolve().parent,
    )


def load_classes(manifest: Manifest) -> list[LoadedClass]:
    """Read the authored fixtures and build deterministic contiguous ASR segments."""
    loaded: list[LoadedClass] = []
    for spec in manifest.classes:
        candidate = (manifest.base_dir / spec.file).resolve()
        if not candidate.is_relative_to(manifest.base_dir):
            raise ValueError(f"fixture path escapes the manifest directory: {spec.file}")
        try:
            raw = candidate.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(f"fixture is not readable: {spec.file}") from exc
        lines = [line for line in raw.splitlines() if line.strip()]
        if not lines:
            raise ValueError(f"fixture has no text: {spec.file}")
        text = "\n".join(lines)
        segments: list[SegmentInput] = []
        offset = 0
        for ordinal, line in enumerate(lines):
            segment_text = line + ("\n" if ordinal < len(lines) - 1 else "")
            segments.append(
                SegmentInput(
                    id=uuid.uuid5(
                        _TOKEN_NAMESPACE,
                        f"{manifest.version}:{spec.class_ref}:{ordinal}",
                    ),
                    ordinal=ordinal,
                    text=segment_text,
                    char_start=offset,
                    char_end=offset + len(segment_text),
                    t_start=None,
                    t_end=None,
                )
            )
            offset += len(segment_text)
        if offset != len(text):
            raise ValueError(f"fixture offsets do not cover the text: {spec.file}")
        loaded.append(
            LoadedClass(spec=spec, text=text, segments=tuple(segments))
        )
    return loaded


def measure_class(
    loaded: LoadedClass,
    profile: WindowProfile,
    *,
    repeats: int = 3,
    count_tokens: TokenCounter = count_tokens_default,
) -> ClassMeasurement:
    """Measure one class under one window profile; latency is local planning only."""
    if repeats < 1:
        raise ValueError("repeats must be >= 1")
    plan: WindowPlan | None = None
    samples: list[float] = []
    for _ in range(repeats):
        started = time.perf_counter_ns()
        current = plan_windows(
            transcript_text=loaded.text,
            segments=loaded.segments,
            target_tokens=profile.target_tokens,
            overlap_tokens=profile.overlap_tokens,
            count_tokens=count_tokens,
        )
        samples.append((time.perf_counter_ns() - started) / 1_000_000)
        plan = current
    if plan is None:
        raise RuntimeError("window planning produced no plan")
    windows = plan.window_count
    hours = loaded.spec.spoken_seconds / 3600
    return ClassMeasurement(
        profile=profile,
        class_ref=loaded.spec.class_ref,
        language=loaded.spec.language,
        spoken_seconds=loaded.spec.spoken_seconds,
        segments=len(loaded.segments),
        transcript_tokens=plan.transcript_tokens,
        windows=windows,
        total_tokens=plan.total_tokens,
        overlap_tokens=plan.overlap_tokens_total,
        max_window_tokens=max((window.token_count for window in plan.windows), default=0),
        amplification=plan.amplification,
        planning_latency_ms=statistics.median(samples),
        tokens_per_class_hour=plan.total_tokens / hours,
        windows_per_class_hour=windows / hours,
    )


def measure(
    manifest: Manifest,
    profiles: Sequence[WindowProfile] = DEFAULT_PROFILES,
    *,
    repeats: int = 3,
    classes: Sequence[LoadedClass] | None = None,
    count_tokens: TokenCounter = count_tokens_default,
) -> MeasurementReport:
    if not profiles:
        raise ValueError("at least one profile is required")
    loaded = list(classes) if classes is not None else load_classes(manifest)
    measurements = tuple(
        measure_class(loaded_class, profile, repeats=repeats, count_tokens=count_tokens)
        for profile in profiles
        for loaded_class in loaded
    )
    return MeasurementReport(
        manifest=manifest,
        tokenizer_exact=tokenizer_is_exact(),
        measurements=measurements,
    )


def _bounded_repeats(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--repeats must be an integer") from exc
    if not 1 <= parsed <= MAX_REPEATS:
        raise argparse.ArgumentTypeError(f"--repeats must be between 1 and {MAX_REPEATS}")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.eval.window_cost",
        description=(
            "Measure analysis-window token cost over authored/redacted fixtures. "
            "Never calls an LLM, Riva or Google."
        ),
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--profile",
        action="append",
        choices=[profile.name for profile in DEFAULT_PROFILES],
        dest="profile_names",
        help="profile to measure; repeatable (default: all)",
    )
    parser.add_argument("--format", choices=("table", "json"), default="table")
    parser.add_argument("--repeats", type=_bounded_repeats, default=3)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    names = set(args.profile_names or ())
    selected = tuple(
        profile for profile in DEFAULT_PROFILES if not names or profile.name in names
    )
    try:
        manifest = load_manifest(args.manifest)
        report = measure(
            manifest,
            selected,
            repeats=args.repeats,
        )
    except ValueError as exc:
        print(f"window_cost: {exc}", file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(report.render_table())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

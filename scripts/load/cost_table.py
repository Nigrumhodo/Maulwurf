"""S4.5: reproducible real cost per class hour (C-S4-SG-04).

The G8 budget needs three measured terms, not estimates:

* **ASR:** the effective minutes a class hour sends; the measured factor is the fragment
  plan (including the local overlap) and the provider rate stays an explicit input. The
  F0.1 spike never measured a price, so without ``--asr-rate-usd-per-minute`` the term is
  ``NO VERIFICADO`` instead of an invented number.
* **ffmpeg CPU:** the user+system seconds of the ffprobe/ffmpeg children on the production
  ``pipeline.process`` path, normalized to one source hour and to vCPU-hours.
* **RAM:** the peak working set of a slot (worker RSS + tmpfs holding the original and the
  active fragment) over the measured local wall time, expressed as GiB-hours per class
  hour. It is a lower bound: provider waiting and idle reservation are excluded.

The load comes from the S3.5 harness (`scripts/load/asr_cost.py`), which runs the
production audio path with an empty-words transcriber stub: no Riva channel, no key and no
provider call. Audio is synthetic, generated in RAM and written only to a private tmpfs
that every worker deletes and verifies.

Run from the repository root with the ingest environment::

    cd apps/ingest && uv run python ../../scripts/load/cost_table.py
    cd apps/ingest && uv run python ../../scripts/load/cost_table.py --format json
    cd apps/ingest && uv run python ../../scripts/load/cost_table.py \
        --asr-rate-usd-per-minute 0.01 --cpu-rate-usd-per-vcpu-hour 0.05 \
        --ram-rate-usd-per-gib-hour 0.005

The default clip is 300 s, the published basis; the S3.5 harness keeps its own 60 s
default for the fixed-overhead check. Every rate is an operator input, never a provider
measurement. The script refuses to run without a writable tmpfs, never prints audio and
keeps no WAV after the run.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

_SCRIPT: Final = Path(__file__).resolve()
sys.path.insert(0, str(_SCRIPT.parent))

import asr_cost  # noqa: E402

from maulwurf_ingest.capacity import PROVISIONAL_SLOTS  # noqa: E402

TICKET: Final = "S4.5"
TEST_BASIS: Final = "C-S4-SG-04"
NOT_VERIFIED: Final = asr_cost.NOT_VERIFIED
SECONDS_PER_HOUR: Final = 3600.0
# The published captures use 300 s; shorter clips inflate the fixed overhead.
DEFAULT_CLIP_SECONDS: Final = 300.0


@dataclass(frozen=True)
class CostTerm:
    """One resource term: a measured quantity times an explicit operator rate."""

    key: str
    label: str
    quantity_per_class_hour: float | None
    unit: str
    rate_usd_per_unit: float | None

    def __post_init__(self) -> None:
        if self.quantity_per_class_hour is not None and (
            isinstance(self.quantity_per_class_hour, bool)
            or not math.isfinite(self.quantity_per_class_hour)
            or self.quantity_per_class_hour < 0
        ):
            raise ValueError(f"{self.key} quantity must be a non-negative number")
        if self.rate_usd_per_unit is not None and (
            isinstance(self.rate_usd_per_unit, bool)
            or not math.isfinite(self.rate_usd_per_unit)
            or self.rate_usd_per_unit <= 0
        ):
            raise ValueError(f"{self.key} rate must be a positive finite number")

    @property
    def rate_status(self) -> str:
        return NOT_VERIFIED if self.rate_usd_per_unit is None else "operator-provided"

    @property
    def cost_per_class_hour_usd(self) -> float | str:
        if self.rate_usd_per_unit is None or self.quantity_per_class_hour is None:
            return NOT_VERIFIED
        return self.quantity_per_class_hour * self.rate_usd_per_unit

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "quantity_per_class_hour": _rounded(self.quantity_per_class_hour, 9),
            "unit": self.unit,
            "rate_usd_per_unit": self.rate_usd_per_unit,
            "rate_status": self.rate_status,
            "cost_per_class_hour_usd": _rounded(self.cost_per_class_hour_usd, 9),
        }


@dataclass(frozen=True)
class CostTable:
    """The three measured terms and their cost, plus the evidence behind them."""

    slots: int
    clip_seconds: float
    fragment_seconds: int
    overlap_seconds: float
    asr_effective_minutes: float
    ffmpeg_cpu_seconds: float | None
    cpu_ratio: float | None
    wall_seconds_per_class_hour: float
    worker_rss_peak_bytes: int | None
    tmpfs_peak_bytes: int
    peak_working_set_bytes: int | None
    ram_gib_hours: float | None
    terms: tuple[CostTerm, ...]
    environment: dict[str, str]
    clean: bool

    @property
    def asr(self) -> CostTerm:
        return self._term("asr")

    @property
    def cpu(self) -> CostTerm:
        return self._term("cpu")

    @property
    def ram(self) -> CostTerm:
        return self._term("ram")

    def _term(self, key: str) -> CostTerm:
        for term in self.terms:
            if term.key == key:
                return term
        raise KeyError(key)

    @property
    def total_cost_per_class_hour_usd(self) -> float | str:
        costs = [term.cost_per_class_hour_usd for term in self.terms]
        if any(isinstance(cost, str) for cost in costs):
            return NOT_VERIFIED
        return sum(cost for cost in costs if isinstance(cost, float))

    @property
    def all_rates_provided(self) -> bool:
        return all(term.rate_status != NOT_VERIFIED for term in self.terms)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticket": TICKET,
            "test_basis": TEST_BASIS,
            "environment": self.environment,
            "load": {
                "slots": self.slots,
                "clip_seconds": self.clip_seconds,
                "fragment_cap_seconds": self.fragment_seconds,
                "overlap_seconds": self.overlap_seconds,
                "cleanup_verified": self.clean,
            },
            "measured": {
                "asr_effective_minutes_per_class_hour": round(
                    self.asr_effective_minutes, 6
                ),
                "ffmpeg_cpu_seconds_per_class_hour": _rounded(
                    self.ffmpeg_cpu_seconds, 6
                ),
                "cpu_ratio": _rounded(self.cpu_ratio, 6),
                "wall_seconds_per_class_hour": round(
                    self.wall_seconds_per_class_hour, 6
                ),
                "worker_rss_peak_bytes": self.worker_rss_peak_bytes,
                "tmpfs_peak_bytes": self.tmpfs_peak_bytes,
                "peak_working_set_bytes": self.peak_working_set_bytes,
                "ram_gib_hours_per_class_hour": _rounded(self.ram_gib_hours, 9),
            },
            "cost": {
                "terms": [term.to_dict() for term in self.terms],
                "total_cost_per_class_hour_usd": _rounded(
                    self.total_cost_per_class_hour_usd, 9
                ),
                "all_rates_provided": self.all_rates_provided,
                "rates_source": (
                    "operator-provided; the F0.1 spike did not measure an ASR price"
                ),
            },
            "notes": [
                "ASR effective minutes are measured; the provider rate is an input.",
                "ffmpeg CPU is the user+system time of the ffprobe/ffmpeg children.",
                "RAM GiB-hours are a local lower bound (RSS + tmpfs x wall time).",
                "No Riva call, no key and no audio kept; G8 is not closed by this table.",
            ],
        }

    def render_table(self) -> str:
        environment = ", ".join(
            f"{key}={value}" for key, value in self.environment.items()
        )
        lines = [
            f"{TICKET} real cost per class hour (basis for {TEST_BASIS})",
            f"Environment: {environment}",
            (
                f"Load: {self.slots} concurrent slot(s), {self.clip_seconds:.1f} s "
                f"synthetic clip, fragment cap {self.fragment_seconds} s, overlap "
                f"{self.overlap_seconds:.2f} s"
            ),
            (
                "Measured: "
                f"ASR {self.asr_effective_minutes:.3f} effective minutes/class-hour; "
                f"ffmpeg CPU {_number_text(self.ffmpeg_cpu_seconds, 3)} s/class-hour "
                f"({_number_text(self.cpu_ratio, 3)} cores); peak working set "
                f"{_bytes_text(self.peak_working_set_bytes)}; wall "
                f"{self.wall_seconds_per_class_hour:.3f} s/class-hour; RAM "
                f"{_number_text(self.ram_gib_hours, 9)} GiB-hours/class-hour"
            ),
            (
                f"Cleanup verified: {_yes_no(self.clean)}; no Riva call, no key, "
                "no audio kept"
            ),
            "",
            (
                f"{'term':<12} {'quantity/class-hour':>22} {'unit':<26} "
                f"{'rate':>24} {'cost/class-hour':>20}"
            ),
        ]
        for term in self.terms:
            lines.append(
                f"{term.key:<12} "
                f"{_number_text(term.quantity_per_class_hour, 9):>22} "
                f"{term.unit:<26} "
                f"{_rate_text(term):>24} "
                f"{_money_text(term.cost_per_class_hour_usd):>20}"
            )
        lines.extend(
            [
                (
                    f"{'total':<12} {'':>22} {'':<26} {'':>24} "
                    f"{_money_text(self.total_cost_per_class_hour_usd):>20}"
                ),
                (
                    "Rates are operator inputs, not provider measurements. "
                    "NO VERIFICADO means no number was invented."
                ),
                (
                    "200 MiB and 3 h are not approved; D4 stays blocked and this "
                    "table does not close G8."
                ),
            ]
        )
        return "\n".join(lines)


def build_cost_table(
    report: asr_cost.LoadReport,
    *,
    cpu_rate_usd_per_vcpu_hour: float | None = None,
    ram_rate_usd_per_gib_hour: float | None = None,
) -> CostTable:
    """Compose the measured load and the explicit rates into the S4.5 table."""
    cpu_seconds = report.ffmpeg_cpu_seconds_per_class_hour
    cpu_hours = None if cpu_seconds is None else cpu_seconds / SECONDS_PER_HOUR
    rss_peak = max(
        (
            measurement.rss_peak_bytes
            for measurement in report.measurements
            if measurement.rss_peak_bytes is not None
        ),
        default=None,
    )
    tmpfs_peak = max(measurement.tmpfs_peak_bytes for measurement in report.measurements)
    return CostTable(
        slots=report.slots,
        clip_seconds=report.clip_seconds,
        fragment_seconds=report.fragment_seconds,
        overlap_seconds=report.overlap_seconds,
        asr_effective_minutes=report.usage.effective_minutes_per_class_hour,
        ffmpeg_cpu_seconds=cpu_seconds,
        cpu_ratio=report.cpu_ratio,
        wall_seconds_per_class_hour=report.wall_seconds_per_class_hour,
        worker_rss_peak_bytes=rss_peak,
        tmpfs_peak_bytes=tmpfs_peak,
        peak_working_set_bytes=report.peak_working_set_bytes,
        ram_gib_hours=report.ram_gib_hours_per_class_hour,
        terms=(
            CostTerm(
                key="asr",
                label="ASR effective minutes",
                quantity_per_class_hour=report.usage.effective_minutes_per_class_hour,
                unit="ASR minute",
                rate_usd_per_unit=report.cost.rate_usd_per_minute,
            ),
            CostTerm(
                key="cpu",
                label="ffmpeg CPU",
                quantity_per_class_hour=cpu_hours,
                unit="vCPU-hour",
                rate_usd_per_unit=cpu_rate_usd_per_vcpu_hour,
            ),
            CostTerm(
                key="ram",
                label="RAM (RSS + tmpfs)",
                quantity_per_class_hour=report.ram_gib_hours_per_class_hour,
                unit="GiB-hour",
                rate_usd_per_unit=ram_rate_usd_per_gib_hour,
            ),
        ),
        environment=report.environment,
        clean=report.worker_cleanup_all_removed and report.root_cleaned,
    )


def run_cost_table(
    *,
    slots: int = PROVISIONAL_SLOTS,
    clip_seconds: float = DEFAULT_CLIP_SECONDS,
    fragment_seconds: int | None = None,
    asr_rate_usd_per_minute: float | None = None,
    cpu_rate_usd_per_vcpu_hour: float | None = None,
    ram_rate_usd_per_gib_hour: float | None = None,
    worker_timeout_s: float = asr_cost.DEFAULT_WORKER_TIMEOUT_S,
    tmpfs: Path | None = None,
) -> CostTable:
    report = asr_cost.run_load(
        slots=slots,
        clip_seconds=clip_seconds,
        fragment_seconds=fragment_seconds,
        rate_usd_per_minute=asr_rate_usd_per_minute,
        worker_timeout_s=worker_timeout_s,
        tmpfs=tmpfs,
    )
    return build_cost_table(
        report,
        cpu_rate_usd_per_vcpu_hour=cpu_rate_usd_per_vcpu_hour,
        ram_rate_usd_per_gib_hour=ram_rate_usd_per_gib_hour,
    )


def _bounded_float(minimum: float, maximum: float) -> Any:
    def _parse(value: str) -> float:
        try:
            parsed = float(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("must be a number") from exc
        if not math.isfinite(parsed) or not minimum <= parsed <= maximum:
            raise argparse.ArgumentTypeError(f"must be between {minimum} and {maximum}")
        return parsed

    return _parse


def _positive_rate(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite number")
    return parsed


def _bounded_int(minimum: int, maximum: int) -> Any:
    def _parse(value: str) -> int:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("must be an integer") from exc
        if not minimum <= parsed <= maximum:
            raise argparse.ArgumentTypeError(f"must be between {minimum} and {maximum}")
        return parsed

    return _parse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cost_table.py",
        description=(
            "Measure the ASR, ffmpeg CPU and RAM cost of a class hour. Never calls "
            "Riva; synthetic audio in RAM only."
        ),
    )
    parser.add_argument(
        "--slots",
        type=_bounded_int(1, asr_cost.MAX_SLOTS),
        default=PROVISIONAL_SLOTS,
        help=f"concurrent slots (default: {PROVISIONAL_SLOTS})",
    )
    parser.add_argument(
        "--clip-seconds",
        type=_bounded_float(
            asr_cost.MIN_CLIP_SECONDS, asr_cost.MAX_CLIP_SECONDS
        ),
        default=DEFAULT_CLIP_SECONDS,
        help=f"synthetic clip seconds (default: {DEFAULT_CLIP_SECONDS})",
    )
    parser.add_argument(
        "--fragment-seconds",
        type=_bounded_int(1, 30),
        default=None,
        help="fragment cap; values above the observed 30 s ceiling are rejected",
    )
    parser.add_argument(
        "--asr-rate-usd-per-minute",
        type=_positive_rate,
        default=None,
        help="explicit ASR rate; omit to keep the ASR cost NO VERIFICADO",
    )
    parser.add_argument(
        "--cpu-rate-usd-per-vcpu-hour",
        type=_positive_rate,
        default=None,
        help="explicit vCPU-hour rate; omit to keep the CPU cost NO VERIFICADO",
    )
    parser.add_argument(
        "--ram-rate-usd-per-gib-hour",
        type=_positive_rate,
        default=None,
        help="explicit GiB-hour rate; omit to keep the RAM cost NO VERIFICADO",
    )
    parser.add_argument(
        "--worker-timeout-s",
        type=_bounded_float(10, 600),
        default=asr_cost.DEFAULT_WORKER_TIMEOUT_S,
        help=f"per-run worker timeout (default: {asr_cost.DEFAULT_WORKER_TIMEOUT_S})",
    )
    parser.add_argument(
        "--tmpfs",
        type=Path,
        default=None,
        help="writable tmpfs directory; defaults to TMPDIR or /dev/shm",
    )
    parser.add_argument(
        "--format",
        choices=("table", "json"),
        default="table",
        help="output format (default: table)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        table = run_cost_table(
            slots=args.slots,
            clip_seconds=args.clip_seconds,
            fragment_seconds=args.fragment_seconds,
            asr_rate_usd_per_minute=args.asr_rate_usd_per_minute,
            cpu_rate_usd_per_vcpu_hour=args.cpu_rate_usd_per_vcpu_hour,
            ram_rate_usd_per_gib_hour=args.ram_rate_usd_per_gib_hour,
            worker_timeout_s=args.worker_timeout_s,
            tmpfs=args.tmpfs,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"cost_table: {exc}", file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps(table.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(table.render_table())
    return 0


def _rounded(value: float | str | None, digits: int) -> float | str | None:
    if value is None or isinstance(value, str):
        return value
    return round(value, digits)


def _number_text(value: float | None, digits: int) -> str:
    if value is None:
        return NOT_VERIFIED
    return f"{value:.{digits}f}"


def _rate_text(term: CostTerm) -> str:
    if term.rate_usd_per_unit is None:
        return NOT_VERIFIED
    return f"{term.rate_usd_per_unit} USD/{term.unit}"


def _money_text(value: float | str) -> str:
    if isinstance(value, str):
        return value
    return f"{value:.9f} USD"


def _bytes_text(value: int | None) -> str:
    if value is None:
        return NOT_VERIFIED
    return f"{value / (1024 * 1024):.2f} MiB"


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


if __name__ == "__main__":
    raise SystemExit(main())

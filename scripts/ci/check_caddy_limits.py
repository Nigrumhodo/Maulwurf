"""C-S2-JF-13 (J2.10): static assertions on the Caddy edge limits.

The live measurement is `scripts/load/ingress_limits.sh` and needs Docker (or a Caddy
binary). This check runs everywhere, catches a regression that removes the limits, and
pins the invariant that the upload ceiling stays *above* the API contract limit so the
API, not Caddy, answers a request that exceeds the published limit with the structured
`payload_too_large` envelope.

Usage:
    python3 scripts/ci/check_caddy_limits.py [--caddyfile PATH]
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CADDYFILE = REPO_ROOT / "infra" / "Caddyfile"

# Provisional default in apps/api/app/core/config.py (S1.A8/A2.2); D4 has not approved
# a limit, so the published `capabilities` value is still null.
API_UPLOAD_MAX_BYTES = 209_715_200
# The API keeps the upload reservation for 15 minutes (provisional `upload_ttl_minutes`).
API_UPLOAD_TTL_SECONDS = 15 * 60

_SIZE_UNITS = {
    "": 1,
    "b": 1,
    "kb": 1_000,
    "mb": 1_000_000,
    "gb": 1_000_000_000,
    "tb": 1_000_000_000_000,
    "kib": 1 << 10,
    "mib": 1 << 20,
    "gib": 1 << 30,
    "tib": 1 << 40,
}
_DURATION_UNITS = {"ms": 0.001, "s": 1, "m": 60, "h": 3_600, "d": 86_400}


class CheckError(Exception):
    """A Caddyfile assertion failed."""


@dataclass(frozen=True)
class ServerTimeouts:
    read_body: float
    read_header: float
    write: float
    idle: float


@dataclass(frozen=True)
class Report:
    timeouts: ServerTimeouts
    upload_max_bytes: int
    api_max_bytes: int


def _fail(message: str) -> None:
    raise CheckError(message)


def _lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        _fail(f"cannot read {path}: {exc}")
        raise AssertionError("unreachable") from exc


def _strip_comment(line: str) -> str:
    # Caddyfile comments start with `#`; the tracked file never puts one inside a token.
    return line.split("#", 1)[0].rstrip()


def _block(lines: list[str], opener: str) -> list[str]:
    """Return the lines inside the first block whose stripped line starts with `opener`."""
    for start, raw in enumerate(lines):
        if _strip_comment(raw).strip().startswith(opener):
            depth = 0
            body: list[str] = []
            for line in lines[start:]:
                code = _strip_comment(line)
                depth += code.count("{") - code.count("}")
                if depth <= 0 and body:
                    return body
                if depth > 0:
                    body.append(line)
            break
    _fail(f"missing block `{opener}`")
    raise AssertionError("unreachable")


def _assignments(lines: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in lines:
        code = _strip_comment(raw).strip()
        if not code or "{" in code or "}" in code:
            continue
        parts = code.split()
        if len(parts) == 2:
            values[parts[0]] = parts[1]
        elif len(parts) == 1 and parts[0] == "0":
            values[parts[0]] = "0"
    return values


def parse_size(value: str) -> int:
    match = re.fullmatch(r"(\d+)\s*([a-zA-Z]*)", value.strip())
    if match is None:
        _fail(f"unrecognized size: {value!r}")
    number, unit = match.groups()
    multiplier = _SIZE_UNITS.get(unit.lower())
    if multiplier is None:
        _fail(f"unrecognized size unit: {value!r}")
    return int(number) * multiplier


def parse_duration(value: str) -> float:
    if value == "0":
        return 0.0
    match = re.fullmatch(r"(\d+)(ms|s|m|h|d)", value.strip())
    if match is None:
        _fail(f"unrecognized duration: {value!r}")
    number, unit = match.groups()
    return int(number) * _DURATION_UNITS[unit]


def _timeouts(lines: list[str]) -> ServerTimeouts:
    servers = _block(lines, "servers")
    values = _assignments(_block(servers, "timeouts"))
    missing = {"read_body", "read_header", "write", "idle"} - values.keys()
    if missing:
        _fail(f"incomplete servers.timeouts; missing {sorted(missing)}")
    return ServerTimeouts(
        read_body=parse_duration(values["read_body"]),
        read_header=parse_duration(values["read_header"]),
        write=parse_duration(values["write"]),
        idle=parse_duration(values["idle"]),
    )


def _upload_ceiling(lines: list[str]) -> int:
    upload = _block(lines, "handle @upload")
    body = _block(upload, "request_body")
    values = _assignments(body)
    if "max_size" not in values:
        _fail("the @upload handler does not declare request_body.max_size")
    return parse_size(values["max_size"])


def _api_ceiling(lines: list[str]) -> int:
    api = _block(lines, "handle @api")
    body = _block(api, "request_body")
    values = _assignments(body)
    if "max_size" not in values:
        _fail("the @api handler does not declare request_body.max_size")
    return parse_size(values["max_size"])


def check(caddyfile: Path) -> Report:
    lines = _lines(caddyfile)
    joined = "\n".join(_strip_comment(line) for line in lines)

    if "admin off" not in joined:
        _fail("missing `admin off`")
    if "tls internal" not in joined:
        _fail("missing `tls internal`")
    if "@internal" not in joined or "respond 404" not in joined:
        _fail("the /internal/* route must stay unexposed (ADR-0008)")

    timeouts = _timeouts(lines)
    upload = _upload_ceiling(lines)
    api = _api_ceiling(lines)

    # J2.10: the edge ceiling is an abuse guard, not the contract. It must stay above
    # the API limit so the structured 413 envelope is the one the client sees.
    if upload <= API_UPLOAD_MAX_BYTES:
        _fail(
            "request_body.max_size for @upload "
            f"({upload}) must exceed the API limit ({API_UPLOAD_MAX_BYTES})"
        )
    if api >= upload:
        _fail("request_body.max_size for @api must be lower than the @upload one")
    if timeouts.read_body < API_UPLOAD_TTL_SECONDS:
        _fail(
            f"read_body ({timeouts.read_body:g}s) must cover the API upload window "
            f"({API_UPLOAD_TTL_SECONDS}s)"
        )
    if timeouts.read_header < 1 or timeouts.read_header > 60:
        _fail("read_header must be between 1s and 60s")
    if timeouts.idle < 60:
        _fail("idle must be at least 1m")
    # A total write deadline would cut `/audios/*/progress` (SSE, `flush_interval -1`).
    if timeouts.write != 0:
        _fail("write must be 0 (no total deadline) so SSE streams are not cut")

    return Report(timeouts=timeouts, upload_max_bytes=upload, api_max_bytes=api)


def summarize(report: Report) -> str:
    timeouts = report.timeouts
    return (
        f"servers.timeouts: read_body={timeouts.read_body:g}s "
        f"read_header={timeouts.read_header:g}s write={timeouts.write:g}s "
        f"idle={timeouts.idle:g}s | @upload max_size={report.upload_max_bytes} "
        f"| @api max_size={report.api_max_bytes} "
        f"| API provisional={API_UPLOAD_MAX_BYTES}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--caddyfile", type=Path, default=DEFAULT_CADDYFILE)
    parser.add_argument(
        "--print-sizes",
        action="store_true",
        help=(
            "print '<upload_max_bytes> <api_json_max_bytes> "
            "<api_contract_max_bytes>' and exit"
        ),
    )
    args = parser.parse_args()
    try:
        report = check(args.caddyfile)
    except CheckError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    if args.print_sizes:
        print(f"{report.upload_max_bytes} {report.api_max_bytes} {API_UPLOAD_MAX_BYTES}")
        return 0
    print(f"Caddyfile: {args.caddyfile}")
    print(summarize(report))
    print("OK: C-S2-JF-13 static checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

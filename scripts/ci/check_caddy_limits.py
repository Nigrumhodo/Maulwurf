"""C-S2-JF-13 (J2.10): static assertions on the Caddy edge limits.

The live measurement is `scripts/load/ingress_limits.sh` and needs Docker (or a Caddy
binary). This check runs everywhere, catches a regression that removes the limits, and
pins three invariants:

* the upload ceiling stays *above* the API contract limit so the API, not Caddy,
  answers a request that exceeds the published limit with the structured
  `payload_too_large` envelope;
* every `handle` block that proxies declares `request_body.max_size`, including the
  catch-all web fallback that receives unknown routes;
* the `/internal/*` guard is a real `handle @internal { respond 404 }` block placed
  before the catch-all handler, not a stray matcher or comment.

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
class HandleBlock:
    opener: str
    body: tuple[str, ...]
    start: int


@dataclass(frozen=True)
class Report:
    timeouts: ServerTimeouts
    upload_max_bytes: int
    api_max_bytes: int
    fallback_max_bytes: int
    proxy_handle_count: int


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


def _find_block(lines: list[str], opener: str) -> list[str] | None:
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
            return None
    return None


def _block(lines: list[str], opener: str) -> list[str]:
    body = _find_block(lines, opener)
    if body is None:
        _fail(f"missing block `{opener}`")
    return body


def _handle_blocks(lines: list[str]) -> list[HandleBlock]:
    """Return every `handle` directive block with its opening line and source index."""
    blocks: list[HandleBlock] = []
    for start, raw in enumerate(lines):
        code = _strip_comment(raw).strip()
        is_handle = code == "handle {" or (
            code.startswith("handle ") and code.endswith("{")
        )
        if not is_handle:
            continue
        depth = 0
        for end in range(start, len(lines)):
            line = _strip_comment(lines[end])
            depth += line.count("{") - line.count("}")
            if depth == 0 and end > start:
                blocks.append(HandleBlock(code, tuple(lines[start + 1 : end]), start))
                break
        else:
            _fail(f"unclosed block `{code}`")
    if not blocks:
        _fail("missing `handle` blocks")
    return blocks


def _body_max_size(lines: list[str] | tuple[str, ...]) -> int | None:
    body = _find_block(list(lines), "request_body")
    if body is None:
        return None
    value = _assignments(body).get("max_size")
    return None if value is None else parse_size(value)


def _proxy_limits(blocks: list[HandleBlock]) -> dict[str, int]:
    limits: dict[str, int] = {}
    for block in blocks:
        proxies = any(
            _strip_comment(line).strip().startswith("reverse_proxy")
            for line in block.body
        )
        if not proxies:
            continue
        limit = _body_max_size(block.body)
        if limit is None:
            _fail(f"`{block.opener}` proxies without request_body.max_size")
        if limit <= 0:
            _fail(f"`{block.opener}` declares a non-positive request_body.max_size")
        if block.opener in limits:
            _fail(f"duplicate `handle` block `{block.opener}`")
        limits[block.opener] = limit
    if not limits:
        _fail("no proxying `handle` blocks found")
    return limits


def _internal_guard(lines: list[str], blocks: list[HandleBlock]) -> None:
    matchers = [
        _strip_comment(raw).strip()
        for raw in lines
        if _strip_comment(raw).strip().startswith("@internal")
    ]
    if matchers != ["@internal path /internal/*"]:
        _fail(
            "the internal matcher must be exactly `@internal path /internal/*` (ADR-0008)"
        )

    guards = [block for block in blocks if block.opener == "handle @internal {"]
    if len(guards) != 1:
        _fail("missing or duplicated `handle @internal` block (ADR-0008)")
    guard = guards[0]
    statements = [
        _strip_comment(line).strip()
        for line in guard.body
        if _strip_comment(line).strip()
    ]
    if statements != ["respond 404"]:
        _fail("`handle @internal` must contain only `respond 404` (ADR-0008)")

    fallbacks = [block for block in blocks if block.opener == "handle {"]
    if len(fallbacks) != 1:
        _fail("exactly one catch-all `handle` block is required")
    if guard.start > fallbacks[0].start:
        _fail("`handle @internal` must precede the catch-all web handle (ADR-0008)")


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


def check(caddyfile: Path) -> Report:
    lines = _lines(caddyfile)
    joined = "\n".join(_strip_comment(line) for line in lines)

    if "admin off" not in joined:
        _fail("missing `admin off`")
    if "tls internal" not in joined:
        _fail("missing `tls internal`")

    blocks = _handle_blocks(lines)
    _internal_guard(lines, blocks)
    proxy_limits = _proxy_limits(blocks)

    upload = proxy_limits.get("handle @upload {")
    if upload is None:
        _fail("missing `handle @upload` proxy route")
    api = proxy_limits.get("handle @api {")
    if api is None:
        _fail("missing `handle @api` proxy route")
    fallback = proxy_limits["handle {"]

    timeouts = _timeouts(lines)

    # J2.10: the edge ceiling is an abuse guard, not the contract. It must stay above
    # the API limit so the structured 413 envelope is the one the client sees.
    if upload <= API_UPLOAD_MAX_BYTES:
        _fail(
            "request_body.max_size for @upload "
            f"({upload}) must exceed the API limit ({API_UPLOAD_MAX_BYTES})"
        )
    if api >= upload:
        _fail("request_body.max_size for @api must be lower than the @upload one")
    # Unknown routes (including POST /search and /chat/*) reach the web fallback.
    if fallback > api:
        _fail("the catch-all web handler ceiling must not exceed the @api ceiling")
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

    return Report(
        timeouts=timeouts,
        upload_max_bytes=upload,
        api_max_bytes=api,
        fallback_max_bytes=fallback,
        proxy_handle_count=len(proxy_limits),
    )


def summarize(report: Report) -> str:
    timeouts = report.timeouts
    return (
        f"servers.timeouts: read_body={timeouts.read_body:g}s "
        f"read_header={timeouts.read_header:g}s write={timeouts.write:g}s "
        f"idle={timeouts.idle:g}s | @upload max_size={report.upload_max_bytes} "
        f"| @api max_size={report.api_max_bytes} "
        f"| web fallback max_size={report.fallback_max_bytes} "
        f"| proxying handles={report.proxy_handle_count} "
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
        print(
            f"{report.upload_max_bytes} {report.api_max_bytes} {API_UPLOAD_MAX_BYTES}"
        )
        return 0
    print(f"Caddyfile: {args.caddyfile}")
    print(summarize(report))
    print("OK: C-S2-JF-13 static checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

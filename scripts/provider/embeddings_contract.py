"""P-S2-JL-10 (L2.2): real contract of the embeddings provider — feeds D5 and D3-Texto.

Protected provider run: **never** in an MR (CI runs ``-m "not provider"``) and **never**
without the lead's explicit authorization and an approved budget (guardrail 4). A mock is
not evidence of the provider contract (catalogue §0.4.4).

What it observes, with no retries, no parallel calls and no quota burst:

===========  =====================================================================
P1           dimension really returned vs the registered ``1536``, and the model echo
P2           input-token limit: one overlong text, ``400`` expected
P3           batch size tolerated (8 and 16 texts)
P4           ``429``/``Retry-After`` only if it arrives naturally; never provoked
P5           latency per request (n <= 10, sequential) — non-sensitive percentiles
P6           ``usage.total_tokens`` from the observed responses (no account names)
P7           report to ``docs/spike/D5-embeddings-contrato.md`` + JSON on stdout
===========  =====================================================================

The key is read from the environment (or the repo-root ``.env``) and never printed: the
report is refused if it contains the key, the probe texts, an ``Authorization`` header
value or a ``Bearer`` value. Class text is never sent: every probe uses a fixed synthetic
phrase.

Run only with an authorized key and a bounded budget::

    cd apps/api && MAULWURF_EMBEDDING_API_KEY=... uv run python \
        ../scripts/provider/embeddings_contract.py
"""

from __future__ import annotations

import importlib.metadata
import json
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_PROVIDER = Path(__file__).resolve().parent
if str(_PROVIDER) not in sys.path:
    sys.path.insert(0, str(_PROVIDER))

import riva_spike  # noqa: E402

NOT_VERIFIED = riva_spike.NOT_VERIFIED
REPORT_RELATIVE_PATH = Path("docs") / "spike" / "D5-embeddings-contrato.md"
PROBE_PHRASE = "contrato de embeddings de maulwurf"
OVERLONG_REPEAT = 40_000
LATENCY_SAMPLES = 5
BATCH_SIZES = (8, 16)
REQUEST_TIMEOUT_S = 60.0
ERROR_SNIPPET_CHARS = 160
MIN_REDACTION_TEXT_CHARS = 24
SEPARATOR = "-" * 100


class ProbeConfigError(Exception):
    """Local configuration problem. The message must not contain a secret."""


@dataclass(frozen=True)
class ProbeResult:
    """Raw outcome of one request: verbatim status, headers and body shape."""

    status: int
    latency_s: float
    dimension_observed: int | None = None
    model_observed: str | None = None
    vector_count: int | None = None
    total_tokens: int | None = None
    retry_after: str | None = None
    error_message: str | None = None
    transport_error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == 200 and self.transport_error is None


@dataclass
class ProbeLog:
    """Everything the report is derived from; nothing here is class text."""

    results: list[tuple[str, ProbeResult]] = field(default_factory=list)
    texts_used: list[str] = field(default_factory=list)

    def record(self, probe: str, *, texts: Sequence[str], result: ProbeResult) -> None:
        self.results.append((probe, result))
        self.texts_used.extend(texts)


def percentile(values: Sequence[float], quantile: float) -> float | None:
    """Nearest-rank percentile of a small sample; ``None`` when there is no sample."""
    ordered = sorted(values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    position = round(quantile * (len(ordered) - 1))
    return ordered[min(max(position, 0), len(ordered) - 1)]


def distribution_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return NOT_VERIFIED


def host_of(base_url: str) -> str:
    """Scheme and host only: a configured path can carry an account, and that is not ours."""
    without_scheme = base_url.split("://", 1)[-1]
    return without_scheme.split("/", 1)[0]


def report_is_redacted(payload: str, *, api_key: str, texts: Sequence[str]) -> bool:
    """Refuse a report that leaks the key, the probe texts or an auth header value.

    Short probes are ignored: a two-word phrase appears in prose by accident, and flagging
    it would refuse every honest report. The guard is about echoed class text, which is long.
    """
    if api_key and api_key in payload:
        return False
    for text in texts:
        if len(text) >= MIN_REDACTION_TEXT_CHARS and text in payload:
            return False
    lowered = payload.casefold()
    return "authorization" not in lowered and "bearer " not in lowered


def build_report(
    *,
    log: ProbeLog,
    model_registered: str,
    dimension_registered: int,
    base_url: str,
    started_at: str,
    command: str,
) -> dict[str, Any]:
    """Summary for D5/D3-Texto. A single 200 is a floor observed, never a maximum."""
    first = next((result for probe, result in log.results if probe == "P1"), None)
    dimension_observed = first.dimension_observed if first is not None else None
    model_observed = first.model_observed if first is not None else None
    overlong = next((result for probe, result in log.results if probe == "P2"), None)
    batches = [result for probe, result in log.results if probe == "P3" and result.ok]
    rate_limited = next((result for probe, result in log.results if result.status == 429), None)
    latencies = [result.latency_s for _, result in log.results if result.status is not None]
    tokens = sum(result.total_tokens or 0 for _, result in log.results if result.ok)
    return {
        "test_id": "P-S2-JL-10",
        "ticket": "L2.2",
        "started_at": started_at,
        "command": command,
        "environment": {
            "host": host_of(base_url),
            "python": sys.version.split()[0],
            "httpx": distribution_version("httpx"),
        },
        "model_registered": model_registered,
        "dimension_registered": dimension_registered,
        "dimension_observed": dimension_observed if first and first.ok else NOT_VERIFIED,
        "model_echo": model_observed or NOT_VERIFIED,
        "model_echo_matches": model_observed == model_registered,
        "input_limit_behavior": (
            "400_on_overlong" if overlong is not None and overlong.status == 400 else NOT_VERIFIED
        ),
        "max_batch_observed": max(
            (result.vector_count or 0 for result in batches), default=NOT_VERIFIED
        ),
        "batch_notes": "minimum observed, not the provider maximum",
        "rate_limit_observed": NOT_VERIFIED if rate_limited is None else 429,
        "retry_after_observed": (
            NOT_VERIFIED
            if rate_limited is None or rate_limited.retry_after is None
            else rate_limited.retry_after
        ),
        "latency_s": {
            "n": len(latencies),
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "max": max(latencies) if latencies else None,
        },
        "total_tokens_observed": tokens,
        "cost_note": f"{tokens} tokens observed in this run; no account names recorded",
        "quota": NOT_VERIFIED,
        "feeds": ["D5", "D3-Texto"],
        "rows": [{"probe": probe, **_result_row(result)} for probe, result in log.results],
    }


def _result_row(result: ProbeResult) -> dict[str, Any]:
    row: dict[str, Any] = {
        "status": result.status,
        "latency_s": round(result.latency_s, 4),
    }
    if result.ok:
        row["dimension_observed"] = result.dimension_observed
        row["vector_count"] = result.vector_count
        row["total_tokens"] = result.total_tokens
    if result.retry_after is not None:
        row["retry_after"] = result.retry_after
    if result.error_message is not None:
        row["error_message"] = result.error_message
    if result.transport_error is not None:
        row["transport_error"] = result.transport_error
    return row


def render_markdown(report: dict[str, Any]) -> str:
    """Evidence sheet for ``docs/spike/``: date, environment, command and result.

    English prose (canonical documentation); the literal ``NO VERIFICADO`` is kept verbatim
    because it is data the report scripts emit, not a comment.
    """
    lines = [
        "# D5 — embeddings provider contract (P-S2-JL-10)",
        "",
        "Evidence from `P-S2-JL-10` (ticket L2.2) feeding **D5** (model/dimension/cost) and",
        "the **D3-Texto** sheet. Run with an authorized credential and a bounded budget; no",
        "retries, no parallel calls and no provoked quota. A mock is not contract evidence.",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Date (UTC) | {report['started_at']} |",
        f"| Command | `{report['command']}` |",
        f"| Host | {report['environment']['host']} |",
        f"| Python / httpx | {report['environment']['python']} / "
        f"{report['environment']['httpx']} |",
        f"| Model registered | {report['model_registered']} |",
        f"| Dimension registered | {report['dimension_registered']} |",
        f"| Dimension observed | {report['dimension_observed']} |",
        f"| Model echo | {report['model_echo']} (matches: {report['model_echo_matches']}) |",
        f"| Input limit | {report['input_limit_behavior']} |",
        f"| Max batch observed | {report['max_batch_observed']} ({report['batch_notes']}) |",
        f"| 429 observed | {report['rate_limit_observed']} |",
        f"| `Retry-After` observed | {report['retry_after_observed']} |",
        f"| Latency p50 / p95 (s) | {report['latency_s']['p50']} / "
        f"{report['latency_s']['p95']} (n={report['latency_s']['n']}) |",
        f"| Tokens observed | {report['total_tokens_observed']} |",
        f"| Quota | {report['quota']} |",
        "",
        "## Notes",
        "",
        f"- Cost: {report['cost_note']}.",
        "- The price per 1M tokens comes from the provider's published price list at execution",
        "  time; this sheet only records the tokens observed.",
        "- `429`/`Retry-After` are recorded only when they arrive naturally: the script never",
        "  provokes quota. `NO VERIFICADO` means not observed, not that it does not exist.",
        "- The max batch observed is a **minimum** tolerated (8 and 16 texts), not the provider",
        "  maximum; publishing the maximum needs a separate authorized probe.",
        "",
        "## Probe",
        "",
        "| Probe | Status | Latency (s) | Dimension | Tokens | Note |",
        "|---|---|---|---|---|---|",
    ]
    for row in report["rows"]:
        note = _table_note(row)
        lines.append(
            f"| {row['probe']} | {row['status']} | {row['latency_s']} | "
            f"{row.get('dimension_observed', NOT_VERIFIED)} | "
            f"{row.get('total_tokens', NOT_VERIFIED)} | {note} |"
        )
    lines.extend(
        [
            "",
            "## For D5 / D3-Texto",
            "",
            "This sheet **feeds** the decisions; it does not close them. The lead records the",
            "closure in `docs/ARCH.md` §7 and, if the embedding model takes part in real text,",
            "the **D3-Texto** sheet must be closed before indexing class transcripts",
            "(`docs/plan/S2.md` §0.6).",
            "",
        ]
    )
    return "\n".join(lines)


def _post(
    client: Any,
    endpoint: str,
    *,
    api_key: str,
    model: str,
    texts: Sequence[str],
) -> ProbeResult:
    started = time.monotonic()
    try:
        response = client.post(
            endpoint,
            json={"input": list(texts), "model": model},
            headers={"Authorization": f"Bearer {api_key}"},
        )
    except Exception as exc:  # noqa: BLE001 - any network failure is a result, not a crash
        return ProbeResult(
            status=0,
            latency_s=time.monotonic() - started,
            transport_error=type(exc).__name__,
        )
    latency = time.monotonic() - started
    return _parse(response, latency=latency)


def _parse(response: Any, *, latency: float) -> ProbeResult:
    message = None
    if response.status_code != 200:
        message = _snippet(response.text)
    dimension = None
    model_observed = None
    vector_count = None
    total_tokens = None
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        items = body.get("data")
        if isinstance(items, list):
            vector_count = len(items)
            first = items[0] if items else None
            if isinstance(first, dict) and isinstance(first.get("embedding"), list):
                dimension = len(first["embedding"])
        if isinstance(body.get("model"), str):
            model_observed = body["model"]
        usage = body.get("usage")
        if isinstance(usage, dict) and isinstance(usage.get("total_tokens"), int):
            total_tokens = usage["total_tokens"]
    return ProbeResult(
        status=response.status_code,
        latency_s=latency,
        dimension_observed=dimension,
        model_observed=model_observed,
        vector_count=vector_count,
        total_tokens=total_tokens,
        retry_after=response.headers.get("Retry-After"),
        error_message=message,
    )


def _snippet(text: str) -> str:
    collapsed = " ".join(text.split())
    return collapsed[:ERROR_SNIPPET_CHARS]


def _table_note(row: dict[str, Any]) -> str:
    """Short cell for the evidence table; the full message stays in the JSON report."""
    if row.get("retry_after") is not None:
        return f"Retry-After: {row['retry_after']}"
    note = str(row.get("error_message") or row.get("transport_error") or "")
    note = " ".join(note.split())
    return note[:40] + ("…" if len(note) > 40 else "")


def run_probes(endpoint: str, *, api_key: str, model: str) -> ProbeLog:
    """Sequential, bounded probes. No retries, no parallelism, no quota burst."""
    import httpx

    log = ProbeLog()
    with httpx.Client(timeout=REQUEST_TIMEOUT_S) as client:
        base_texts = [PROBE_PHRASE]
        log.record(
            "P1",
            texts=base_texts,
            result=_post(client, endpoint, api_key=api_key, model=model, texts=base_texts),
        )
        overlong = [(PROBE_PHRASE + " ") * OVERLONG_REPEAT]
        log.record(
            "P2",
            texts=[],
            result=_post(client, endpoint, api_key=api_key, model=model, texts=overlong),
        )
        for size in BATCH_SIZES:
            batch = [f"{PROBE_PHRASE} {index}" for index in range(size)]
            log.record(
                "P3",
                texts=batch,
                result=_post(client, endpoint, api_key=api_key, model=model, texts=batch),
            )
        for _ in range(LATENCY_SAMPLES):
            log.record(
                "P5",
                texts=base_texts,
                result=_post(client, endpoint, api_key=api_key, model=model, texts=base_texts),
            )
    return log


def main(argv: list[str] | None = None, dotenv_path: Path | None = None) -> int:
    args = list(sys.argv if argv is None else argv)
    if len(args) > 1:
        print(
            "embeddings_contract.py accepts no arguments; set "
            "MAULWURF_EMBEDDING_BASE_URL and MAULWURF_EMBEDDING_API_KEY instead",
            file=sys.stderr,
        )
        return 2
    env_path = riva_spike.repo_root() / ".env" if dotenv_path is None else dotenv_path
    riva_spike.load_dotenv(env_path)
    try:
        endpoint, api_key, model, dimension = _require_settings()
    except ProbeConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    started_at = datetime.now(UTC).isoformat(timespec="seconds")
    log = run_probes(endpoint, api_key=api_key, model=model)
    report = build_report(
        log=log,
        model_registered=model,
        dimension_registered=dimension,
        base_url=endpoint,
        started_at=started_at,
        command="uv run python ../scripts/provider/embeddings_contract.py",
    )
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if not report_is_redacted(payload, api_key=api_key, texts=log.texts_used):
        print("refusing to print a report that contains the key or probe text", file=sys.stderr)
        return 2
    path = riva_spike.repo_root() / REPORT_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(report) + "\n", encoding="utf-8")
    print(payload)
    print(f"{SEPARATOR}\nreport written to {path.relative_to(riva_spike.repo_root())}")
    return 0


def _require_settings() -> tuple[str, str, str, int]:
    from app.core.config import settings

    if settings.embedding_base_url is None or settings.embedding_api_key is None:
        raise ProbeConfigError(
            "MAULWURF_EMBEDDING_BASE_URL and MAULWURF_EMBEDDING_API_KEY are required "
            "in the environment or the repo-root .env"
        )
    base_url = str(settings.embedding_base_url).rstrip("/")
    return (
        f"{base_url}/embeddings",
        settings.embedding_api_key.get_secret_value(),
        settings.embedding_model,
        settings.embedding_dimension,
    )


if __name__ == "__main__":
    raise SystemExit(main())

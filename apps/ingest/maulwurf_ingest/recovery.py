"""Operator recovery for failed cleanup and host loss (S4.1, I-S4-SG-03).

The executable runbook lives in ``docs/runbooks/cleanup.md``. This module is the part
that runs against the database and the attempt tmpfs; it never reads audio, transcript
text, prompts, or credentials.

Two operations are deliberately separate:

- ``sweep_once`` repeats the startup recovery pass (``reconcile_committed`` and
  ``sweep_stranded``). It never retranscribes and never takes a live lease.
- ``resolve_attempts`` records an operator attestation for attempts whose audio was
  destroyed with a host or a container. It refuses to write without a destruction
  evidence file and only writes ``cleanup_status = 'verified'`` for attempts whose last
  heartbeat is covered by that evidence.

The service keeps its admission latch in memory. A failed cleanup discovered by this
external process must therefore be followed by an ingest restart (the runbook contains
that step); the persisted row makes the restarted process close admission.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import tempfile
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path

import asyncpg

from maulwurf_ingest import hardening, metrics, rls
from maulwurf_ingest.config import settings
from maulwurf_ingest.logs import configure_logging
from maulwurf_ingest.reconciler import (
    ReconcileAction,
    ReconcileResult,
    local_workdirs,
    reconcile_committed,
    sweep_stranded,
)

# The runbook's recurring pass. S4.2 owns moving this interval into the supervisor; until
# then the operator process is the only 60 s sweeper.
SWEEP_INTERVAL_S = 60.0
# Database and operator clocks can differ by a few seconds. A minute of skew cannot hide an
# attempt that was created after the reported destruction, and the explicit attempt ids
# remain the primary control.
CLOCK_SKEW_TOLERANCE = timedelta(seconds=60)
EVIDENCE_MAX_BYTES = 8192
ATTESTATION_SOURCE = "operator_attestation"

EVIDENCE_KEYS = frozenset({
    "host",
    "destroyed_at",
    "memory_destroyed",
    "tmpfs_mount_destroyed",
    "no_snapshots",
    "no_swap",
    "reviewer",
    "reference",
})

PENDING_RESOLVABLE_STATUSES = frozenset({
    "receiving",
    "transcribing",
    "transcript_committed_cleanup_pending",
    # A restart already swept a stranded receiving/transcribing attempt. Its cleanup can
    # still be attested with the same host-loss evidence; ``awaiting_upload`` is the only
    # state that cannot have written bytes.
    "requires_reupload",
    "rejected",
    "cancelled",
    "succeeded",
})

FAILED_SQL = """
SELECT id
  FROM ingestion_attempts
 WHERE cleanup_status = 'failed'
 ORDER BY updated_at, id
"""

COMMITTED_PENDING_SQL = """
SELECT id
  FROM ingestion_attempts
 WHERE status = 'transcript_committed_cleanup_pending'
   AND cleanup_status IN ('pending', 'verified')
   AND (lease_expires_at IS NULL OR lease_expires_at <= now())
 ORDER BY updated_at, id
"""

STRANDED_SQL = """
SELECT id
  FROM ingestion_attempts
 WHERE status IN ('receiving', 'transcribing')
   AND lease_expires_at <= now()
 ORDER BY updated_at, id
"""

RESOLVE_ROWS_SQL = """
SELECT id, user_id, status, cleanup_status, fencing_token, heartbeat_at, updated_at,
       lease_expires_at
  FROM ingestion_attempts
 WHERE id = ANY($1::uuid[])
 ORDER BY id
"""

RESOLVE_UPDATE_SQL = """
UPDATE ingestion_attempts
   SET cleanup_status = 'verified',
       cleanup_evidence = $4::jsonb,
       cleanup_verified_at = now(),
       audio_deleted_at = now(),
       updated_at = now()
 WHERE id = $1 AND user_id = $2 AND fencing_token = $3
   AND cleanup_status = $5
   AND (lease_expires_at IS NULL OR lease_expires_at <= now())
RETURNING id
"""

EXIT_OK = 0
EXIT_ERROR = 2
EXIT_ATTENTION = 3


class RecoveryError(RuntimeError):
    """The operator input or the database state cannot be used safely."""


class EvidenceError(RecoveryError):
    """The destruction attestation is incomplete or malformed."""


@dataclass(frozen=True)
class DestructionEvidence:
    """Facts an operator attests before a cleanup can be marked verified."""

    host: str
    destroyed_at: datetime
    reviewer: str
    reference: str
    sha256: str

    def as_document(self) -> dict[str, object]:
        return {
            "verified": True,
            "source": ATTESTATION_SOURCE,
            "host": self.host,
            "destroyed_at": self.destroyed_at.isoformat(),
            "memory_destroyed": True,
            "tmpfs_mount_destroyed": True,
            "no_snapshots": True,
            "no_swap": True,
            "reviewer": self.reviewer,
            "reference": self.reference,
            "evidence_sha256": self.sha256,
        }


@dataclass(frozen=True)
class StatusReport:
    failed_cleanups: tuple[uuid.UUID, ...]
    committed_pending: tuple[uuid.UUID, ...]
    stranded: tuple[uuid.UUID, ...]
    local_attempt_dirs: int
    oldest_temporary_age_s: float
    tmpfs_free_bytes: int
    tmpfs_total_bytes: int
    tmpfs_is_tmpfs: bool

    @property
    def admission_expected_open(self) -> bool:
        return not self.failed_cleanups

    @property
    def attention_required(self) -> bool:
        return bool(self.failed_cleanups or self.committed_pending or self.stranded)

    def as_document(self) -> dict[str, object]:
        return {
            "admission_expected": "open" if self.admission_expected_open else "closed",
            "attention_required": self.attention_required,
            "failed_cleanups": len(self.failed_cleanups),
            "failed_attempt_ids": [str(value) for value in self.failed_cleanups],
            "committed_pending": len(self.committed_pending),
            "committed_pending_ids": [str(value) for value in self.committed_pending],
            "stranded": len(self.stranded),
            "stranded_ids": [str(value) for value in self.stranded],
            "local_attempt_dirs": self.local_attempt_dirs,
            "oldest_temporary_age_seconds": round(self.oldest_temporary_age_s, 3),
            "tmpfs_free_bytes": self.tmpfs_free_bytes,
            "tmpfs_total_bytes": self.tmpfs_total_bytes,
            "tmpfs_is_tmpfs": self.tmpfs_is_tmpfs,
        }


@dataclass(frozen=True)
class SweepReport:
    pass_number: int = 0
    enabled: int = 0
    held: int = 0
    cleanup_failed: int = 0
    lost: int = 0
    stranded_ended: int = 0

    @classmethod
    def from_results(
        cls, reconciled: Sequence[ReconcileResult], ended: Sequence[uuid.UUID]
    ) -> SweepReport:
        actions = [item.action for item in reconciled]
        return cls(
            enabled=actions.count(ReconcileAction.ENABLED),
            held=actions.count(ReconcileAction.HELD),
            cleanup_failed=actions.count(ReconcileAction.CLEANUP_FAILED),
            lost=actions.count(ReconcileAction.LOST),
            stranded_ended=len(ended),
        )

    def as_document(self) -> dict[str, object]:
        return {
            "pass": self.pass_number,
            "enabled": self.enabled,
            "held": self.held,
            "cleanup_failed": self.cleanup_failed,
            "lost": self.lost,
            "stranded_ended": self.stranded_ended,
        }


class ResolveAction(StrEnum):
    WOULD_VERIFY = "would_verify"
    VERIFIED = "verified"
    REFUSED = "refused"


@dataclass(frozen=True)
class ResolveDecision:
    attempt_id: uuid.UUID
    action: ResolveAction
    reason: str | None = None
    status: str | None = None
    cleanup_status: str | None = None

    def as_document(self) -> dict[str, object]:
        return {
            "attempt_id": str(self.attempt_id),
            "action": self.action.value,
            "reason": self.reason,
            "status": self.status,
            "cleanup_status": self.cleanup_status,
        }


@dataclass(frozen=True)
class ResolveReport:
    evidence: DestructionEvidence
    applied: bool
    decisions: tuple[ResolveDecision, ...]

    @property
    def refused(self) -> int:
        return sum(1 for item in self.decisions if item.action is ResolveAction.REFUSED)

    def as_document(self) -> dict[str, object]:
        return {
            "applied": self.applied,
            "evidence": self.evidence.as_document(),
            "decisions": [item.as_document() for item in self.decisions],
        }


def _text_field(document: dict[str, object], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str):
        raise EvidenceError(f"evidence field {key!r} must be a string")
    text = value.strip()
    if not text or len(text) > 200:
        raise EvidenceError(f"evidence field {key!r} must have 1-200 characters")
    if not text.isprintable():
        raise EvidenceError(f"evidence field {key!r} is not printable text")
    return text


def _true_field(document: dict[str, object], key: str) -> None:
    if document.get(key) is not True:
        raise EvidenceError(f"evidence field {key!r} must be true")


def _parse_destroyed_at(value: object) -> datetime:
    if not isinstance(value, str):
        raise EvidenceError("evidence field 'destroyed_at' must be an ISO 8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise EvidenceError("evidence field 'destroyed_at' is not a valid ISO 8601 instant") \
            from None
    if parsed.tzinfo is None:
        raise EvidenceError("evidence field 'destroyed_at' must include a timezone")
    normalized = parsed.astimezone(UTC)
    if normalized > datetime.now(UTC) + timedelta(minutes=5):
        raise EvidenceError("evidence field 'destroyed_at' is in the future")
    return normalized


def load_evidence(path: Path) -> DestructionEvidence:
    """Read and validate the destruction attestation. No value is echoed on failure."""
    try:
        raw = path.read_bytes()
    except OSError:
        raise EvidenceError("the evidence file cannot be read") from None
    if len(raw) > EVIDENCE_MAX_BYTES:
        raise EvidenceError(f"the evidence file exceeds {EVIDENCE_MAX_BYTES} bytes")
    digest = hashlib.sha256(raw).hexdigest()
    try:
        document: object = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise EvidenceError("the evidence file is not valid JSON") from None
    if not isinstance(document, dict):
        raise EvidenceError("the evidence file must contain a JSON object")
    unknown = set(document) - EVIDENCE_KEYS
    missing = EVIDENCE_KEYS - set(document)
    if unknown:
        raise EvidenceError("the evidence file contains unknown fields")
    if missing:
        raise EvidenceError(
            f"the evidence file is missing required fields: {', '.join(sorted(missing))}"
        )
    for key in ("memory_destroyed", "tmpfs_mount_destroyed", "no_snapshots", "no_swap"):
        _true_field(document, key)
    return DestructionEvidence(
        host=_text_field(document, "host"),
        destroyed_at=_parse_destroyed_at(document.get("destroyed_at")),
        reviewer=_text_field(document, "reviewer"),
        reference=_text_field(document, "reference"),
        sha256=digest,
    )


def _uuid_value(row: asyncpg.Record, key: str) -> uuid.UUID:
    value = row[key]
    if not isinstance(value, uuid.UUID):
        raise RecoveryError("a database row is missing an identifier")
    return value


def _text_value(row: asyncpg.Record, key: str) -> str:
    value = row[key]
    if not isinstance(value, str):
        raise RecoveryError("a database row has an unexpected shape")
    return value


def _int_value(row: asyncpg.Record, key: str) -> int:
    value = row[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise RecoveryError("a database row has an unexpected shape")
    return value


def _optional_datetime(row: asyncpg.Record, key: str) -> datetime | None:
    value = row[key]
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise RecoveryError("a database row has an unexpected shape")
    return value


def _datetime_value(row: asyncpg.Record, key: str) -> datetime:
    value = _optional_datetime(row, key)
    if value is None:
        raise RecoveryError("a database row has an unexpected shape")
    return value


def _ids(rows: Sequence[asyncpg.Record]) -> tuple[uuid.UUID, ...]:
    return tuple(_uuid_value(row, "id") for row in rows)


def _tmp_root(tmp_dir: str | None) -> str:
    return tempfile.gettempdir() if tmp_dir is None else tmp_dir


def _local_attempt_dir_count(root: str) -> int:
    try:
        entries = list(Path(root).iterdir())
    except OSError:
        return 0
    return sum(1 for entry in entries if entry.name.startswith(metrics.ATTEMPT_DIR_PREFIX))


def _tmpfs_is_tmpfs(root: str) -> bool:
    try:
        mounts = hardening.parse_mountinfo(hardening.MOUNTINFO.read_text())
    except OSError:
        return False
    mount = hardening.mount_for(mounts, root)
    return mount is not None and mount.fstype == "tmpfs"


async def collect_status(pool: asyncpg.Pool, *, tmp_dir: str | None = None) -> StatusReport:
    """Read-only state used by the runbook before any recovery action."""
    root = _tmp_root(tmp_dir)
    failed = _ids(await rls.fetch(pool, None, FAILED_SQL))
    committed = _ids(await rls.fetch(pool, None, COMMITTED_PENDING_SQL))
    stranded = _ids(await rls.fetch(pool, None, STRANDED_SQL))
    try:
        free, total = metrics.tmpfs_bytes(root)
    except OSError:
        raise RecoveryError("the temporary directory is not readable") from None
    return StatusReport(
        failed_cleanups=failed,
        committed_pending=committed,
        stranded=stranded,
        local_attempt_dirs=_local_attempt_dir_count(root),
        oldest_temporary_age_s=metrics.oldest_temporary_age_s(root, now=time.time()),
        tmpfs_free_bytes=free,
        tmpfs_total_bytes=total,
        tmpfs_is_tmpfs=_tmpfs_is_tmpfs(root),
    )


def render_status(report: StatusReport) -> str:
    def _ids_text(values: tuple[uuid.UUID, ...]) -> str:
        return ",".join(str(value) for value in values)

    return "\n".join([
        f"admission_expected={'open' if report.admission_expected_open else 'closed'}",
        f"attention_required={'true' if report.attention_required else 'false'}",
        f"failed_cleanups={len(report.failed_cleanups)}",
        f"failed_attempt_ids={_ids_text(report.failed_cleanups)}",
        f"committed_pending={len(report.committed_pending)}",
        f"committed_pending_ids={_ids_text(report.committed_pending)}",
        f"stranded={len(report.stranded)}",
        f"stranded_ids={_ids_text(report.stranded)}",
        f"local_attempt_dirs={report.local_attempt_dirs}",
        f"oldest_temporary_age_seconds={report.oldest_temporary_age_s:.3f}",
        f"tmpfs_free_bytes={report.tmpfs_free_bytes}",
        f"tmpfs_total_bytes={report.tmpfs_total_bytes}",
        f"tmpfs_is_tmpfs={'true' if report.tmpfs_is_tmpfs else 'false'}",
    ])


async def sweep_once(
    pool: asyncpg.Pool, *, instance: str, tmp_dir: str | None = None
) -> SweepReport:
    """One startup-equivalent recovery pass. It never runs ASR and never takes a live lease."""
    reconciled = await reconcile_committed(pool, instance=instance, tmp_dir=tmp_dir)
    ended = await sweep_stranded(pool)
    return SweepReport.from_results(reconciled, ended)


Sleeper = Callable[[float], Awaitable[None]]
PassRunner = Callable[[], Awaitable[SweepReport]]
PassObserver = Callable[[SweepReport], None]
StopPredicate = Callable[[SweepReport], bool]


async def run_sweeper(
    *,
    interval_s: float,
    max_passes: int | None,
    run_pass: PassRunner,
    sleep: Sleeper = asyncio.sleep,
    on_report: PassObserver | None = None,
    stop_on: StopPredicate | None = None,
) -> tuple[SweepReport, ...]:
    """Run the first pass immediately, then one pass every ``interval_s`` seconds.

    ``max_passes=None`` is the long-running operator sweeper. A finite value is used by
    the runbook drill and by the unit test; no hidden retry loop or backoff is added.
    ``stop_on`` lets the caller fail stop after a cleanup failure instead of keeping the
    admission-moving process alive with a stale in-memory latch.
    """
    if interval_s < SWEEP_INTERVAL_S:
        raise RecoveryError(f"the sweeper interval must be at least {SWEEP_INTERVAL_S:.0f} s")
    reports: list[SweepReport] = []
    number = 0
    while max_passes is None or number < max_passes:
        number += 1
        report = replace(await run_pass(), pass_number=number)
        reports.append(report)
        if on_report is not None:
            on_report(report)
        if stop_on is not None and stop_on(report):
            break
        if max_passes is not None and number >= max_passes:
            break
        await sleep(interval_s)
    return tuple(reports)


def render_sweep(report: SweepReport) -> str:
    return (
        f"pass={report.pass_number} enabled={report.enabled} held={report.held} "
        f"cleanup_failed={report.cleanup_failed} lost={report.lost} "
        f"stranded_ended={report.stranded_ended}"
    )


def _refused(
    attempt_id: uuid.UUID, reason: str, *, status: str | None = None,
    cleanup_status: str | None = None,
) -> ResolveDecision:
    return ResolveDecision(
        attempt_id=attempt_id, action=ResolveAction.REFUSED, reason=reason,
        status=status, cleanup_status=cleanup_status,
    )


def _evidence_covers(evidence: DestructionEvidence, row: asyncpg.Record) -> bool:
    heartbeat = _optional_datetime(row, "heartbeat_at")
    last_activity = heartbeat if heartbeat is not None else _datetime_value(row, "updated_at")
    return last_activity <= evidence.destroyed_at + CLOCK_SKEW_TOLERANCE


async def resolve_attempts(
    pool: asyncpg.Pool,
    *,
    attempt_ids: Sequence[uuid.UUID],
    evidence: DestructionEvidence,
    apply: bool,
    tmp_dir: str | None = None,
) -> ResolveReport:
    """Attest that the listed attempts' audio was destroyed with a host or container.

    The default is a dry run. Every attempt is checked before a single row is written:
    the cleanup must still be pending/failed, the lease must be expired, the last
    heartbeat must be covered by the evidence, and no local attempt directory may exist.
    """
    root = _tmp_root(tmp_dir)
    ordered = tuple(dict.fromkeys(attempt_ids))
    rows = await rls.fetch(pool, None, RESOLVE_ROWS_SQL, list(ordered))
    by_id = {_uuid_value(row, "id"): row for row in rows}
    decisions: list[ResolveDecision] = []
    document = {**evidence.as_document(), "recorded_at": datetime.now(UTC).isoformat()}
    encoded = json.dumps(document, sort_keys=True)
    for attempt_id in ordered:
        row = by_id.get(attempt_id)
        if row is None:
            decisions.append(_refused(attempt_id, "not_found"))
            continue
        status = _text_value(row, "status")
        cleanup_status = _text_value(row, "cleanup_status")
        if cleanup_status not in {"pending", "failed"}:
            decisions.append(
                _refused(attempt_id, "already_resolved", status=status,
                         cleanup_status=cleanup_status)
            )
            continue
        if status == "awaiting_upload":
            decisions.append(
                _refused(attempt_id, "no_audio_received", status=status,
                         cleanup_status=cleanup_status)
            )
            continue
        if cleanup_status == "pending" and status not in PENDING_RESOLVABLE_STATUSES:
            decisions.append(
                _refused(attempt_id, "status_not_resolvable", status=status,
                         cleanup_status=cleanup_status)
            )
            continue
        lease = _optional_datetime(row, "lease_expires_at")
        if lease is not None and lease > datetime.now(UTC):
            decisions.append(
                _refused(attempt_id, "live_lease", status=status,
                         cleanup_status=cleanup_status)
            )
            continue
        if not _evidence_covers(evidence, row):
            decisions.append(
                _refused(attempt_id, "not_covered_by_evidence", status=status,
                         cleanup_status=cleanup_status)
            )
            continue
        if local_workdirs(Path(root), attempt_id):
            decisions.append(
                _refused(attempt_id, "local_attempt_directory_present", status=status,
                         cleanup_status=cleanup_status)
            )
            continue
        if not apply:
            decisions.append(
                ResolveDecision(
                    attempt_id=attempt_id, action=ResolveAction.WOULD_VERIFY,
                    status=status, cleanup_status=cleanup_status,
                )
            )
            continue
        updated = await rls.fetchrow(
            pool,
            _uuid_value(row, "user_id"),
            RESOLVE_UPDATE_SQL,
            attempt_id,
            _uuid_value(row, "user_id"),
            _int_value(row, "fencing_token"),
            encoded,
            cleanup_status,
        )
        if updated is None:
            decisions.append(
                _refused(attempt_id, "state_changed", status=status,
                         cleanup_status=cleanup_status)
            )
            continue
        decisions.append(
            ResolveDecision(
                attempt_id=attempt_id, action=ResolveAction.VERIFIED,
                status=status, cleanup_status="verified",
            )
        )
    return ResolveReport(evidence=evidence, applied=apply, decisions=tuple(decisions))


def render_resolve(report: ResolveReport) -> str:
    lines = [
        f"evidence_sha256={report.evidence.sha256}",
        f"evidence_host={report.evidence.host}",
        f"evidence_destroyed_at={report.evidence.destroyed_at.isoformat()}",
        f"applied={'true' if report.applied else 'false'}",
    ]
    for item in report.decisions:
        line = f"attempt={item.attempt_id} action={item.action.value}"
        if item.reason is not None:
            line += f" reason={item.reason}"
        if item.status is not None:
            line += f" status={item.status}"
        if item.cleanup_status is not None:
            line += f" cleanup_status={item.cleanup_status}"
        lines.append(line)
    return "\n".join(lines)


async def open_pool(database_url: str) -> asyncpg.Pool:
    dsn = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    return await asyncpg.create_pool(dsn, min_size=1, max_size=2, timeout=5.0)


def _database_url() -> str:
    value = settings.database_url
    if value is None or not value.strip():
        raise RecoveryError("MAULWURF_DATABASE_URL is not set")
    return value


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise RecoveryError("an option has an unexpected type")
    return value


def _uuid_list(value: object) -> tuple[uuid.UUID, ...]:
    if not isinstance(value, list):
        raise RecoveryError("at least one --attempt is required")
    result: list[uuid.UUID] = []
    for item in value:
        if not isinstance(item, uuid.UUID):
            raise RecoveryError("--attempt must be a UUID")
        result.append(item)
    if not result:
        raise RecoveryError("at least one --attempt is required")
    return tuple(result)


def _optional_positive_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise RecoveryError("--max-passes must be a positive integer")
    return value


async def _status_command(pool: asyncpg.Pool, args: argparse.Namespace) -> int:
    report = await collect_status(pool, tmp_dir=_optional_str(getattr(args, "tmp_dir", None)))
    if bool(getattr(args, "json", False)):
        print(json.dumps(report.as_document(), indent=2, sort_keys=True))
    else:
        print(render_status(report))
    if bool(getattr(args, "check", False)) and report.attention_required:
        return EXIT_ATTENTION
    return EXIT_OK


async def _sweep_command(pool: asyncpg.Pool, args: argparse.Namespace) -> int:
    interval = float(getattr(args, "interval", SWEEP_INTERVAL_S))
    watch = bool(getattr(args, "watch", False))
    max_passes = _optional_positive_int(getattr(args, "max_passes", None))
    if max_passes is not None and not watch:
        raise RecoveryError("--max-passes requires --watch")
    passes: int | None = max_passes if watch else 1
    tmp_dir = _optional_str(getattr(args, "tmp_dir", None))

    async def run_pass() -> SweepReport:
        return await sweep_once(pool, instance="ops:cleanup-recover", tmp_dir=tmp_dir)

    def report_pass(report: SweepReport) -> None:
        if bool(getattr(args, "json", False)):
            print(json.dumps(report.as_document(), sort_keys=True), flush=True)
        else:
            print(render_sweep(report), flush=True)

    reports = await run_sweeper(
        interval_s=interval,
        max_passes=passes,
        run_pass=run_pass,
        on_report=report_pass,
        stop_on=lambda report: bool(report.cleanup_failed),
    )
    if any(report.cleanup_failed for report in reports):
        return EXIT_ATTENTION
    return EXIT_OK


async def _resolve_command(pool: asyncpg.Pool, args: argparse.Namespace) -> int:
    evidence_file = _optional_str(getattr(args, "evidence_file", None))
    if evidence_file is None:
        raise RecoveryError("--evidence-file is required")
    evidence = load_evidence(Path(evidence_file))
    report = await resolve_attempts(
        pool,
        attempt_ids=_uuid_list(getattr(args, "attempt", None)),
        evidence=evidence,
        apply=bool(getattr(args, "apply", False)),
        tmp_dir=_optional_str(getattr(args, "tmp_dir", None)),
    )
    if bool(getattr(args, "json", False)):
        print(json.dumps(report.as_document(), indent=2, sort_keys=True))
    else:
        print(render_resolve(report))
    return EXIT_ATTENTION if report.refused else EXIT_OK


async def _dispatch(pool: asyncpg.Pool, args: argparse.Namespace) -> int:
    command = getattr(args, "command", None)
    if command == "status":
        return await _status_command(pool, args)
    if command == "sweep":
        return await _sweep_command(pool, args)
    if command == "resolve":
        return await _resolve_command(pool, args)
    raise RecoveryError("unknown command")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cleanup_recover",
        description=(
            "S4.1 cleanup/recovery operator tool. It reads only attempt metadata and "
            "never audio or transcript text."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="read-only recovery state")
    status.add_argument("--check", action="store_true", help="exit 3 when attention is required")
    status.add_argument("--json", action="store_true", help="print a JSON document")
    status.add_argument("--tmp-dir", default=None, help="attempt tmpfs (default: TMPDIR)")

    sweep = subparsers.add_parser("sweep", help="run one recovery pass or the 60 s sweeper")
    sweep.add_argument("--watch", action="store_true", help="keep running the 60 s sweeper")
    sweep.add_argument(
        "--interval", type=float, default=SWEEP_INTERVAL_S,
        help=f"seconds between passes in --watch mode (minimum {SWEEP_INTERVAL_S:.0f})",
    )
    sweep.add_argument(
        "--max-passes", type=int, default=None, help="bounded number of passes for a drill"
    )
    sweep.add_argument("--json", action="store_true", help="print one JSON object per pass")
    sweep.add_argument("--tmp-dir", default=None, help="attempt tmpfs (default: TMPDIR)")

    resolve = subparsers.add_parser(
        "resolve", help="record a destruction attestation for pending/failed cleanup"
    )
    resolve.add_argument("--attempt", action="append", type=uuid.UUID, required=True)
    resolve.add_argument("--evidence-file", required=True)
    resolve.add_argument(
        "--apply", action="store_true", help="write the attestation (default: dry run)"
    )
    resolve.add_argument("--json", action="store_true", help="print a JSON document")
    resolve.add_argument("--tmp-dir", default=None, help="attempt tmpfs (default: TMPDIR)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging()
    try:
        return asyncio.run(_open_and_dispatch(args))
    except KeyboardInterrupt:
        return EXIT_OK
    except RecoveryError as exc:
        print(f"error={exc}", file=sys.stderr)
        return EXIT_ERROR
    except Exception as exc:  # noqa: BLE001 - the message may contain the DSN
        print(f"error={type(exc).__name__}", file=sys.stderr)
        return EXIT_ERROR


async def _open_and_dispatch(args: argparse.Namespace) -> int:
    pool = await open_pool(_database_url())
    try:
        return await _dispatch(pool, args)
    finally:
        await pool.close()


if __name__ == "__main__":
    raise SystemExit(main())

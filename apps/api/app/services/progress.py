"""Honest progress snapshot and SSE encoding (A2.9, S2.md §A2.9 on DISENO §3.5).

The events are STATE, not an increment: on every connect or reconnect the server sends a
fresh `snapshot` read from the database (DISENO §3.5). Progress reports only the bytes and
fragments the ingestion actually recorded — never a percentage, never an interpolated ETA
(ESPECIFICACION.md M2). This module is pure; the polling generator lives in
`services/library.py` next to the reads it performs, which keeps the import graph acyclic.
"""

import json
from dataclasses import dataclass
from typing import Any

# Provisional poll cadence: D4 has not published operational limits, so these are bounded
# constants (like `audios.RETRY_AFTER_SECONDS`), not a published capacity decision.
POLL_SECONDS = 1.0
HEARTBEAT_SECONDS = 15.0
# Hard cap for a stuck class; closing the connection lets the client reconnect (the state is
# in the DB) instead of holding an idle stream open forever.
MAX_SECONDS = 900.0
# Aggregates that mean there is no more work to report (state_machine.AGGREGATE_STATES).
TERMINAL_AGGREGATES = ("ready", "partial", "failed")


def encode_event(*, name: str, id: int, data: dict[str, Any]) -> str:
    """One SSE frame; `data` is JSON with only measured values."""
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {name}\nid: {id}\ndata: {payload}\n\n"


def heartbeat() -> str:
    """SSE comment used as a keep-alive between events (DISENO §3.5, `: ping`)."""
    return ": ping\n\n"


@dataclass(frozen=True)
class ProgressSnapshot:
    """Flat view of one class state; safe to read after the session that built it closes."""

    attempt_status: str | None
    attempt_error_code: str | None
    received_bytes: int
    fragments_done: int | None
    fragments_total: int | None
    stages: dict[str, str]
    aggregate: str

    def attempt_payload(self) -> dict[str, Any]:
        return {
            "status": self.attempt_status,
            "received_bytes": self.received_bytes,
            "fragments_done": self.fragments_done,
            "fragments_total": self.fragments_total,
        }

    def snapshot_payload(self) -> dict[str, Any]:
        return {
            "attempt": self.attempt_payload(),
            "stages": dict(self.stages),
            "aggregate": self.aggregate,
        }

    def progress_payload(self) -> dict[str, Any]:
        return {
            "received_bytes": self.received_bytes,
            "fragments_done": self.fragments_done,
            "fragments_total": self.fragments_total,
        }

    def progress_key(self) -> tuple[int, int | None, int | None, str | None]:
        """Values whose change means a new `progress` event is owed."""
        return (
            self.received_bytes,
            self.fragments_done,
            self.fragments_total,
            self.attempt_status,
        )

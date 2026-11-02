"""CLI for the A4.7 orphan sweep over class-derived rows.

Run from the repository root with the cross-tenant jobs role::

    MAULWURF_DATABASE_ROLE=maulwurf_jobs python scripts/maintenance/sweep_orphans.py
    MAULWURF_DATABASE_ROLE=maulwurf_jobs \
        python scripts/maintenance/sweep_orphans.py --user-id <uuid> --json

The sweep is read-only: it reports chunk, embedding, transcript, segment, citation, task
and reminder rows that survived their class, and exits non-zero when any exists. It never
prints text, titles, emails or other row content, only check names and counts.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.services.orphan_sweep import OrphanReport

JOBS_ROLE = "maulwurf_jobs"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/maintenance/sweep_orphans.py",
        description=(
            "Verifica que ninguna fila derivada de una clase (transcripts, chunks, "
            "embeddings, citas, tareas o recordatorios) sobreviva a su clase. Solo lee."
        ),
    )
    parser.add_argument(
        "--user-id",
        type=uuid.UUID,
        action="append",
        default=None,
        dest="user_ids",
        help="tenant concreto; repetible (por defecto, todos los tenants)",
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


async def _execute(args: argparse.Namespace) -> OrphanReport:
    # Imported here so `--help` works without a configured environment.
    from app.core.config import settings
    from app.core.db import bind_cross_tenant, reset_cross_tenant, session_scope
    from app.services.orphan_sweep import sweep_orphans

    if settings.database_role != JOBS_ROLE:
        raise RuntimeError(
            "run with MAULWURF_DATABASE_ROLE=maulwurf_jobs so row-level security "
            "does not hide another tenant"
        )
    token = bind_cross_tenant()
    try:
        async with session_scope() as db:
            return await sweep_orphans(db, user_ids=args.user_ids)
    finally:
        reset_cross_tenant(token)


def _human_payload(report: OrphanReport) -> str:
    lines = ["orphan sweep (read-only)"]
    for finding in report.findings:
        lines.append(f"{finding.name}: {finding.count}")
    lines.append(f"total: {report.total}")
    lines.append("clean" if report.clean else "orphans found")
    return "\n".join(lines)


def _json_payload(report: OrphanReport, user_ids: Sequence[uuid.UUID] | None) -> str:
    payload: dict[str, Any] = report.as_dict()
    payload["scope"] = "all" if user_ids is None else [str(value) for value in user_ids]
    return json.dumps(payload, sort_keys=True)


def _print_report(
    report: OrphanReport, *, user_ids: Sequence[uuid.UUID] | None, as_json: bool
) -> None:
    print(_json_payload(report, user_ids) if as_json else _human_payload(report))


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = asyncio.run(_execute(args))
    except RuntimeError as exc:
        print(f"sweep_orphans: {exc}", file=sys.stderr)
        return 2
    _print_report(report, user_ids=args.user_ids, as_json=args.as_json)
    return 0 if report.clean else 1


if __name__ == "__main__":
    raise SystemExit(main())

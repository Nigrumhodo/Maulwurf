"""CLI for the explicit S3.1 analysis backfill over persisted transcripts.

Run from the repository root::

    python scripts/backfill_analysis.py --user-id <uuid>            # dry-run
    python scripts/backfill_analysis.py --user-id <uuid> --apply    # queue analysis

The command is deliberately tenant-scoped and dry-run by default. It prints only IDs,
versions and statuses: never transcript text, audio paths, subjects or teacher names.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from collections.abc import Sequence
from datetime import date
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.services.analysis_backfill import BackfillItem, BackfillReport

DEFAULT_LIMIT = 500
MAX_LIMIT = 2000


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"fecha ISO inválida: {value}") from exc


def _bounded_limit(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--limit debe ser un entero") from exc
    if not 1 <= parsed <= MAX_LIMIT:
        raise argparse.ArgumentTypeError(f"--limit debe estar entre 1 y {MAX_LIMIT}")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/backfill_analysis.py",
        description=(
            "Encola el análisis explícito de clases F1 usando solo su transcripción "
            "persistida. Nunca re-sube audio ni re-transcribe."
        ),
    )
    parser.add_argument(
        "--user-id",
        type=uuid.UUID,
        required=True,
        help="tenant dueño de las clases (obligatorio para no cruzar usuarios)",
    )
    parser.add_argument(
        "--audio-id",
        type=uuid.UUID,
        action="append",
        default=None,
        dest="audio_ids",
        help="clase concreta; repetible (por defecto, todas las del tenant)",
    )
    parser.add_argument("--subject-id", type=uuid.UUID, default=None)
    parser.add_argument("--from-date", type=_iso_date, default=None, dest="class_date_from")
    parser.add_argument("--to-date", type=_iso_date, default=None, dest="class_date_to")
    parser.add_argument("--limit", type=_bounded_limit, default=DEFAULT_LIMIT)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="escribe los runs y eventos; sin este flag solo se informa qué haría",
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


async def _execute(args: argparse.Namespace) -> BackfillReport:
    # Imported here so `--help` and usage errors work without a configured environment.
    from app.core.db import session_scope
    from app.services.analysis_backfill import backfill_classes

    async with session_scope() as db:
        return await backfill_classes(
            db,
            user_id=args.user_id,
            audio_ids=args.audio_ids,
            subject_id=args.subject_id,
            class_date_from=args.class_date_from,
            class_date_to=args.class_date_to,
            limit=args.limit,
            apply=args.apply,
        )


def _item_payload(item: BackfillItem) -> dict[str, Any]:
    return {
        "audio_id": str(item.audio_id),
        "status": item.status.value,
        "transcript_version": item.transcript_version,
        "run_id": str(item.run_id) if item.run_id is not None else None,
        "run_status": item.run_status,
    }


def _print_report(report: BackfillReport, *, user_id: uuid.UUID, as_json: bool) -> None:
    counts = {status.value: count for status, count in sorted(report.counts.items())}
    if as_json:
        print(
            json.dumps(
                {
                    "user_id": str(user_id),
                    "mode": "apply" if report.applied else "dry-run",
                    "truncated": report.truncated,
                    "counts": counts,
                    "items": [_item_payload(item) for item in report.items],
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        return
    mode = "apply" if report.applied else "dry-run"
    print(
        f"backfill user={user_id} mode={mode} "
        f"items={len(report.items)} truncated={report.truncated}"
    )
    print("counts " + " ".join(f"{key}={value}" for key, value in counts.items()))
    for item in report.items:
        run = f" run={item.run_id} run_status={item.run_status}" if item.run_id else ""
        version = (
            f" transcript_version={item.transcript_version}"
            if item.transcript_version is not None
            else ""
        )
        print(f"audio={item.audio_id} status={item.status.value}{version}{run}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if (
        args.class_date_from is not None
        and args.class_date_to is not None
        and args.class_date_from > args.class_date_to
    ):
        parser.error("--from-date no puede ser posterior a --to-date")
    try:
        report = asyncio.run(_execute(args))
    except Exception as exc:
        # Only the type: messages can include DSNs or database details.
        print(f"error: {type(exc).__name__}", file=sys.stderr)
        return 1
    _print_report(report, user_id=args.user_id, as_json=args.as_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

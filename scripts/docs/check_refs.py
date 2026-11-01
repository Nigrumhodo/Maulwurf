#!/usr/bin/env python3
"""Check that backticked repository paths in the documentation exist.

The documentation uses inline code spans for repository paths (for example
``apps/api/app/main.py``). A path that is renamed or never created is easy to miss
in review, so this checker fails when a documented path is absent from the tree. It scans
``AGENTS.md``, ``README.md`` and every Markdown file under ``docs/``.

How a span is classified
------------------------

Only spans whose first segment is an existing top-level entry (``apps``, ``docs``,
``infra``, ``scripts``, ...) are treated as repository paths. That keeps relative
references such as ``core/config.py`` (relative to ``apps/api``) and runtime values
such as Docker image names out of the check. ``docs/es/`` is excluded because it is
the archived Spanish snapshot and is no longer edited.

Paths that are intentionally not in the tree yet live in ``EXPECTED_FILES`` below.
Each entry needs a reason (the sprint/ticket that will create it, or why the path
must stay documented). An entry ending in ``/`` covers every path below it.
Deleting an entry when the file lands is encouraged; the checker does not enforce it.

Usage
-----

    python3 scripts/docs/check_refs.py
    python3 scripts/docs/check_refs.py --verbose

The command exits 0 when every documented repository path exists or is expected,
and 1 otherwise.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
EXCLUDED_PARTS = {DOCS / "es"}

# Entries are exact paths, or directory prefixes ending in "/".
EXPECTED_FILES: dict[str, str] = {
    # S2 web work still open (D2.x); D1.8 remains unclaimed.
    "apps/web/src/components/landing/": "planned by D1.8 (public landing)",
    "apps/web/src/components/upload/IngestProgress.tsx": "planned by D2.3",
    "apps/web/src/components/transcript/": "planned by D2.5/D3.5",
    "apps/web/src/components/chat/Citation.tsx": "planned by D2.7",
    "apps/web/src/lib/sse.ts": "planned by D2.3",
    "apps/web/src/lib/stream.ts": "planned by D2.6",
    "apps/web/src/lib/markdown.ts": "planned by D2.7",
    # S2 evaluation and load tickets still unclaimed (L2.7/L2.8).
    "scripts/eval/g4.py": "planned by L2.7 (S2, unclaimed)",
    "scripts/load/latency_rag.py": "planned by L2.8 (S2, unclaimed)",
    # S3 files: the sprint is closed and nothing under these paths exists yet.
    "apps/api/alembic/versions/0003_s3.py": "planned by S3/A3.1",
    "apps/api/app/models/analysis.py": "planned by S3/A3.1",
    "apps/api/app/models/processing_run.py": "planned by S3/A3.2",
    "apps/api/app/models/task.py": "planned by S3/A3.1",
    "apps/api/app/models/task_source.py": "planned by S3/A3.1",
    "apps/api/app/routers/calendar.py": "planned by S3/J3.4",
    "apps/api/app/routers/tasks.py": "planned by S3/A3.4",
    "apps/api/app/services/analysis.py": "planned by S3/A3.2",
    "apps/api/app/services/analysis_validation.py": "planned by S3/A3.3",
    "apps/api/app/services/calendar_sync.py": "planned by S3/J3.4",
    "apps/api/app/services/conflicts.py": "planned by S3/J3.5",
    "apps/api/app/services/date_resolution.py": "planned by S3/A3.3",
    "apps/api/app/services/evidence.py": "planned by S3/A3.3",
    "apps/api/app/services/google_calendar.py": "planned by S3/J3.2",
    "apps/api/app/services/revisions.py": "planned by S3/A3.6",
    "apps/api/app/services/summary.py": "planned by S3/L3.3",
    "apps/api/app/services/task_state.py": "planned by S3/A3.5",
    "apps/api/app/services/tasks.py": "planned by S3/A3.4",
    "apps/api/app/services/tools.py": "planned by S3/L3.4",
    "apps/api/app/workers/jobs/analyze.py": "planned by S3/A3.2",
    "apps/api/app/workers/jobs/calendar_sync.py": "planned by S3/J3.4",
    "apps/api/app/workers/retry.py": "planned by S3/J3.1",
    "apps/api/prompts/registry.py": "planned by S3/L3.1",
    "apps/api/tests/integration/test_task_idempotency.py": "planned by S3/A3.4",
    "apps/api/tests/prompt_regression/": "planned by S3/L3.1 (R-* suite)",
    "apps/web/src/components/tasks/": "planned by S3/D3.1–D3.3",
    "apps/web/src/components/calendar/": "planned by S3/D3.6–D3.7",
    "apps/web/src/components/chat/ProposalCard.tsx": "planned by S3/D3.8",
    "apps/web/src/components/chat/ToolBadge.tsx": "planned by S3/D3.8",
    "scripts/backfill_analysis.py": "planned by S3/A3.2",
    "scripts/eval/g6.py": "planned by S3/L3.3",
    "scripts/load/asr_cost.py": "planned by S3/L3.5",
    "scripts/provider/google_scopes.py": "planned by S3/J3.2",
    "scripts/provider/google_test_account.py": "planned by S3/J3.3",
    # S4 files: the sprint is closed and nothing under these paths exists yet.
    "apps/api/alembic/versions/0004_s4.py": "planned by S4/A4.1",
    "apps/api/app/models/account_deletion.py": "planned by S4/A4.4",
    "apps/api/app/models/notification.py": "planned by S4/A4.1",
    "apps/api/app/routers/notifications.py": "planned by S4/A4.2",
    "apps/api/app/services/deletion.py": "planned by S4/A4.4",
    "apps/api/app/services/digests.py": "planned by S4/J4.6",
    "apps/api/app/services/gmail.py": "planned by S4/J4.1",
    "apps/api/app/services/limits.py": "planned by S4/A4.6",
    "apps/api/app/services/notifications.py": "planned by S4/A4.3",
    "apps/api/app/services/reminders.py": "planned by S4/J4.3",
    "apps/api/app/services/scheduler_lease.py": "planned by S4/J4.2",
    "apps/api/app/templates/notifications/": "planned by S4/J4.1",
    "apps/api/tests/integration/test_isolation_final.py": "planned by S4/A4.7",
    "apps/web/public/": "planned by S4/D4.5 (PWA manifest and service worker)",
    "apps/web/src/components/settings/": "planned by S4/D4.2",
    "apps/web/src/components/search/": "planned by S4/D4.3",
    "apps/web/src/components/subjects/SubjectStats.tsx": "planned by S4/D4.4",
    "apps/web/src/lib/preferences.ts": "planned by S4/D4.1",
    "apps/web/src/lib/search.ts": "planned by S4/D4.3",
    "docs/demo/guion.md": "planned by S4/D4.7/L4.5",
    "docs/eval/": "planned: redacted evaluation outputs (L2.7, S4/L4.1)",
    "docs/plan/F4-backlog.md": "planned by S4/L4.6",
    "docs/reviews/consistency-checklist.md": "planned by S4/L4.3",
    "docs/reviews/g1-client.md": "planned by S4/D4.8",
    "docs/reviews/security-cross-review.md": "planned by S4/L4.2",
    "docs/runbooks/deploy.md": "planned by S4/J4.7",
    "docs/runbooks/restore.md": "planned by S4/A4.5",
    "infra/deploy/": "planned by S4/J4.7",
    "infra/docker-compose.prod.yml": "planned by S4/J4.7",
    "scripts/load/g1_load.py": "planned by S4/S4.3",
    "scripts/maintenance/sweep_orphans.py": "planned by S4/A4.7",
    "scripts/ops/backup_restore.sh": "planned by S4/A4.5",
    "scripts/provider/gmail_send_test.py": "planned by S4/J4.10",
    # Documented paths that are intentionally not tracked.
    "docs/sprints/artifacts/D1.7/": "D1.7 review screenshots are deferred; see D1.7-review.md",
    "infra/docker-compose.override.yml": (
        "local-only override created by developers; documented in RUNBOOK-dev.md §6"
    ),
    "scripts/asr/transcribe_file_offline.py": (
        "external path inside the separately cloned NVIDIA python-clients repository"
    ),
}


def iter_markdown_files() -> list[Path]:
    files = [ROOT / "AGENTS.md", ROOT / "README.md"]
    files.extend(sorted(DOCS.rglob("*.md")))
    return [
        path
        for path in files
        if not any(excluded == path or excluded in path.parents for excluded in EXCLUDED_PARTS)
    ]


def strip_fenced_blocks(text: str) -> str:
    """Replace fenced code blocks with blank lines to keep line numbers stable."""
    lines = text.splitlines(keepends=True)
    in_fence = False
    result: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            result.append("\n")
            continue
        result.append("\n" if in_fence else line)
    return "".join(result)


def is_repository_path(span: str, top_level: set[str]) -> bool:
    if not span or any(char in span for char in " \t{}<>|$*…") or "..." in span:
        return False
    if span.startswith(("http://", "https://", "www.", "@", "/", "~")):
        return False
    if ":" in span or "=" in span or "(" in span or ")" in span:
        return False
    if span.startswith((".env", ".venv")) or "/.venv" in span or "node_modules" in span:
        return False
    first = span.split("/", 1)[0]
    return "/" in span and first in top_level


def expected_reason(span: str) -> tuple[str, str] | None:
    candidate = span.rstrip("/")
    for path, reason in EXPECTED_FILES.items():
        if path.endswith("/"):
            prefix = path.rstrip("/")
            if candidate == prefix or candidate.startswith(prefix + "/"):
                return path, reason
        elif candidate == path:
            return path, reason
    return None


def check(verbose: bool) -> int:
    top_level = {entry.name for entry in ROOT.iterdir() if entry.name != ".git"}
    span_pattern = re.compile(r"`([^`\n]+)`")
    checked = 0
    failures: list[tuple[Path, int, str]] = []
    matched_expected: set[str] = set()

    for path in iter_markdown_files():
        text = strip_fenced_blocks(path.read_text(encoding="utf-8"))
        for match in span_pattern.finditer(text):
            span = match.group(1).strip()
            if not is_repository_path(span, top_level):
                continue
            checked += 1
            if (ROOT / span).exists():
                continue
            reason = expected_reason(span)
            if reason is not None:
                matched_expected.add(reason[0])
                continue
            line = text.count("\n", 0, match.start()) + 1
            failures.append((path.relative_to(ROOT), line, span))

    if verbose:
        print(
            f"Checked {checked} documented repository paths in "
            f"{len(iter_markdown_files())} files."
        )
        unused = sorted(set(EXPECTED_FILES) - matched_expected)
        if unused:
            print("Expected entries not referenced by a missing path (informational only):")
            for entry in unused:
                print(f"  - {entry}")
        print()

    if failures:
        print("Documented repository paths that do not exist:", file=sys.stderr)
        for path, line, span in failures:
            print(f"  {path}:{line}: `{span}`", file=sys.stderr)
        print(
            "\nFix the path or add it to EXPECTED_FILES in scripts/docs/check_refs.py "
            "with the ticket that will create it.",
            file=sys.stderr,
        )
        return 1

    print(f"OK: {checked} documented repository paths exist or are expected.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="show checked paths and unused entries",
    )
    args = parser.parse_args()
    return check(verbose=args.verbose)


if __name__ == "__main__":
    raise SystemExit(main())

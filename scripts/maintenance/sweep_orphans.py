#!/usr/bin/env python3
"""Entry point for the A4.7 orphan sweep.

The implementation lives in ``apps/api/tools/sweep_orphans.py``; this shim only makes the
API package importable from a repository checkout::

    MAULWURF_DATABASE_ROLE=maulwurf_jobs \
        python scripts/maintenance/sweep_orphans.py [--user-id <uuid>] [--json]

The command is read-only and exits 1 when a class-derived row outlived its class.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_API_ROOT = _REPO_ROOT / "apps" / "api"
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from tools.sweep_orphans import main  # noqa: E402 - path setup must happen first

if __name__ == "__main__":
    raise SystemExit(main())

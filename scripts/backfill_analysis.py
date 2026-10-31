#!/usr/bin/env python3
"""Thin shim for the S3.1 analysis backfill (S3.1).

Run from the repository root::

    python scripts/backfill_analysis.py --user-id <uuid> --apply

The implementation lives in ``apps/api/tools/backfill_analysis.py``; it is an operator
tool and is not copied into the API runtime image.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_API_ROOT = _REPO_ROOT / "apps" / "api"
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from tools.backfill_analysis import main  # noqa: E402 - path setup must happen first

if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Thin shim for the S3.2 analysis-window cost harness.

Run from the repository root with the API dependencies available::

    cd apps/api && uv run python ../../scripts/eval/window_cost.py --format table
    cd apps/api && uv run python ../../scripts/eval/window_cost.py --format json

The harness lives in ``apps/api/tools/eval/window_cost.py``. Running it directly with the
API environment is equivalent and is the documented form::

    cd apps/api && uv run python -m tools.eval.window_cost

It measures authored/redacted text only and never calls a provider.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_API_ROOT = _REPO_ROOT / "apps" / "api"
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from tools.eval.window_cost import main  # noqa: E402 - path setup must happen first

if __name__ == "__main__":
    raise SystemExit(main())

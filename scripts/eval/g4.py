#!/usr/bin/env python3
"""Thin shim for the G4 harness (L2.7).

Run from the repository root::

    python scripts/eval/g4.py validate --dataset apps/api/tests/fixtures/g4/datasets/v1

The harness itself lives in ``apps/api/tools/eval``; it is a development tool and is not
copied into the API image.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_API_ROOT = _REPO_ROOT / "apps" / "api"
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from tools.eval.cli import main  # noqa: E402 - path setup must happen first

if __name__ == "__main__":
    raise SystemExit(main())

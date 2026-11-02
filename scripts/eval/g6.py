#!/usr/bin/env python3
"""Score the frozen g6-v1 extraction fixtures. No provider call.

    cd apps/api && uv run python ../../scripts/eval/g6.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_API_ROOT = Path(__file__).resolve().parents[2] / "apps" / "api"
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from tools.eval.g6 import main  # noqa: E402 - path setup must happen first

if __name__ == "__main__":
    raise SystemExit(main())

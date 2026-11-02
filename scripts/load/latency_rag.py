#!/usr/bin/env python3
"""Thin shim for the L2.8 latency harness.

Run from the repository root after preparing a G4 run::

    python scripts/load/latency_rag.py --run-id <run-id> --languages es,en,fr \
        --publish docs/eval/latency/<run-id>

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

from tools.eval.latency_runner import main  # noqa: E402 - path setup must happen first

if __name__ == "__main__":
    raise SystemExit(main())

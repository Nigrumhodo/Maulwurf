#!/usr/bin/env python3
"""Entry point for the S4.1 cleanup/recovery runbook.

The implementation lives in ``maulwurf_ingest.recovery``. Inside the hardened ingest
container the runbook uses the installed module directly, so nothing is copied into the
read-only root filesystem or the audio tmpfs:

    cd infra
    docker compose exec -T ingest python -m maulwurf_ingest.recovery status --check

From a repository checkout this script is the equivalent entry point; the path bootstrap
below makes ``apps/ingest`` importable:

    python scripts/ops/cleanup_recover.py status --check

When the file is read from stdin, ``__file__`` is ``"<stdin>"`` and the installed package
is used instead.
"""

from __future__ import annotations

import sys
from pathlib import Path

if __file__ != "<stdin>":
    _INGEST_ROOT = Path(__file__).resolve().parents[2] / "apps" / "ingest"
    if str(_INGEST_ROOT) not in sys.path:
        sys.path.insert(0, str(_INGEST_ROOT))

from maulwurf_ingest.recovery import main  # noqa: E402 - path setup must happen first

if __name__ == "__main__":
    raise SystemExit(main())

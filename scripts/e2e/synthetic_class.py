"""Run the S2.9 in-RAM harness.

From ``apps/ingest``::

    uv run python ../../scripts/e2e/synthetic_class.py

Requires ``MAULWURF_DATABASE_URL`` and espeak-ng. Inserts one synthetic user.
Does not call Riva and does not run the blocked 1 h Caddy demo.
"""

from __future__ import annotations

import sys

from maulwurf_ingest.synthetic_class import main

if __name__ == "__main__":
    sys.exit(main())

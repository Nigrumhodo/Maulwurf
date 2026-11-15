#!/usr/bin/env bash
# I-S1-JF-02 (J1.3) is the no-buffering half of C-S2-JF-13.
# `ingress_limits.sh` is the single harness: it repeats those checks and also
# measures the edge ceilings. This entry point remains so older docs keep working.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$here/ingress_limits.sh" "$@"

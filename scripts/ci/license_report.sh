#!/usr/bin/env bash
# Writes the dependency license report for one app and fails on denied licenses.
# Usage: scripts/ci/license_report.sh <api|ingest|web> [output-dir]
# Tool versions are pinned here; production dependencies only (no dev groups).
set -euo pipefail

app=${1:?usage: license_report.sh <api|ingest|web> [output-dir]}
out_dir=${2:-reports/licenses}
pip_licenses_version=5.5.5
license_checker_version=25.0.1

repo_root="$(git rev-parse --show-toplevel)"
out_json="$(realpath -m "$out_dir")/$app.json"
mkdir -p "$(dirname "$out_json")"

case "$app" in
  api | ingest)
    cd "$repo_root/apps/$app"
    uv sync --frozen --no-dev
    uv run --frozen --no-dev --with "pip-licenses==$pip_licenses_version" \
      pip-licenses --format=json --ignore-packages pip-licenses > "$out_json"
    ;;
  web)
    cd "$repo_root/apps/web"
    npm ci --ignore-scripts
    # The checker is installed at a pinned version. `npx --yes` without a version
    # would fetch whatever is current; this path refuses an unpinned spec.
    if [[ ! "$license_checker_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
      echo "license_report.sh: license-checker version is not pinned" >&2
      exit 2
    fi
    tool_dir="$(mktemp -d)"
    npm install --ignore-scripts --prefix "$tool_dir" \
      "license-checker@${license_checker_version}"
    "$tool_dir/node_modules/.bin/license-checker" --production --json \
      --excludePackages "maulwurf-web@0.1.0" --out "$out_json"
    ;;
  *)
    echo "license_report.sh: unknown app '$app'" >&2
    exit 2
    ;;
esac

python3 "$repo_root/scripts/ci/check_licenses.py" "$out_json" | tee "${out_json%.json}-summary.txt"

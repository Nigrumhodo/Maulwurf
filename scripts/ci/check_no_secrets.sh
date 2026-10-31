#!/usr/bin/env bash
# Fails when a git-tracked text file matches a provider credential pattern.
# Prints only file, rule id and line numbers, never the matched text.
# Allowlist: scripts/ci/secret_scan_allowlist, one "<rule-id> <path>" per line.
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

allowlist="scripts/ci/secret_scan_allowlist"
rule_names=(nvidia-api-key google-oauth-client-secret aws-access-key-id private-key-block bearer-token)
declare -A rule_patterns=(
  [nvidia-api-key]='nvapi-[A-Za-z0-9_-]{40,}'
  [google-oauth-client-secret]='GOCSPX-[A-Za-z0-9_-]{20,}'
  [aws-access-key-id]='AKIA[0-9A-Z]{16}'
  [private-key-block]='-----BEGIN ([A-Z]+ )*PRIVATE KEY( BLOCK)?-----'
  [bearer-token]='[Bb]earer [A-Za-z0-9._~+/-]{20,}=*'
)

is_allowlisted() {
  local rule=$1 path=$2
  [[ -f "$allowlist" ]] || return 1
  awk -v rule="$rule" -v path="$path" '
    /^[[:space:]]*(#|$)/ { next }
    $1 == rule && $2 == path { found = 1 }
    END { exit !found }
  ' "$allowlist"
}

findings=0
scanned=0
while IFS= read -r -d '' file; do
  scanned=$((scanned + 1))
  for rule in "${rule_names[@]}"; do
    is_allowlisted "$rule" "$file" && continue
    lines=$(grep -nIE -e "${rule_patterns[$rule]}" -- "$file" | cut -d: -f1 | paste -sd, -) || true
    if [[ -n "$lines" ]]; then
      echo "secret-scan: rule=$rule file=$file lines=$lines" >&2
      findings=$((findings + 1))
    fi
  done
done < <(git ls-files -z)

if (( findings > 0 )); then
  echo "secret-scan: $findings finding(s) in $scanned tracked files" >&2
  exit 1
fi
echo "secret-scan: $scanned tracked files, 0 findings"

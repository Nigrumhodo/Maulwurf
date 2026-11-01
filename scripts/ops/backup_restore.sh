#!/usr/bin/env bash
# A4.5 / I-S4-AN-05 — D8 backup and restore of encrypted text/metadata only.
#
# Commands:
#   backup            --database-url URL --out DIR [--label NAME]
#   export-deletions  --database-url URL --out FILE
#   export-deletions  --database-url URL --refresh-backup DUMP
#   restore           --database-url URL --backup FILE [--deletion-log FILE]
#                     [--allow-missing-deletion-log] --confirm-clean
#   verify            --backup FILE [--manifest FILE] [--allow-missing-deletion-log]
#   prune             --out DIR
#
# The dump contains only the durable PostgreSQL schema: audio never exists in the database
# by contract, and `backup` fails closed if an unexpected binary column appears. The dump
# and the identifier-only deletion log are streamed through GPG (AES-256) straight to disk,
# so no plaintext backup file is written. Retention is the D8 30-day window: `backup` prunes
# older files and `restore` refuses an expired archive.
#
# Restore is fail-closed around the deletion log. It replays the log bundled with the backup
# and refuses to run when that log is missing, or when an explicit --deletion-log does not
# match the SHA-256 recorded in the manifest. --allow-missing-deletion-log is the only way to
# restore without a log; it warns that data deleted after the backup can reappear. A log
# exported after later deletions is attached to a backup with `export-deletions
# --refresh-backup`, which updates the manifest checksum before the next restore.
#
# Required environment:
#   MAULWURF_BACKUP_PASSPHRASE       passphrase (>= 16 chars), or
#   MAULWURF_BACKUP_PASSPHRASE_FILE  file whose first line is the passphrase.
#
# The connecting role must be a superuser or have BYPASSRLS. FORCE ROW LEVEL SECURITY is
# active on every tenant table, and a non-bypassing role would silently back up zero rows.
set -Eeuo pipefail
umask 077

readonly RETENTION_DAYS=30
readonly ALLOWED_BINARY_COLUMNS=(
  "google_credentials.access_token_enc"
  "google_credentials.refresh_token_enc"
)
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly REAPPLY_SQL="${SCRIPT_DIR}/restore_reapply.sql"

BACKUP_PASSPHRASE=""
WORK_DIR=""
PARTIAL_FILES=()

die() {
  printf 'backup_restore: %s\n' "$*" >&2
  exit 2
}

warn() {
  printf 'backup_restore: WARNING: %s\n' "$*" >&2
}

usage() {
  sed -n '2,31p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

ensure_work_dir() {
  if [[ -z "${WORK_DIR}" ]]; then
    WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/maulwurf-ops.XXXXXX")"
    chmod 700 "${WORK_DIR}"
  fi
}

cleanup() {
  local path
  for path in "${PARTIAL_FILES[@]}"; do
    rm -f "${path}"
  done
  if [[ -n "${WORK_DIR}" && -d "${WORK_DIR}" ]]; then
    rm -rf "${WORK_DIR}"
  fi
}
trap cleanup EXIT

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

load_passphrase() {
  if [[ -n "${MAULWURF_BACKUP_PASSPHRASE_FILE:-}" ]]; then
    [[ -r "${MAULWURF_BACKUP_PASSPHRASE_FILE}" ]] \
      || die "MAULWURF_BACKUP_PASSPHRASE_FILE is not readable"
    IFS= read -r BACKUP_PASSPHRASE <"${MAULWURF_BACKUP_PASSPHRASE_FILE}" || true
  elif [[ -n "${MAULWURF_BACKUP_PASSPHRASE:-}" ]]; then
    BACKUP_PASSPHRASE="${MAULWURF_BACKUP_PASSPHRASE}"
  else
    die "set MAULWURF_BACKUP_PASSPHRASE or MAULWURF_BACKUP_PASSPHRASE_FILE"
  fi
  if (( ${#BACKUP_PASSPHRASE} < 16 )); then
    die "the backup passphrase must be at least 16 characters"
  fi
}

sync_url() {
  local url="$1"
  url="${url/postgresql+asyncpg:/postgresql:}"
  url="${url/postgresql+psycopg:/postgresql:}"
  printf '%s' "${url}"
}

# libpq reads the password from a private PGPASSFILE and the rest of the connection from
# PG* variables, so a URL with credentials never appears in `ps` or /proc/<pid>/cmdline.
# PGDATABASE does not expand a connection URI on every libpq build, so the URL is parsed.
load_pg_env() {
  local url="$1" output key value password="" escaped
  output="$(python3 - "${url}" <<'PY'
import sys
from urllib.parse import parse_qsl, unquote, urlsplit

url = urlsplit(sys.argv[1])
if url.scheme not in {"postgres", "postgresql"}:
    raise SystemExit(f"backup_restore: unsupported database URL scheme: {url.scheme}")
query = dict(parse_qsl(url.query, keep_blank_values=True))
values = {}
if url.username is not None:
    values["PGUSER"] = unquote(url.username)
if url.password is not None:
    values["PGPASSWORD"] = unquote(url.password)
if url.hostname is not None:
    values["PGHOST"] = unquote(url.hostname)
if url.port is not None:
    values["PGPORT"] = str(url.port)
if url.path and url.path != "/":
    values["PGDATABASE"] = unquote(url.path.lstrip("/"))
for parameter, variable in (
    ("host", "PGHOST"),
    ("port", "PGPORT"),
    ("user", "PGUSER"),
    ("password", "PGPASSWORD"),
    ("dbname", "PGDATABASE"),
    ("sslmode", "PGSSLMODE"),
):
    if parameter in query:
        values[variable] = query[parameter]
for variable, value in values.items():
    if value:
        print(f"{variable}\t{value}")
PY
)" || die "invalid database URL"
  unset PGHOST PGPORT PGUSER PGDATABASE PGSSLMODE
  while IFS=$'\t' read -r key value; do
    case "${key}" in
      PGPASSWORD) password="${value}" ;;
      PGHOST|PGPORT|PGUSER|PGDATABASE|PGSSLMODE) export "${key}=${value}" ;;
      "") ;;
      *) die "unsupported database URL parameter: ${key}" ;;
    esac
  done <<<"${output}"
  if [[ -n "${password}" ]]; then
    [[ "${password}" != *$'\n'* ]] || die "the database password cannot contain a newline"
    ensure_work_dir
    # The URL password wins over any ambient credential and never reaches argv.
    unset PGPASSWORD PGPASSFILE
    PGPASSFILE="${WORK_DIR}/pgpass"
    escaped="${password//\\/\\\\}"
    escaped="${escaped//:/\\:}"
    printf '*:*:*:*:%s\n' "${escaped}" >"${PGPASSFILE}"
    chmod 600 "${PGPASSFILE}"
    export PGPASSFILE
  fi
}

psql_at() {
  local url="$1"
  shift
  load_pg_env "${url}"
  PGCONNECT_TIMEOUT="${PGCONNECT_TIMEOUT:-10}" \
    psql -X -q -v ON_ERROR_STOP=1 -At "$@"
}

# A backup that cannot see every tenant is worse than no backup: it looks complete.
require_bypass_role() {
  local url="$1" can_bypass
  can_bypass="$(psql_at "${url}" -c \
    "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user")"
  [[ "${can_bypass}" == "t" ]] \
    || die "backup/restore must run as a superuser or a BYPASSRLS role"
}

# The no-audio contract is enforced here as a schema property. Only the two AES-GCM token
# blobs are expected; any other bytea column aborts the backup instead of shipping a payload.
assert_no_audio_payload() {
  local url="$1" allowlist unexpected large_objects
  allowlist="$(printf "'%s'," "${ALLOWED_BINARY_COLUMNS[@]}")"
  allowlist="${allowlist%,}"
  unexpected="$(psql_at "${url}" -c "
    SELECT table_name || '.' || column_name
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND data_type = 'bytea'
      AND table_name || '.' || column_name NOT IN (${allowlist})
    ORDER BY 1")"
  [[ -z "${unexpected}" ]] \
    || die "refusing backup: unexpected binary column(s): ${unexpected//$'\n'/, }"
  large_objects="$(psql_at "${url}" -c \
    "SELECT count(*) FROM pg_largeobject_metadata")"
  [[ "${large_objects}" == "0" ]] \
    || die "refusing backup: the database contains ${large_objects} large object(s)"
}

gpg_common() {
  gpg --batch --yes --quiet --pinentry-mode loopback \
    --cipher-algo AES256 \
    --s2k-digest-algo SHA512 \
    --s2k-count 65011712 \
    --compress-algo none \
    --passphrase-fd 3 \
    "$@" 3<<<"${BACKUP_PASSPHRASE}"
}

encrypt_stdin_to() {
  local output="$1"
  gpg_common --symmetric --output "${output}"
}

decrypt_stream() {
  local input="$1"
  gpg_common --decrypt "${input}"
}

decrypt_to() {
  local input="$1" output="$2"
  decrypt_stream "${input}" >"${output}"
}

deletion_log_sql() {
  cat <<'SQL'
SELECT jsonb_build_object(
  'version', 1,
  'generated_at', to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
  'retention_days', 30,
  'account_deletions', COALESCE((
    SELECT jsonb_agg(jsonb_build_object(
      'id', id,
      'former_user_id', former_user_id,
      'status', status,
      'delete_events', delete_events,
      'expires_at', expires_at,
      'created_at', created_at,
      'completed_at', completed_at))
    FROM account_deletions), '[]'::jsonb),
  'users', COALESCE((
    SELECT jsonb_agg(jsonb_build_object(
      'id', id, 'status', status, 'deleted_at', deleted_at))
    FROM users WHERE status <> 'active'), '[]'::jsonb),
  'audios', COALESCE((
    SELECT jsonb_agg(jsonb_build_object(
      'id', id, 'user_id', user_id, 'deleted_at', deleted_at))
    FROM audios WHERE deleted_at IS NOT NULL), '[]'::jsonb),
  'subjects', COALESCE((
    SELECT jsonb_agg(jsonb_build_object(
      'id', id, 'user_id', user_id, 'deleted_at', deleted_at))
    FROM subjects WHERE deleted_at IS NOT NULL), '[]'::jsonb),
  'conversations', COALESCE((
    SELECT jsonb_agg(jsonb_build_object(
      'id', id, 'user_id', user_id, 'deleted_at', deleted_at))
    FROM conversations WHERE deleted_at IS NOT NULL), '[]'::jsonb)
)::text;
SQL
}

export_deletions() {
  local url="$1" output="$2"
  require_bypass_role "${url}"
  deletion_log_sql | psql_at "${url}" -f - | encrypt_stdin_to "${output}"
}

write_manifest() {
  local dump="$1" log="$2" manifest="$3" created="$4"
  local dump_sha log_sha document
  dump_sha="$(sha256sum "${dump}" | awk '{print $1}')"
  log_sha="$(sha256sum "${log}" | awk '{print $1}')"
  document="{\"format\":1,\"created_at\":\"${created}\",\"retention_days\":${RETENTION_DAYS},"
  document+="\"dump\":\"$(basename "${dump}")\",\"dump_sha256\":\"${dump_sha}\","
  document+="\"deletion_log\":\"$(basename "${log}")\","
  document+="\"deletion_log_sha256\":\"${log_sha}\"}"
  printf '%s\n' "${document}" >"${manifest}"
  chmod 600 "${manifest}"
}

manifest_for() {
  local backup="$1"
  case "${backup}" in
    *.dump.gpg) printf '%s.manifest.json' "${backup%.dump.gpg}" ;;
    *) printf '%s.manifest.json' "${backup}" ;;
  esac
}

# `verify` is intentionally independent of any database: it decides whether the archive is
# still inside the D8 window and whether its bytes are the ones the manifest recorded.
# A manifest that pins a deletion log makes the pair the unit of verification: a missing log
# is an error (or an explicit warning with --allow-missing-deletion-log), never a silent pass.
verify_archive() {
  local backup="$1" manifest="$2" allow_missing="${3:-0}"
  [[ -f "${backup}" ]] || die "backup not found: ${backup}"
  [[ -f "${manifest}" ]] || die "manifest not found: ${manifest}"
  python3 - "${manifest}" "${backup}" "${allow_missing}" <<'PY'
import hashlib
import json
import os
import sys
from datetime import UTC, datetime

# The manifest is not secret, so retention is checked against the runbook's hard maximum
# instead of trusting an edited value.
MAX_RETENTION_DAYS = 30
manifest_path, backup_path, allow_missing = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
with open(manifest_path, encoding="utf-8") as handle:
    manifest = json.load(handle)
if manifest.get("format") != 1:
    raise SystemExit("backup_restore: unsupported manifest format")
if int(manifest["retention_days"]) != MAX_RETENTION_DAYS:
    raise SystemExit("backup_restore: manifest retention is not the D8 30-day policy")
created = datetime.fromisoformat(manifest["created_at"].replace("Z", "+00:00"))
age_days = (datetime.now(UTC) - created).total_seconds() / 86_400
if age_days > MAX_RETENTION_DAYS:
    raise SystemExit(
        f"backup_restore: backup expired ({age_days:.1f} days > {MAX_RETENTION_DAYS} days)"
    )
with open(backup_path, "rb") as handle:
    digest = hashlib.file_digest(handle, "sha256").hexdigest()
if digest != manifest["dump_sha256"]:
    raise SystemExit("backup_restore: backup checksum does not match the manifest")
log_name = manifest.get("deletion_log")
if log_name:
    log_path = os.path.join(os.path.dirname(backup_path), log_name)
    if os.path.exists(log_path):
        with open(log_path, "rb") as handle:
            log_digest = hashlib.file_digest(handle, "sha256").hexdigest()
        if log_digest != manifest.get("deletion_log_sha256"):
            raise SystemExit(
                "backup_restore: deletion log checksum does not match the manifest"
            )
    else:
        print(
            "backup_restore: WARNING: the manifest pins a deletion log that is missing "
            f"next to the backup: {log_path}",
            file=sys.stderr,
        )
        if not allow_missing:
            raise SystemExit(
                "backup_restore: the pinned deletion log is missing; restoring this archive "
                "without it can resurrect data deleted after the backup "
                "(pass --allow-missing-deletion-log to verify the dump alone)"
            )
print(
    f"backup_restore: backup OK ({age_days:.1f} days old, "
    f"retention {MAX_RETENTION_DAYS} days)"
)
PY
}

backup_log_for() {
  local dump="$1"
  printf '%s.deletions.json.gpg' "${dump%.dump.gpg}"
}

manifest_log_sha() {
  python3 - "$1" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    manifest = json.load(handle)
print(manifest.get("deletion_log_sha256", ""))
PY
}

sha256_of() {
  sha256sum "$1" | awk '{print $1}'
}

# Attach a log exported after later deletions to the backup whose manifest must pin it. The
# manifest is written to a temporary file and renamed, so a crash cannot leave a half JSON.
update_manifest_log() {
  local manifest="$1" log="$2" temporary="${1}.tmp"
  PARTIAL_FILES+=("${temporary}")
  python3 - "${manifest}" "${log}" "${temporary}" <<'PY'
import hashlib
import json
import os
import sys

manifest_path, log_path, temporary_path = sys.argv[1], sys.argv[2], sys.argv[3]
with open(manifest_path, encoding="utf-8") as handle:
    manifest = json.load(handle)
with open(log_path, "rb") as handle:
    digest = hashlib.file_digest(handle, "sha256").hexdigest()
manifest["deletion_log"] = os.path.basename(log_path)
manifest["deletion_log_sha256"] = digest
with open(temporary_path, "w", encoding="utf-8") as handle:
    json.dump(manifest, handle, separators=(",", ":"))
    handle.write("\n")
os.chmod(temporary_path, 0o600)
PY
  mv "${temporary}" "${manifest}"
}

cmd_backup() {
  local url="" out="" label="manual"
  while (($#)); do
    case "$1" in
      --database-url) url="${2:-}"; shift 2 ;;
      --out) out="${2:-}"; shift 2 ;;
      --label) label="${2:-}"; shift 2 ;;
      *) die "backup: unknown argument $1" ;;
    esac
  done
  [[ -n "${url}" && -n "${out}" ]] || die "backup: --database-url and --out are required"
  [[ "${label}" =~ ^[A-Za-z0-9._-]+$ ]] || die "backup: invalid --label"
  url="$(sync_url "${url}")"
  mkdir -p "${out}"
  chmod 700 "${out}"
  require_bypass_role "${url}"
  assert_no_audio_payload "${url}"
  load_pg_env "${url}"

  local stamp base dump log manifest created partial_dump partial_log
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  base="maulwurf-${stamp}-${label}"
  dump="${out}/${base}.dump.gpg"
  log="$(backup_log_for "${dump}")"
  manifest="${out}/${base}.manifest.json"
  [[ ! -e "${dump}" && ! -e "${log}" && ! -e "${manifest}" ]] \
    || die "backup: ${base} already exists"
  created="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  partial_dump="${out}/.${base}.dump.partial"
  partial_log="${out}/.${base}.log.partial"
  # A failed pipeline leaves no half archive behind.
  PARTIAL_FILES+=("${partial_dump}" "${partial_log}")

  pg_dump --format=custom --no-owner --no-privileges \
    | encrypt_stdin_to "${partial_dump}"
  export_deletions "${url}" "${partial_log}"
  mv "${partial_dump}" "${dump}"
  mv "${partial_log}" "${log}"
  write_manifest "${dump}" "${log}" "${manifest}" "${created}"
  cmd_prune --out "${out}" >/dev/null

  printf 'backup_restore: created %s\n' "$(basename "${dump}")"
  printf 'backup_restore: created %s\n' "$(basename "${log}")"
  printf 'backup_restore: created %s\n' "$(basename "${manifest}")"
}

cmd_export_deletions() {
  local url="" out="" refresh_backup="" manifest=""
  while (($#)); do
    case "$1" in
      --database-url) url="${2:-}"; shift 2 ;;
      --out) out="${2:-}"; shift 2 ;;
      --refresh-backup) refresh_backup="${2:-}"; shift 2 ;;
      *) die "export-deletions: unknown argument $1" ;;
    esac
  done
  [[ -n "${url}" ]] || die "export-deletions: --database-url is required"
  if [[ -n "${refresh_backup}" ]]; then
    [[ -z "${out}" ]] \
      || die "export-deletions: --out and --refresh-backup are mutually exclusive"
    [[ -f "${refresh_backup}" ]] || die "backup not found: ${refresh_backup}"
    manifest="$(manifest_for "${refresh_backup}")"
    # Replacing a log requires the dump and any log already pinned to be intact. A missing
    # log is allowed here because refreshing is precisely how it is restored to the set.
    verify_archive "${refresh_backup}" "${manifest}" 1
    out="$(backup_log_for "${refresh_backup}")"
  else
    [[ -n "${out}" ]] || die "export-deletions: --out or --refresh-backup is required"
  fi
  url="$(sync_url "${url}")"
  mkdir -p "$(dirname "${out}")"
  local partial="${out}.partial"
  PARTIAL_FILES+=("${partial}")
  export_deletions "${url}" "${partial}"
  mv "${partial}" "${out}"
  if [[ -n "${refresh_backup}" ]]; then
    update_manifest_log "${manifest}" "${out}"
    printf 'backup_restore: refreshed %s and updated %s\n' \
      "$(basename "${out}")" "$(basename "${manifest}")"
  else
    printf 'backup_restore: exported %s\n' "$(basename "${out}")"
  fi
}

require_clean_database() {
  local url="$1" tables extensions roles
  tables="$(psql_at "${url}" -c "
    SELECT count(*) FROM pg_tables
    WHERE schemaname NOT IN ('pg_catalog', 'information_schema')")"
  [[ "${tables}" == "0" ]] \
    || die "restore target is not clean (${tables} application tables)"
  extensions="$(psql_at "${url}" -c "
    SELECT count(*) FROM pg_extension WHERE extname <> 'plpgsql'")"
  [[ "${extensions}" == "0" ]] \
    || die "restore target is not clean (${extensions} non-default extensions)"
  roles="$(psql_at "${url}" -c "
    SELECT count(*) FROM pg_roles
    WHERE rolname IN ('maulwurf_app', 'maulwurf_jobs', 'maulwurf_ingest')")"
  [[ "${roles}" == "3" ]] \
    || die "target cluster is missing the runtime roles; pg_dump does not include them"
}

# The JSON log is identifier-only. Dollar-quoting with a random tag avoids COPY escaping
# corrupting strings; the tag is generated at run time and cannot appear in the document.
load_log_sql() {
  local log="$1"
  python3 - "${log}" <<'PY'
import json
import sys
import uuid

with open(sys.argv[1], encoding="utf-8") as handle:
    document = json.load(handle)
tag = f"mw_{uuid.uuid4().hex}"
payload = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
print("CREATE TEMP TABLE restore_log (payload jsonb NOT NULL);")
print(f"INSERT INTO restore_log (payload) VALUES (${tag}${payload}${tag}$);")
PY
}

reapply_tombstones() {
  local url="$1" log="$2" sql_file
  ensure_work_dir
  sql_file="${WORK_DIR}/reapply.sql"
  {
    printf 'BEGIN;\n'
    load_log_sql "${log}"
    cat "${REAPPLY_SQL}"
    printf '\nCOMMIT;\n'
  } >"${sql_file}"
  psql_at "${url}" -f "${sql_file}"
}

cmd_restore() {
  local url="" backup="" deletion_log="" confirmed=0 allow_missing=0
  local manifest="" expected_sha="" actual_sha=""
  while (($#)); do
    case "$1" in
      --database-url) url="${2:-}"; shift 2 ;;
      --backup) backup="${2:-}"; shift 2 ;;
      --deletion-log) deletion_log="${2:-}"; shift 2 ;;
      --allow-missing-deletion-log) allow_missing=1; shift ;;
      --confirm-clean) confirmed=1; shift ;;
      *) die "restore: unknown argument $1" ;;
    esac
  done
  [[ -n "${url}" && -n "${backup}" ]] \
    || die "restore: --database-url and --backup are required"
  ((confirmed == 1)) \
    || die "restore: pass --confirm-clean to acknowledge the destructive restore"
  url="$(sync_url "${url}")"
  manifest="$(manifest_for "${backup}")"
  verify_archive "${backup}" "${manifest}" "${allow_missing}"
  require_bypass_role "${url}"
  require_clean_database "${url}"

  # Fail closed: replay the log bundled with the backup unless the operator names one.
  if [[ -z "${deletion_log}" ]]; then
    deletion_log="$(backup_log_for "${backup}")"
    if [[ ! -f "${deletion_log}" ]]; then
      ((allow_missing == 1)) \
        || die "restore: deletion log not found: ${deletion_log}; pass --deletion-log FILE or --allow-missing-deletion-log (the latter can resurrect deleted data)"
      warn "restoring without a deletion log; only tombstones already inside the dump are replayed and data deleted after the backup can reappear"
      deletion_log=""
    fi
  else
    [[ -f "${deletion_log}" ]] || die "deletion log not found: ${deletion_log}"
  fi

  # Any explicit log is the manifest's log: its bytes must be the pinned ones. This also
  # covers a log passed from another backup, which would otherwise replay arbitrary work.
  if [[ -n "${deletion_log}" ]]; then
    expected_sha="$(manifest_log_sha "${manifest}")"
    [[ -n "${expected_sha}" ]] \
      || die "restore: the manifest does not pin a deletion log; refusing to replay ${deletion_log}"
    actual_sha="$(sha256_of "${deletion_log}")"
    [[ "${actual_sha}" == "${expected_sha}" ]] \
      || die "restore: deletion log checksum does not match the manifest: ${deletion_log}"
  fi

  ensure_work_dir
  local log_json="${WORK_DIR}/deletions.json"
  if [[ -n "${deletion_log}" ]]; then
    decrypt_to "${deletion_log}" "${log_json}"
  else
    printf '{"version":1}\n' >"${log_json}"
  fi

  load_pg_env "${url}"
  [[ -n "${PGDATABASE:-}" ]] || die "restore: the database URL must name a database"
  # pg_restore requires an explicit target even when PGDATABASE is set. Only the database
  # name goes in argv; user, host and the password stay in PG* variables and PGPASSFILE.
  # pg_restore is not transactional with the replay: once it starts writing, a failure leaves
  # a target that contains rows deleted after the backup. The operator must discard it.
  if ! decrypt_stream "${backup}" \
    | pg_restore --clean --if-exists --no-owner --no-privileges --exit-on-error \
      --dbname="${PGDATABASE}"; then
    die "pg_restore failed after it may have written rows; discard the target database and restore into a clean one"
  fi
  if ! reapply_tombstones "${url}" "${log_json}"; then
    die "tombstone replay failed after pg_restore wrote the dump (including rows deleted later); discard the target database and restore into a clean one"
  fi

  if [[ -n "${deletion_log}" ]]; then
    printf 'backup_restore: restored %s and reapplied deletion log %s\n' \
      "$(basename "${backup}")" "$(basename "${deletion_log}")"
  else
    printf 'backup_restore: restored %s; no deletion log was replayed (explicitly allowed)\n' \
      "$(basename "${backup}")"
  fi
}

cmd_verify() {
  local backup="" manifest="" allow_missing=0
  while (($#)); do
    case "$1" in
      --backup) backup="${2:-}"; shift 2 ;;
      --manifest) manifest="${2:-}"; shift 2 ;;
      --allow-missing-deletion-log) allow_missing=1; shift ;;
      *) die "verify: unknown argument $1" ;;
    esac
  done
  [[ -n "${backup}" ]] || die "verify: --backup is required"
  [[ -n "${manifest}" ]] || manifest="$(manifest_for "${backup}")"
  verify_archive "${backup}" "${manifest}" "${allow_missing}"
}

cmd_prune() {
  local out="" removed=0 path
  while (($#)); do
    case "$1" in
      --out) out="${2:-}"; shift 2 ;;
      *) die "prune: unknown argument $1" ;;
    esac
  done
  [[ -n "${out}" ]] || die "prune: --out is required"
  [[ -d "${out}" ]] || die "prune: directory not found: ${out}"
  while IFS= read -r -d '' path; do
    rm -f "${path}"
    removed=$((removed + 1))
  done < <(find "${out}" -maxdepth 1 -type f -name 'maulwurf-*' \
    -mmin +$((RETENTION_DAYS * 24 * 60)) -print0)
  printf 'backup_restore: pruned %d file(s) older than %d days\n' "${removed}" \
    "${RETENTION_DAYS}"
}

main() {
  (($#)) || die "missing command; try --help"
  local command_name="$1"
  shift
  case "${command_name}" in
    backup) load_passphrase; require_command pg_dump; require_command gpg
            require_command psql; require_command python3; cmd_backup "$@" ;;
    export-deletions) load_passphrase; require_command gpg; require_command psql
                      require_command python3; cmd_export_deletions "$@" ;;
    restore) load_passphrase; require_command pg_restore; require_command psql
             require_command gpg; require_command python3; cmd_restore "$@" ;;
    verify) require_command python3; cmd_verify "$@" ;;
    prune) cmd_prune "$@" ;;
    --help|-h|help) usage ;;
    *) die "unknown command: ${command_name}" ;;
  esac
}

main "$@"

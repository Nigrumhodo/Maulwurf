#!/usr/bin/env bash
# C-S2-JF-13 (J2.10): measure the Caddy edge limits with synthetic bytes and
# repeat the J1.3 no-buffering check through the same Caddyfile.
#
# The body is generated from /dev/zero in RAM; nothing is written to disk or kept.
# A 220 MB body is streamed so the effective request_body.max_size is exercised
# end to end, not just declared in the configuration.
#
# Backends:
#   MODE=docker (default when the docker CLI exists): caddy:2.8-alpine and the
#     Python sink in a throw-away network; does not need the compose stack.
#   MODE=native (or no docker): requires a Caddy 2.8 binary (CADDY_BIN or PATH)
#     and python3. The temporary Caddyfile changes the site port and the
#     upstream to 127.0.0.1, and uses an isolated XDG storage directory.
#
# Cases and expected results:
#   1 MiB upload                         -> 202
#   request_body.max_size upload         -> 202, sink receives every byte
#   one byte over the upload ceiling     -> 413, sink receives nothing
#   one byte over the @api 10 MB ceiling -> 413
#   one byte over the web fallback 10 MB ceiling -> 413
#   read_body=2s and a 5 s upload        -> connection cut, sink receives nothing
#   no new proxy files, deleted fds or request bodies in the log
#
# Usage:
#   bash scripts/load/ingress_limits.sh
#   MODE=native CADDY_BIN=/path/to/caddy bash scripts/load/ingress_limits.sh
#   TEST_PORT=9443 SLOW_PORT=9444 SINK_PORT=18000 ...
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CADDYFILE="${CADDYFILE:-$ROOT/infra/Caddyfile}"
CHECKER="$ROOT/scripts/ci/check_caddy_limits.py"
MODE="${MODE:-auto}"
TEST_PORT="${TEST_PORT:-9443}"
SLOW_PORT="${SLOW_PORT:-9444}"
SINK_PORT="${SINK_PORT:-18000}"
CADDY_IMAGE="caddy:2.8-alpine@sha256:af32e97399febea808609119bb21544d0265c58a02836576e32a2d082c262c17"
SINK_IMAGE="python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f"
SINK_NAME="mw-ingress-sink-$$"
CADDY_NAME="mw-ingress-caddy-$$"
SLOW_CADDY_NAME="mw-ingress-caddy-slow-$$"
NETWORK="mw-ingress-$$"

SINK_PID=""
CADDY_PID=""
SLOW_CADDY_PID=""
TMP_DIR=""

die() {
	echo "FAIL: $*" >&2
	exit 1
}

cleanup() {
	set +e
	[ -n "$CADDY_PID" ] && kill "$CADDY_PID" 2>/dev/null
	[ -n "$SLOW_CADDY_PID" ] && kill "$SLOW_CADDY_PID" 2>/dev/null
	[ -n "$SINK_PID" ] && kill "$SINK_PID" 2>/dev/null
	[ -n "$CADDY_PID" ] && wait "$CADDY_PID" 2>/dev/null
	[ -n "$SLOW_CADDY_PID" ] && wait "$SLOW_CADDY_PID" 2>/dev/null
	[ -n "$SINK_PID" ] && wait "$SINK_PID" 2>/dev/null
	if [ "$MODE" = "docker" ]; then
		docker rm -f "$SINK_NAME" "$CADDY_NAME" "$SLOW_CADDY_NAME" >/dev/null 2>&1
		docker network rm "$NETWORK" >/dev/null 2>&1
	fi
	[ -n "$TMP_DIR" ] && rm -rf "$TMP_DIR"
}
trap cleanup EXIT INT TERM

if [ "$MODE" = "auto" ]; then
	if command -v docker >/dev/null 2>&1; then
		MODE="docker"
	else
		MODE="native"
	fi
fi
case "$MODE" in
docker)
	command -v docker >/dev/null 2>&1 || die "MODE=docker requires the docker CLI"
	;;
native)
	CADDY_BIN="${CADDY_BIN:-$(command -v caddy || true)}"
	[ -n "$CADDY_BIN" ] || die "MODE=native requires CADDY_BIN or caddy on PATH"
	"$CADDY_BIN" version | grep -q '^v2\.8\.' ||
		die "Caddy 2.8.x is required (compose pins caddy:2.8-alpine)"
	command -v python3 >/dev/null 2>&1 || die "MODE=native requires python3"
	;;
*)
	die "MODE must be auto, docker or native"
	;;
esac

SIZES=$(python3 "$CHECKER" --caddyfile "$CADDYFILE" --print-sizes) ||
	die "the Caddyfile limits do not pass $CHECKER"
read -r UPLOAD_MAX_BYTES API_JSON_MAX_BYTES API_CONTRACT_MAX_BYTES <<<"$SIZES"
[ -n "$UPLOAD_MAX_BYTES" ] && [ -n "$API_JSON_MAX_BYTES" ] &&
	[ -n "$API_CONTRACT_MAX_BYTES" ] || die "could not read the Caddyfile sizes"

TMP_DIR="$(mktemp -d)"
UPLOAD_URL="https://localhost:${TEST_PORT}/audios/smoke/content?attempt_id=smoke"
API_URL="https://localhost:${TEST_PORT}/subjects"
FALLBACK_URL="https://localhost:${TEST_PORT}/not-a-route"
SLOW_URL="https://localhost:${SLOW_PORT}/audios/smoke/content?attempt_id=smoke"

render_docker() {
	local out="$1" slow="$2"
	sed -e "s|api:8000|${SINK_NAME}:8000|g" -e "s|web:3000|${SINK_NAME}:8000|g" \
		"$CADDYFILE" >"$out"
	grep -q "${SINK_NAME}:8000" "$out" || die "could not point the Caddyfile at the sink"
	if [ "$slow" = "yes" ]; then
		sed '0,/read_body 20m/s//read_body 2s/' "$out" >"$out.slow"
		mv "$out.slow" "$out"
		grep -q 'read_body 2s' "$out" || die "could not render the 2s read_body copy"
	fi
}

render_native() {
	local out="$1" port="$2" http_port="$3" slow="$4"
	awk -v port="$port" -v http_port="$http_port" -v sink="127.0.0.1:${SINK_PORT}" '
		/^https:\/\/localhost \{/ { sub(/localhost/, "localhost:" port) }
		/^\tadmin off$/ {
			print
			print "\tskip_install_trust"
			print "\tpersist_config off"
			print "\thttp_port " http_port
			next
		}
		{ gsub(/api:8000/, sink); gsub(/web:3000/, sink); print }
	' "$CADDYFILE" >"$out"
	grep -q "localhost:${port}" "$out" || die "could not set the native site port"
	if [ "$slow" = "yes" ]; then
		sed '0,/read_body 20m/s//read_body 2s/' "$out" >"$out.slow"
		mv "$out.slow" "$out"
		grep -q 'read_body 2s' "$out" || die "could not render the 2s read_body copy"
	fi
}

sink_log() {
	if [ "$MODE" = "docker" ]; then
		docker logs "$SINK_NAME" 2>&1
	elif [ -f "$TMP_DIR/sink.log" ]; then
		cat "$TMP_DIR/sink.log"
	fi
}

caddy_log() {
	if [ "$MODE" = "docker" ]; then
		docker logs "$CADDY_NAME" 2>&1
	elif [ -f "$TMP_DIR/caddy.log" ]; then
		cat "$TMP_DIR/caddy.log"
	fi
}

slow_caddy_log() {
	if [ "$MODE" = "docker" ]; then
		docker logs "$SLOW_CADDY_NAME" 2>&1
	elif [ -f "$TMP_DIR/slow-caddy.log" ]; then
		cat "$TMP_DIR/slow-caddy.log"
	fi
}

sink_count() {
	sink_log | grep -c '^received ' || true
}

sink_last_bytes() {
	sink_log | grep '^received ' | tail -1 | awk '{print $2}'
}

wait_ready() {
	local port="$1" label="$2" log_fn="$3" code
	for _ in $(seq 1 80); do
		code=$(curl -ks -o /dev/null -w '%{http_code}' --max-time 2 \
			"https://localhost:${port}/internal/ping" 2>/dev/null || true)
		[ "$code" = "404" ] && return 0
		sleep 0.5
	done
	echo "FAIL: $label did not answer on https://localhost:${port}" >&2
	"$log_fn" | tail -20 >&2 || true
	return 1
}

send_stream() {
	local bytes="$1" url="$2" method="$3" out rc
	set +e
	out=$(head -c "$bytes" /dev/zero | curl -ks --http1.1 -H 'Expect:' -o /dev/null \
		-w '%{http_code}' -X "$method" -T - "$url" 2>/dev/null)
	rc=$?
	set -e
	printf '%s %s' "${out:-000}" "$rc"
}

# A 64 KiB chunk every 0.5 s keeps the request alive for 5 s. With the production
# read_body=20m this finishes; with the 2 s copy it is cut before the body ends.
send_slow() {
	local url="$1" out rc
	set +e
	out=$(
		python3 - 2>/dev/null <<'PY' | curl -ks --http1.1 --max-time 20 -H 'Expect:' \
			-o /dev/null -w '%{http_code}' -X PUT -T - "$url" 2>/dev/null
import sys
import time

try:
    for _ in range(10):
        sys.stdout.buffer.write(b"x" * 65536)
        sys.stdout.buffer.flush()
        time.sleep(0.5)
except BrokenPipeError:
    pass
PY
	)
	rc=$?
	set -e
	printf '%s %s' "${out:-000}" "$rc"
}

caddy_hwm_kb() {
	if [ "$MODE" = "docker" ]; then
		docker exec "$CADDY_NAME" sh -lc 'grep VmHWM /proc/1/status' 2>/dev/null |
			awk '{print $2}'
	elif [ -n "$CADDY_PID" ]; then
		grep VmHWM "/proc/${CADDY_PID}/status" 2>/dev/null | awk '{print $2}'
	fi
}

marker_setup() {
	if [ "$MODE" = "docker" ]; then
		docker exec "$CADDY_NAME" sh -lc 'touch /tmp/marker' >/dev/null
	else
		touch "$TMP_DIR/marker"
	fi
}

files_since_marker() {
	if [ "$MODE" = "docker" ]; then
		docker exec "$CADDY_NAME" sh -lc \
			'find / -xdev -newer /tmp/marker -type f 2>/dev/null | grep -vE "^/(data|config)/"' ||
			true
	else
		find "$TMP_DIR/home-main/data" "$TMP_DIR/home-main/config" \
			-type f -newer "$TMP_DIR/marker" 2>/dev/null |
			grep -vE '/(certificates|pki)/' || true
	fi
}

deleted_fds() {
	if [ "$MODE" = "docker" ]; then
		docker exec "$CADDY_NAME" sh -lc \
			'ls -l /proc/[0-9]*/fd 2>/dev/null | grep -c deleted' || true
	else
		ls -l "/proc/${CADDY_PID}/fd" 2>/dev/null | grep -c deleted || true
	fi
}

FAILURES=0
ok() {
	printf '  OK   %-44s %s\n' "$1" "$2"
}
bad() {
	printf '  FAIL %-44s %s\n' "$1" "$2" >&2
	FAILURES=$((FAILURES + 1))
}
expect_eq() {
	local label="$1" got="$2" want="$3"
	if [ "$got" = "$want" ]; then ok "$label" "$got"; else bad "$label" "got=$got want=$want"; fi
}

echo "Caddyfile: $CADDYFILE"
echo "Mode: $MODE | upload ceiling: $UPLOAD_MAX_BYTES bytes | @api JSON ceiling: $API_JSON_MAX_BYTES bytes"
echo "API provisional contract: $API_CONTRACT_MAX_BYTES bytes"
echo "Date: $(date -u +%Y-%m-%dT%H:%M:%SZ) | Host: $(uname -srm)"
echo

if [ "$MODE" = "docker" ]; then
	render_docker "$TMP_DIR/Caddyfile" "no"
	render_docker "$TMP_DIR/SlowCaddyfile" "yes"
	docker network create "$NETWORK" >/dev/null
	docker run -d --rm --name "$SINK_NAME" --network "$NETWORK" \
		-v "$ROOT/scripts/load/sink_server.py:/sink.py:ro" \
		"$SINK_IMAGE" python /sink.py 8000 >/dev/null
	docker run -d --rm --name "$CADDY_NAME" --network "$NETWORK" \
		-p "127.0.0.1:${TEST_PORT}:443" \
		-v "$TMP_DIR/Caddyfile:/etc/caddy/Caddyfile:ro" "$CADDY_IMAGE" >/dev/null
	docker run -d --rm --name "$SLOW_CADDY_NAME" --network "$NETWORK" \
		-p "127.0.0.1:${SLOW_PORT}:443" \
		-v "$TMP_DIR/SlowCaddyfile:/etc/caddy/Caddyfile:ro" "$CADDY_IMAGE" >/dev/null
else
	render_native "$TMP_DIR/Caddyfile" "$TEST_PORT" "18080" "no"
	render_native "$TMP_DIR/SlowCaddyfile" "$SLOW_PORT" "18081" "yes"
	mkdir -p "$TMP_DIR/home-main/data" "$TMP_DIR/home-main/config"
	mkdir -p "$TMP_DIR/home-slow/data" "$TMP_DIR/home-slow/config"
	python3 "$ROOT/scripts/load/sink_server.py" "$SINK_PORT" >"$TMP_DIR/sink.log" 2>&1 &
	SINK_PID=$!
	sleep 1
	XDG_DATA_HOME="$TMP_DIR/home-main/data" XDG_CONFIG_HOME="$TMP_DIR/home-main/config" \
		"$CADDY_BIN" run --config "$TMP_DIR/Caddyfile" --adapter caddyfile \
		>"$TMP_DIR/caddy.log" 2>&1 &
	CADDY_PID=$!
	XDG_DATA_HOME="$TMP_DIR/home-slow/data" XDG_CONFIG_HOME="$TMP_DIR/home-slow/config" \
		"$CADDY_BIN" run --config "$TMP_DIR/SlowCaddyfile" --adapter caddyfile \
		>"$TMP_DIR/slow-caddy.log" 2>&1 &
	SLOW_CADDY_PID=$!
fi

wait_ready "$TEST_PORT" "main Caddy" caddy_log
wait_ready "$SLOW_PORT" "slow Caddy" slow_caddy_log

# Warm up the sink before taking the baseline count.
code=""
for _ in $(seq 1 20); do
	read -r code _ <<<"$(send_stream 1 "$UPLOAD_URL" PUT)"
	[ "$code" = "202" ] && break
	sleep 0.5
done
[ "$code" = "202" ] || die "the sink did not accept the 1-byte warm-up (HTTP $code)"
BASE_COUNT=$(sink_count)

read -r code rc <<<"$(send_stream 1048576 "$UPLOAD_URL" PUT)"
expect_eq "1 MiB upload" "$code" "202"
expect_eq "1 MiB received by the sink" "$(sink_last_bytes)" "1048576"

marker_setup
read -r code rc <<<"$(send_stream "$UPLOAD_MAX_BYTES" "$UPLOAD_URL" PUT)"
expect_eq "exact request_body.max_size" "$code" "202"
expect_eq "exact bytes received by the sink" "$(sink_last_bytes)" "$UPLOAD_MAX_BYTES"

read -r code rc <<<"$(send_stream $((UPLOAD_MAX_BYTES + 1)) "$UPLOAD_URL" PUT)"
expect_eq "one byte over the upload ceiling" "$code" "413"

read -r code rc <<<"$(send_stream 10000001 "$API_URL" POST)"
expect_eq "one byte over the @api 10 MB ceiling" "$code" "413"

read -r code rc <<<"$(send_stream 10000001 "$FALLBACK_URL" POST)"
expect_eq "one byte over the web fallback 10 MB ceiling" "$code" "413"

expect_eq "sink received only valid bodies" "$(sink_count)" "$((BASE_COUNT + 2))"

read -r code rc <<<"$(send_slow "$SLOW_URL")"
if [ "$code" = "000" ] || [ "$code" = "408" ] || [ "$rc" -ne 0 ]; then
	ok "read_body=2s cuts a 5 s upload" "http=$code rc=$rc"
else
	bad "read_body=2s cuts a 5 s upload" "http=$code rc=$rc"
fi
expect_eq "timed-out upload never reached the sink" "$(sink_count)" "$((BASE_COUNT + 2))"

FILES=$(files_since_marker)
DELETED=$(deleted_fds)
BODIES=$(caddy_log | grep -ciE 'BEGIN|Content-Disposition|filename=' || true)
HWM=$(caddy_hwm_kb)

if [ -z "$FILES" ]; then
	ok "no new proxy files from the upload" "none"
else
	bad "no new proxy files from the upload" "$FILES"
fi
if [ "${DELETED:-0}" = "0" ]; then
	ok "no deleted file descriptors (spool)" "0"
else
	bad "no deleted file descriptors (spool)" "$DELETED"
fi
if [ "${BODIES:-0}" = "0" ]; then
	ok "no request bodies in the proxy log" "0"
else
	bad "no request bodies in the proxy log" "$BODIES"
fi
if [ -n "$HWM" ]; then
	echo "  INFO Caddy peak RSS (VmHWM): ${HWM} kB"
	if [ "$HWM" -lt 262144 ]; then
		ok "peak RSS below 256 MiB (no whole-body buffer)" "${HWM} kB"
	else
		bad "peak RSS below 256 MiB (no whole-body buffer)" "${HWM} kB"
	fi
fi

echo
if [ "$FAILURES" -eq 0 ]; then
	echo "OK: C-S2-JF-13 — ingress limits and no-buffering checks passed."
	exit 0
fi
echo "FAIL: $FAILURES C-S2-JF-13 case(s) failed." >&2
exit 1

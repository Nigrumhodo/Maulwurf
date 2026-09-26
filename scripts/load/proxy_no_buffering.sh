#!/usr/bin/env bash
# I-S1-JF-02 (J1.3): sube un flujo grande a través de Caddy y falla si aparece
# cualquier rastro de buffering (archivos, spools o bodies en logs).
#
# Requiere el stack de infra levantado (docker compose) y genera el dato en RAM
# (/dev/zero): no se versiona ni se conserva audio ni artefacto alguno.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SIZE_BYTES="${SIZE_BYTES:-536870912}" # 512 MiB por defecto
TEST_PORT="${TEST_PORT:-9443}"
NETWORK="${NETWORK:-maulwurf_edge}"
SINK_NAME="mw-sink"
CADDY_NAME="mw-caddy-test"
SINK_IMAGE="python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f"
CADDY_IMAGE="caddy:2.8-alpine@sha256:af32e97399febea808609119bb21544d0265c58a02836576e32a2d082c262c17"
TMP_CADDYFILE="$(mktemp)"

cleanup() {
	docker rm -f "$SINK_NAME" "$CADDY_NAME" >/dev/null 2>&1 || true
	rm -f "$TMP_CADDYFILE"
}
trap cleanup EXIT

docker rm -f "$SINK_NAME" "$CADDY_NAME" >/dev/null 2>&1 || true
# Mismo Caddyfile de producción; solo el upstream del PUT (bloque @upload) se
# sustituye por el sink para poder consumir el cuerpo sin endpoint real.
sed "/handle @upload {/,/^\t}/ s/api:8000/${SINK_NAME}:8000/" "$ROOT/infra/Caddyfile" >"$TMP_CADDYFILE"
grep -q "${SINK_NAME}:8000" "$TMP_CADDYFILE" || {
	echo "no se pudo sustituir el upstream de upload en el Caddyfile" >&2
	exit 1
}

docker run -d --rm --name "$SINK_NAME" --network "$NETWORK" \
	-v "$ROOT/scripts/load/sink_server.py:/sink.py:ro" \
	"$SINK_IMAGE" python /sink.py 8000 >/dev/null

docker run -d --rm --name "$CADDY_NAME" --network "$NETWORK" \
	-p "127.0.0.1:${TEST_PORT}:443" \
	-v "$TMP_CADDYFILE:/etc/caddy/Caddyfile:ro" "$CADDY_IMAGE" >/dev/null

for _ in $(seq 1 30); do
	if curl -ks -o /dev/null "https://localhost:${TEST_PORT}/"; then break; fi
	sleep 1
done

docker exec "$CADDY_NAME" sh -lc 'touch /tmp/marker' >/dev/null

echo "Subiendo ${SIZE_BYTES} bytes en streaming vía Caddy..."
HTTP=$(head -c "$SIZE_BYTES" /dev/zero | curl -ks -X PUT -T - \
	-H 'Content-Type: application/octet-stream' \
	"https://localhost:${TEST_PORT}/audios/smoke/content?attempt_id=smoke" \
	-o /dev/null -w '%{http_code}')
echo "HTTP ${HTTP}"

FILES=$(docker exec "$CADDY_NAME" sh -lc \
	'find / -xdev -newer /tmp/marker -type f 2>/dev/null | grep -vE "^/(data|config)/"' || true)
DELETED=$(docker exec "$CADDY_NAME" sh -lc \
	'ls -l /proc/[0-9]*/fd 2>/dev/null | grep deleted' || true)
BODIES=$(docker logs "$CADDY_NAME" 2>&1 | grep -ciE 'BEGIN|Content-Disposition|filename=' || true)

status=0
if [ "$HTTP" != "202" ]; then
	echo "FALLO: HTTP ${HTTP} != 202" >&2
	status=1
fi
if [ -n "$FILES" ]; then
	echo "FALLO: archivos nuevos en el proxy:" >&2
	echo "$FILES" >&2
	status=1
fi
if [ -n "$DELETED" ]; then
	echo "FALLO: descriptores con archivos borrados (posible spool)" >&2
	status=1
fi
if [ "$BODIES" != "0" ]; then
	echo "FALLO: bodies detectados en el log (${BODIES})" >&2
	status=1
fi
if [ "$status" -eq 0 ]; then
	echo "OK: I-S1-JF-02 — sin buffering a disco, sin spool y sin bodies."
fi
exit "$status"

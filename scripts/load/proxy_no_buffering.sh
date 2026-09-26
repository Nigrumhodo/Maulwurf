#!/usr/bin/env bash
# I-S1-JF-02 (J1.3): sube un flujo grande a través de Caddy y verifica que no
# aparece ningún archivo nuevo en los filesystems del proxy (ni spool temporal).
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
# Mismo Caddyfile de producción, solo cambia el upstream de ingesta por el sink.
sed "s/ingest:8000/${SINK_NAME}:8000/" "$ROOT/infra/Caddyfile" >"$TMP_CADDYFILE"

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
head -c "$SIZE_BYTES" /dev/zero | curl -ks -X PUT -T - \
	-H 'Content-Type: application/octet-stream' \
	"https://localhost:${TEST_PORT}/audios/smoke/content?attempt_id=smoke" \
	-o /dev/null -w 'HTTP %{http_code} · %{size_upload} bytes subidos\n'

echo "--- Archivos nuevos en el contenedor de Caddy (excluye /data y /config) ---"
docker exec "$CADDY_NAME" sh -lc \
	'find / -xdev -newer /tmp/marker -type f 2>/dev/null | grep -vE "^/(data|config)/" || echo "(ninguno)"'
echo "--- Descriptores con archivos borrados (posible spool) ---"
docker exec "$CADDY_NAME" sh -lc 'ls -l /proc/[0-9]*/fd 2>/dev/null | grep deleted || echo "(ninguno)"'
echo "--- Bodies en claro en el log de acceso ---"
docker logs "$CADDY_NAME" 2>&1 | grep -ciE 'BEGIN|Content-Disposition|filename=' || true

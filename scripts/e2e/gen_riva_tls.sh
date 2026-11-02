#!/usr/bin/env bash
# Ephemeral TLS material for the E-S2-JF-06 Riva double (J2.6).
#
# The real GrpcRivaTransport requires TLS, so the smoke uses a local CA and a certificate
# for the compose service name. Everything here is generated at execution time into a
# git-ignored directory; nothing is committed, uploaded or reused between runs.
#
# Usage: bash scripts/e2e/gen_riva_tls.sh <directory>
set -euo pipefail

dir=${1:?usage: gen_riva_tls.sh <directory>}
mkdir -p "$dir"
chmod 755 "$dir"

# The CA key and the server key are private material: create them owner-only and never
# leave the server key world-readable. The e2e-asr container joins the host TLS group
# (`E2E_TLS_GID` in the compose override) to read the 640 key as a non-root user.
harden_permissions() {
  chmod 644 "$dir/ca.pem" "$dir/server.pem"
  chmod 640 "$dir/server.key"
}

if [[ -s "$dir/ca.pem" && -s "$dir/server.pem" && -s "$dir/server.key" ]]; then
  harden_permissions
  echo "gen_riva_tls: reusing existing material in $dir"
  exit 0
fi

umask 077
openssl req -x509 -newkey rsa:2048 -sha256 -nodes -days 2 \
  -keyout "$dir/ca.key" -out "$dir/ca.pem" \
  -subj "/CN=maulwurf-e2e-ca" >/dev/null 2>&1

openssl req -newkey rsa:2048 -sha256 -nodes \
  -keyout "$dir/server.key" -out "$dir/server.csr" \
  -subj "/CN=e2e-asr" >/dev/null 2>&1

sandbox=$(mktemp -d)
trap 'rm -rf "$sandbox"' EXIT
cat > "$sandbox/ext.cnf" <<'EOF'
subjectAltName = DNS:e2e-asr, DNS:localhost, IP:127.0.0.1
extendedKeyUsage = serverAuth
EOF

openssl x509 -req -sha256 -days 2 \
  -in "$dir/server.csr" -CA "$dir/ca.pem" -CAkey "$dir/ca.key" -CAcreateserial \
  -extfile "$sandbox/ext.cnf" -out "$dir/server.pem" >/dev/null 2>&1

# The CA key is only needed to sign; the client trusts ca.pem and never needs it.
rm -f "$dir/ca.key" "$dir/server.csr" "$dir/ca.srl"
harden_permissions
echo "gen_riva_tls: wrote ephemeral CA and server certificate to $dir"

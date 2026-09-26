# Versiones de runtime (L1.1)

Pins de entorno para instalaciones reproducibles y para que J1.1/J1.4 copien las
mismas cifras. Decisión formal: [ADR-0002](adr/ADR-0002-versiones.md). Compose y CI
aún no existen.

| Runtime | Pin | Dónde |
|---|---|---|
| Python | 3.12 (`requires-python = ">=3.12"`) | `.python-version`, `apps/api/pyproject.toml`, `apps/ingest/pyproject.toml` |
| Node.js | 22 (LTS; `engines.node` `>=22 <23`) | `.nvmrc`, `apps/web/package.json` |
| PostgreSQL | 16 (`pgvector/pgvector:pg16`, fijada por digest) | `infra/docker-compose.yml` |
| pgvector | incluido en `pgvector/pgvector:pg16` | `infra/docker-compose.yml` |
| Redis | 7.4-alpine, fijado por digest (`--save "" --appendonly no`) | `infra/docker-compose.yml`, [ADR-0005](adr/ADR-0005-redis.md) |
| Caddy | 2.8-alpine, fijado por digest | `infra/docker-compose.yml`, `infra/Caddyfile` |
| Cliente Riva | `nvidia-riva-client==2.27.0` | `apps/ingest/pyproject.toml`, `requirements-riva.txt` |

Lockfiles que deben instalarse sin resolver a ciegas: `apps/api/uv.lock`,
`apps/ingest/uv.lock`, `apps/web/package-lock.json`.

## Verificación L1.1

Fecha UTC: 2026-09-23. Entorno: WSL (Linux x86_64). Sin secretos en esta nota.

Comandos (todos OK):

```text
uv python install 3.12
cd apps/api && uv sync --extra dev --python 3.12
cd apps/ingest && uv sync --extra dev --python 3.12
cd apps/web && npm ci
```

Versiones efectivas:

| Herramienta / paquete | Versión |
|---|---|
| CPython (uv) | 3.12.14 |
| uv | 0.12.18 |
| Node.js | 22.20.0 |
| npm | 10.9.3 |
| nvidia-riva-client | 2.27.0 |
| FastAPI (api/ingest) | 0.141.1 |

`package-lock.json` no cambió con `npm ci`. Los `uv.lock` se regeneraron al pasar
`requires-python` de `>=3.11` a `>=3.12`. Los `.venv` y `node_modules` no se versionan.

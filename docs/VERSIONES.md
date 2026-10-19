# Runtime versions (L1.1)

Environment pins for reproducible installations and for J1.1/J1.4 to copy the same
figures. Formal decision: [ADR-0002](adr/ADR-0002-versiones.md). The same figures are
used by `infra/docker-compose.yml` and `.github/workflows/quality.yml`.

| Runtime | Pin | Where |
|---|---|---|
| Python | 3.12 (`requires-python = ">=3.12"`) | `.python-version`, `apps/api/pyproject.toml`, `apps/ingest/pyproject.toml` |
| Node.js | 22 (LTS; `engines.node` `>=22 <23`) | `.nvmrc`, `apps/web/package.json` |
| PostgreSQL | 16 (`pgvector/pgvector:pg16`, pinned by digest) | `infra/docker-compose.yml` |
| pgvector | 0.8.6 (in `pgvector/pgvector:pg16@sha256:ccc6e83d…`; ≥ 0.8 is required for `hnsw.iterative_scan`) | `infra/docker-compose.yml` |
| Redis | 7.4-alpine, pinned by digest (`--save "" --appendonly no`) | `infra/docker-compose.yml`, [ADR-0005](adr/ADR-0005-redis.md) |
| Caddy | 2.8-alpine, pinned by digest | `infra/docker-compose.yml`, `infra/Caddyfile` |
| Riva client | `nvidia-riva-client==2.27.0` | `apps/ingest/pyproject.toml`, `requirements-riva.txt` |
| ffmpeg/ffprobe | `7:7.1.5-0+deb13u1` (J1.8) | `apps/ingest/Dockerfile` |
| Python base image | `python:3.12-slim`, by digest | `apps/api/Dockerfile`, `apps/ingest/Dockerfile` |
| Node base image | `node:22-alpine`, by digest | `apps/web/Dockerfile` |
| uv | 0.12.18 | api/ingest Dockerfiles, `.github/workflows/quality.yml` |

Lockfiles that must be installed without blind resolution: `apps/api/uv.lock`,
`apps/ingest/uv.lock`, `apps/web/package-lock.json`.

`espeak-ng` (required by the synthetic-audio tests, S1.A1) is installed by CI unpinned
and is not part of the images: it stays out of the table until it is pinned.

The pinned `pgvector` digest `sha256:ccc6e83d…` resolves (registry tag metadata, checked
2026-10-19) to `pgvector/pgvector:0.8.6-pg16`, so `hnsw.iterative_scan = strict_order`
(L2.3) is available; the digest stays authoritative even if the `pg16` tag is re-pointed.

## L1.1 verification

Date (UTC): 2026-09-23. Environment: WSL (Linux x86_64). No secrets in this note.

Commands (all OK):

```text
uv python install 3.12
cd apps/api && uv sync --extra dev --python 3.12
cd apps/ingest && uv sync --extra dev --python 3.12
cd apps/web && npm ci
```

Effective versions:

| Tool / package | Version |
|---|---|
| CPython (uv) | 3.12.14 |
| uv | 0.12.18 |
| Node.js | 22.20.0 |
| npm | 10.9.3 |
| nvidia-riva-client | 2.27.0 |
| FastAPI (api/ingest) | 0.141.1 |

`package-lock.json` did not change with `npm ci`. The `uv.lock` files were regenerated when
`requires-python` moved from `>=3.11` to `>=3.12`. The `.venv` and `node_modules`
directories are not versioned.

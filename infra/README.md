# infra/ — local development environment (S1–S2)

Status: `docker-compose.yml` (J1.1), the `ingest` hardening (J1.2), the no-buffering
`Caddyfile` (J1.3), the Redis policy (J1.6) and the `ingest` environment wiring (J2.8) are
merged into `develop`. Ticket
ownership is recorded in [docs/sprints/CLAIMS.md](../docs/sprints/CLAIMS.md); full commands are in
[docs/RUNBOOK-dev.md](../docs/RUNBOOK-dev.md).

## Contents

| File | What it is |
|---|---|
| `docker-compose.yml` | S1's 8 services with healthchecks; images pinned by digest |
| `Caddyfile` | Local TLS proxy (`tls internal`), same origin for web/api/ingest, no buffering or cache |

| Service | Image | Notes |
|---|---|---|
| `web` | `maulwurf-web:local` (build `apps/web`) | Node 22-alpine, non-root user; Vite build served by Express |
| `api` | `maulwurf-api:local` (build `apps/api`) | FastAPI; real `/readyz`: 200 with Postgres and Redis, 503 if either is missing |
| `ingest` | `maulwurf-ingest:local` (build `apps/ingest`, target `runtime`) | Hardened: see below |
| `worker` / `scheduler` | `maulwurf-api:local` | Real ARQ processes (J1.5) with `arq --check` healthcheck; outbox dispatch, index job and reconciliation (J2.1–J2.4); `analyze` is still a no-op |
| `postgres` | `pgvector/pgvector:pg16` by digest | The only durable one: `pgdata` volume |
| `redis` | `redis:7.4-alpine` by digest | No persistence, `noeviction` ([ADR-0005](../docs/adr/ADR-0005-redis.md)) |
| `caddy` | `caddy:2.8-alpine` by digest | `admin off`; JSON access log without bodies |

## Local environment guarantees

- **No durable audio:** there is no MinIO/S3 and no audio volume; ingestion temporaries
  live in private tmpfs and are lost when the container is recreated. The only volumes are
  `pgdata`, `caddy_data`, and `caddy_config`.
- **Hardened `ingest` (J1.2):** read-only root fs, `cap_drop: ALL`,
  `no-new-privileges`, user `10001`, tmpfs `noexec,nosuid,nodev` on `/work/tmp`,
  CPU/memory limits, `pids_limit`, no additional swap, and `core: 0`.
- **Redis (J1.6):** `--save "" --appendonly no`, `noeviction`, values ≤ 4 KiB and only
  `arq:*`/`mw:*` keys; published on loopback only for test I-S1-JF-03.
- **Proxy (J1.3):** purely streaming uploads; the `PUT /audios/*/content` route stays on
  `api:8000`, which streams to `ingest` internally (ADR-0008); `/internal/*` is never routed
  from the edge. The no-buffering test is `scripts/load/proxy_no_buffering.sh` (I-S1-JF-02).
- **Secrets outside the compose file:** the Postgres password goes as
  `${POSTGRES_PASSWORD:-maulwurf}` (overridable with `.env`); the `MAULWURF_*` values arrive
  via `env_file`. Never commit `.env`.

## Stack environment variables

| Variable | Use |
|---|---|
| `CADDY_HTTP_PORT` / `CADDY_HTTPS_PORT` | Host ports for 80/443 (e.g. 8080/8443) |
| `REDIS_PORT` | Host port for the Redis published on loopback (e.g. 6380) |
| `POSTGRES_PASSWORD` | Override for the local Postgres password |
| `INGEST_TMPFS_BYTES` / `INGEST_MEM_LIMIT` | `ingest` tmpfs and memory limit; provisional |

The `INGEST_*` values come from `.env.example` and are **not approved** until
S1.B2/D4 (measured limits); the 200 MiB/3 h from spike S1.A5 are not either.

## Quick start

```bash
cp .env.example .env   # fill in the MAULWURF_* values with openssl rand -hex 32
cd infra
docker compose up -d --build --wait
docker compose ps      # 8/8 healthy
```

If 80/443 are taken on the host:

```bash
CADDY_HTTP_PORT=8080 CADDY_HTTPS_PORT=8443 docker compose up -d --build --wait
```

Healthchecks, TLS, tests, and shutdown: [docs/RUNBOOK-dev.md](../docs/RUNBOOK-dev.md).

## Pending in this area

- **J2.5:** the C-S2-JF-10 compose capture, alert destination and scrape configuration are
  still pending; see `docs/sprints/CLAIMS.md`.
- **Migrations:** the `api` container runs `alembic upgrade head` on boot (A1.7);
  manual commands in the [runbook](../docs/RUNBOOK-dev.md) (§5).
- **Host verification (J1.2):** active `zram` and `core_pattern` to systemd-coredump were
  left as a pending operations item in the logbook.
- **Upload path:** A2.4 implemented the streaming `PUT` with SHA-256 and dedupe, and A2.13
  added the API-to-ingest client; the default sink still answers `503` until
  `MAULWURF_INGEST_URL` and the ingest credential are configured. The S2 upload/library UI
  (`D2.x`) and the ingress upload limit (J2.10) are still open.

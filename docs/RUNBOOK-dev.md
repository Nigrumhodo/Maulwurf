# Development runbook (local)

> Scope: local development environment. Not a production runbook (S4/J4.x).
> The source of truth for contracts is [ESPECIFICACION.md](ESPECIFICACION.md);
> the actual state of the repository is what these pages document.

## 1. Requirements

- Docker Engine + Compose v2 (tested with Docker 29.8 and Compose 5.5).
- `uv` 0.12.18 or higher (it also manages Python 3.12 via `.python-version`).
- Node.js 22 (`.nvmrc`) and `npm`.
- `openssl` to generate local secrets.

## 2. First start

```bash
git clone https://github.com/Nigrumhodo/Maulwurf.git
cd Maulwurf
cp .env.example .env

# Generate local secrets (not committed; .env is in .gitignore)
openssl rand -hex 32   # MAULWURF_SECRET_KEY
openssl rand -hex 32   # MAULWURF_ENCRYPTION_KEY
openssl rand -hex 32   # MAULWURF_OAUTH_STATE_SECRET

cd infra
docker compose up -d --build --wait
docker compose ps
```

If ports 80/443 are already taken on the host, set the ports in the root `.env`:

```bash
# .env (repo root)
CADDY_HTTP_PORT=8080
CADDY_HTTPS_PORT=8443

cd infra
docker compose --env-file ../.env up -d --build --wait
# use https://localhost:8443 in the following commands
```

Compose only reads a `.env` located next to `docker-compose.yml` (`infra/`) for variable
interpolation, so from `infra/` always pass `--env-file ../.env` (or export the variables).
Without it the ports silently fall back to 80/443. An inline
`CADDY_HTTPS_PORT=8443 docker compose ...` also works.

With Compose, `MAULWURF_PUBLIC_ORIGIN` is derived from `CADDY_HTTPS_PORT` in
`infra/docker-compose.yml` (`environment` wins over `env_file`), so setting it in `.env` has
no effect there. It only applies when running `uvicorn` standalone.

### Google login (A1.3)

The login needs a Google OAuth client for local testing. Credentials stay in your local
`.env`; never paste them in chat, PRs, or tickets.

1. In [Google Cloud Console](https://console.cloud.google.com), create a test project.
2. **APIs & Services -> OAuth consent screen**: user type External, publishing status
   Testing, scopes `openid`, `email` and `profile` only. Add the Google accounts you will
   sign in with as **test users**; others get `access_denied`.
3. **Credentials -> Create credentials -> OAuth client ID**: type Web application, no
   JavaScript origins. Add this exact authorized redirect URI:
   `https://localhost:<CADDY_HTTPS_PORT>/auth/google/callback`, e.g.
   `https://localhost:8443/auth/google/callback` (port 443 is implicit: no `:443` suffix
   in the URI). Scheme, host and port must match.
4. Put the values in `.env`: `MAULWURF_GOOGLE_CLIENT_ID`, `MAULWURF_GOOGLE_CLIENT_SECRET`,
   and a random `MAULWURF_OAUTH_STATE_SECRET` (see above).
5. Restart `api` with the same `--env-file`, open `https://localhost:<CADDY_HTTPS_PORT>/login` and accept
   Caddy's local certificate.

Calendar and Gmail scopes are not requested at login; they arrive in S3.

The 8 services (`web`, `api`, `ingest`, `worker`, `scheduler`, `postgres`,
`redis`, `caddy`) end up `healthy`. `api` applies the migrations on startup (§5);
`worker`/`scheduler` are ARQ processes with healthcheck `arq ... --check` (J1.5).

## 3. Healthchecks

```bash
curl -k -o /dev/null -w '%{http_code}\n' https://localhost/          # web (200: placeholder)
curl -k https://localhost/readyz                                     # api: 200 with Postgres and Redis, 503 if one is missing
docker compose exec api    python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/healthz').status)"
docker compose exec ingest python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/healthz').status)"
```

## 4. Local TLS

Caddy uses its internal CA (`tls internal`), persisted in the `caddy_data` volume.
To trust it instead of using `curl -k`:

```bash
docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt /tmp/maulwurf-root.crt
sudo cp /tmp/maulwurf-root.crt /usr/local/share/ca-certificates/maulwurf-root.crt
sudo update-ca-certificates
```

## 5. Migrations (A1.7)

The `api` container runs `alembic upgrade head` before uvicorn, so
`docker compose up` leaves the DB at `head` with no manual step. If several replicas start at
the same time, an advisory lock serializes them; the one waiting more than 120 s fails instead
of hanging.

```bash
docker compose exec api alembic current            # applied revision (head; 0001–0003)
docker compose exec api alembic upgrade head       # apply manually, e.g. after a pull
docker compose exec api alembic downgrade -1       # rollback to the previous revision
docker compose exec api alembic history            # revision chain
```

Rolling back to `base` (or below `0001`) drops all the app tables and their local data: use
it only in development. A schema change is always a new revision
(`uv run alembic revision --autogenerate -m "..."` from `apps/api`, reviewed by hand);
an already-applied revision is never edited. `uv run alembic check` must answer "No new
upgrade operations detected" before opening the PR.

## 6. Tests and lint

```bash
# API
cd apps/api
uv sync --frozen --extra dev
uv run ruff check . && uv run mypy .
uv run pytest -m "not integration and not provider and not load"

# Ingest
cd apps/ingest
uv sync --frozen --extra dev
uv run ruff check . && uv run mypy .
uv run pytest -m "not integration and not provider and not load"

# Web
cd apps/web
npm ci
npm run lint && npm run typecheck && npm test && npm run build

# Documentation
cd ../..
python3 scripts/docs/check_refs.py
```

Local integration (Redis is published on loopback only; PostgreSQL is not published
to the host). The suite creates its own `maulwurf_test_*` database, migrates it, and drops it
when done; it needs a reachable PostgreSQL, for example a disposable one:

```bash
docker run -d --rm --name mw-test-pg -e POSTGRES_USER=maulwurf \
  -e POSTGRES_PASSWORD=maulwurf -e POSTGRES_DB=maulwurf -p 127.0.0.1:55432:5432 \
  pgvector/pgvector:pg16
cd apps/api
MAULWURF_DATABASE_URL=postgresql+asyncpg://maulwurf:maulwurf@127.0.0.1:55432/maulwurf \
  uv run pytest -m integration
# Ingest: same variable (creates a `maulwurf_ingest_test_*` DB); it needs `ffmpeg` on the
# PATH and, for the synthetic-audio tests, `espeak-ng` (CI installs them).
cd ../ingest
MAULWURF_DATABASE_URL=postgresql+asyncpg://maulwurf:maulwurf@127.0.0.1:55432/maulwurf \
  uv run pytest -m integration
docker stop mw-test-pg
```

If port 6379 is taken (e.g. another local stack), use another port:

```bash
REDIS_PORT=6380 docker compose up -d redis
MAULWURF_REDIS_URL=redis://localhost:6380/0 uv run pytest -m integration
```

For tests that need PostgreSQL from the host, create a local (untracked)
`infra/docker-compose.override.yml` with:

```yaml
services:
  postgres:
    ports:
      - "127.0.0.1:5432:5432"
```

The `P-*` (provider) and `load` suites do not run on MRs; they require a protected
account and budget, and are run separately.

## 7. Shutting down and cleaning up

```bash
cd infra
docker compose down        # keeps pgdata
docker compose down -v     # destructive: deletes volumes
```

There is no persisted audio to delete: ingestion temporaries are `tmpfs` and
disappear when the container is recreated.

## 8. Common issues

- **Port taken**: use `CADDY_HTTP_PORT`/`CADDY_HTTPS_PORT` (§2).
- **`Invalid request` (HTTP 400) on every page**: another process owns host port 443 and
  answers instead of Caddy (check with `ss -ltn | grep :443`). Use other ports (§2).
- **`docker compose` ignores my ports**: pass `--env-file ../.env` from `infra/` (§2).
- **Google `redirect_uri_mismatch`**: the redirect URI registered in Google Cloud differs
  from `https://localhost:<CADDY_HTTPS_PORT>/auth/google/callback`, port included.
- **Google `access_denied`**: the account is not a test user of the consent screen.
- **`/login?error=oauth_not_configured`**: `MAULWURF_GOOGLE_CLIENT_ID`/`_SECRET` are empty
  or `api` was not recreated after editing `.env`.
- **`/login?error=oauth_failed`**: see `docker compose logs api`; common causes are an
  unverified Google email or a login attempt older than 10 minutes.
- **The API does not start**: `MAULWURF_ENV` is required with no default; a non-`local`
  environment with `change-me`, too short or reused secrets also fails on purpose (fail-fast
  in `app/core/config.py`).
- **Certificate warning**: trust the CA (§4) or use `curl -k`.
- **Slow first build**: it downloads ffmpeg, Python dependencies, and `npm ci`.
- **`api` replicas failing at startup**: when scaling `api`, all replicas try to migrate.
  One applies the migration and the others wait for the lock; the one waiting more than 120 s fails and
  Docker restarts it (`restart: unless-stopped`). This is expected, not an app error. In a
  real deployment (S4) the migration will be a single step, separate from the replicas.

## 9. Local environment privacy rules

- No real class recordings or durable audio: real-class ingestion remains blocked until the
  F0 decisions close and the API-to-ingest transport is configured.
- No `NVIDIA_API_KEY` in logs, the frontend, or commits.
- The `P-*` suites only with an authorized account and a documented budget.

## Links

- [../README.md](../README.md) — repository vision and status.
- [ESPECIFICACION.md](ESPECIFICACION.md) — M1–M9 contracts and security.
- [PLAN_SPRINTS.md](PLAN_SPRINTS.md) — backlog and DoD per sprint.
- [adr/](adr/README.md) — architecture decisions.

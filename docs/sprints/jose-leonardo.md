# José Leonardo Pinilla Zamora — sprint logbook

> Suggested profile: Technical lead · RAG (plan §0.2) ·
> Master plan: [../PLAN_SPRINTS.md](../PLAN_SPRINTS.md) ·
> Tests: [../PLAN_TESTS.md](../PLAN_TESTS.md) ·
> Contracts: [../ESPECIFICACION.md](../ESPECIFICACION.md) (§M4, §M6, §M7) ·
> Gates: [../PLAN_IMPLEMENTACION.md](../PLAN_IMPLEMENTACION.md) (G4, G6)

## Claimed tickets

### Week 1 (S1) — Foundation + spike

> Historical section: rows written before the 2026-09-19 review are candidates, not
> claims; current tickets are recorded first in [CLAIMS.md](CLAIMS.md). Until a row exists
> there, this table stays empty.

| Ticket | Area | Status | Evidence (command, date, environment, result) |
|---|---|---|---|
| J1.8 | J | done | 2026-09-25 UTC, Ubuntu (Docker 29.8/WSL). `docker build --target base -t maulwurf-ingest:base apps/ingest`; inside: ffmpeg 7.1.5-0+deb13u1, ffprobe 7.1.5, Python 3.12.14; Id `sha256:91ef546d…`. Pending: parity with the spike's ffmpeg 8.0.1 (S1.A4) — decision recorded in the PR. |
| J1.1 | J | done | 2026-09-25 UTC. `docker compose up -d --build --wait`: 8/8 healthy (caddy on 8080/8443 because 80/443 are taken on the host); `/`→404 web with this PR's minimal Caddyfile (it only routes to web) and `/readyz`→200 verified inside the `api` container; Caddy's routing of `/readyz` is evidenced in J1.3. `docker compose config --volumes` only `pgdata`/`caddy_data`/`caddy_config`; `ingest` without mounts. |
| J1.2 | J | done | 2026-09-25 UTC. `docker inspect` of `ingest`: ReadonlyRootfs=true, User=10001:10001, CapDrop=ALL, no-new-privileges, tmpfs 256 MB, MemorySwap=1g, pids 128, NanoCpus 2; inside: read-only `/`, writable `/work/tmp`, `ulimit -c 0`; cgroup `memory.swap.max=0`. Host: `zram` active and `core_pattern` set to systemd-coredump — pending operations. |
| J1.3 | J | done | 2026-09-25 UTC. `caddy validate` OK; `scripts/load/proxy_no_buffering.sh`: 536 870 912 B through Caddy → HTTP 202; no new files (certificates excluded), no deleted descriptors, 0 bodies in logs. It uses the real Caddyfile with the ingestion upstream replaced by a sink; pending to repeat against the real endpoint (S2). |
| J1.4 | J | done | 2026-09-25 UTC. Local: api `ruff`+`mypy` green (0 unit tests yet); ingest `ruff`+`mypy` green and 27 tests; web `lint`+`typecheck`+Vitest (0 tests)+`build` OK. `quality.yml` validated (YAML); first run on GitHub pending the PR. Type fixes: pydantic plugin and a `cast` for the DSN; annotations in ingest tests; `typecheck`/`test` scripts in web. |
| J1.6 | J | done | 2026-09-25 UTC. `uv run pytest -m integration tests/integration/test_redis_policy.py`: 2 passed; `aof_enabled=0`, empty `save`, `maxmemory-policy=noeviction`, sweep with a 4 KiB threshold (ADR-0005). The ADR stays `Proposed` until the review. |
| J1.7 | J | done | 2026-09-25 UTC. Verification from a clean clone (`git clone --branch s1/j1.7-runbook`, `cp .env.example .env` + secrets, `docker compose -p maulwurfcheck up -d --build --wait` with ports 8081/8444 and `REDIS_PORT=6380`): 8/8 healthy, `/`→200 web and `/readyz`→200 api; volumes only `pgdata`/`caddy_data`/`caddy_config`. Also run on the current tree. Pending: migrations section once A1.7 delivers `alembic.ini`. |
| A1.1 | A | done | 2026-09-27 UTC, Arch Linux, Python 3.12.11 (`uv sync --python 3.12`), Docker 29.8.0, disposable Postgres `pgvector/pgvector:pg16` at `127.0.0.1:55432`. Branch `s1/a1.1-api-scaffold` (`30df7bb`, QA `96cc80c`: hermetic conftest against the shell's `MAULWURF_*`). `uv run pytest tests/unit/test_health.py`: 2 passed; suite with `MAULWURF_ENV=prod` exported: green. `uv run uvicorn app.main:app --port 8765` + `curl /healthz`: 200 `{"status":"ok"}`. `ruff` and `mypy` green. Merged in #21. |
| A1.2 | A | done | 2026-09-27 UTC, Arch Linux, Python 3.12.11 (`uv sync --python 3.12`), Docker 29.8.0, disposable Postgres `pgvector/pgvector:pg16` at `127.0.0.1:55432`. Branch `s1/a1.2-settings` (`015dddb`, `de43cf1`, QA `0e9fa1e`, `6ffc83e`, `8e7a9d5`, `20a01ae`). `pytest tests/unit/test_config.py tests/unit/test_db.py`: 30 passed (fail-fast on a missing or empty secret, development DSN/Origin outside local, empty `VAR=` = not configured, OAuth id/secret pair, defaults guard also with `default_factory`, `change-me` and non-https Origin outside local, Origin with path, query or fragment, unknown `MAULWURF_*` in the environment or in the effective `.env`, foreign `.env` keys ignored, errors without echoing the value, `repr`/dump without secrets). Startup from `apps/api` with the root `.env.example`: OK. In compose: `api` healthy and `docker compose run -e MAULWURF_DATABSE_URL=x api` fails with «variables de entorno desconocidas» (the error message is in Spanish in the code: "unknown environment variables"). Diff sweep without secret values. After merging with `develop`: 29 passed and API suite 63 passed. Merged in #23 and #26. |
| A1.5 | A | done | 2026-09-27 UTC, Arch Linux, Python 3.12.11 (`uv sync --python 3.12`), Docker 29.8.0, disposable Postgres `pgvector/pgvector:pg16` at `127.0.0.1:55432`. Branch `s1/a1.5-core-schema` (`af9bf82`, `af042fb`, `af979b4`, QA `dfedf0b`: explicit active states, typed `user:field:version` AAD, autogenerate with `compare_type`/`compare_server_default`). `alembic upgrade head` from empty → `downgrade base` → `upgrade head` OK; `alembic check`: no differences with the models. `pytest tests/unit/test_crypto.py tests/integration/test_core_schema.py`: 22 passed (U-S1-AN-03: unique nonce, per-user/field AAD, key version, nothing in plaintext in the DB; outbox gate CHECK; composite FK; active-attempt index; tenant filter). Merged in #24. |
| A1.4 | A | done | 2026-09-27 UTC, Arch Linux, Python 3.12.11 (`uv sync --python 3.12`), Docker 29.8.0, disposable Postgres `pgvector/pgvector:pg16` at `127.0.0.1:55432`. Branch `s1/a1.4-session-csrf` (`15478c2`, `54c1a61`, QA `2fe18ec`). `pytest tests/unit/test_session_csrf.py tests/integration/test_sessions.py`: 30 passed (U-S1-AN-01: cookie flags, only hashes in the DB, expiry, revocation and rotation; U-S1-AN-02: POST and binary PUT reject without a token, with a forged token, without Origin, with a foreign Origin, with a suffix or with non-ASCII bytes (403, not 500); concurrent rotation with a single successor (409 `session_conflict`); inactive user → 401; `GET /me` with `no-store`). Code 409 `session_conflict` added to DISENO §3.2. Full suite: 94 passed. Merged in #22. |
| J1.5 | J | done | 2026-09-27 UTC, Arch Linux, Python 3.12.11 (`uv sync --python 3.12`), Docker 29.8.0, disposable Postgres `pgvector/pgvector:pg16` at `127.0.0.1:55432`. Branch `s1/j1.5-arq-outbox` (`47043b2`, `039cece`, QA `d022f17`), partial merged in #25. `pytest tests/unit/test_arq_settings.py tests/unit/test_health.py`: 10 passed. Real `/readyz` in compose: 200 `ready`; with `redis` stopped 503 `{"failed":["redis"]}` and `/healthz` stays 200; when Redis comes back, 200. Worker/scheduler depend only on Postgres/Redis, with `cap_drop: [ALL]` and `no-new-privileges`. `docker compose up -d --build --wait worker scheduler`: both healthy; keys `arq:queue:health-check` and `arq:scheduler:health-check` with TTL ~31 s and 72 bytes; with the worker stopped for 33 s, `arq ... --check` exits 1. Dispatcher (branch `s1/j1.5-dispatcher`, QA `4ec1923`, `a9fdd99`, `9b54c72`): `pytest tests/unit/test_outbox_policy.py` (U-S1-JF-04) and integration `tests/integration/test_dispatcher.py` against real Postgres and Redis green: only IDs, gate respected even with `enabled`, no batch starvation, unknown types to `skipped`, dedup counted. API suite: 140 passed. E2E in compose: the worker ran `index` for the ready event and the blocked one stayed `pending`. Merged in #25 and #30. For J2.1: `completed`/`failed` ack, retries and `next_attempt_at`. |
| A1.7 | A | done | 2026-09-27 UTC, Arch Linux, Python 3.12.11, Docker 29.8.0, `pgvector/pgvector:pg16`. Branch `s1/a1.7-alembic`. `pytest tests/integration/test_migrations.py`: 4 passed (I-S1-AN-04: from empty, downgrade + upgrade, idempotency, 4 concurrent `alembic` processes). API suite in a clean clone: 98 passed. Compose from scratch: 8/8 healthy, the API migrates to `0001` and `/readyz` through Caddy 200; 3 `api` replicas on an empty schema: one migrates, 0 errors. A test with threads hung (Alembic's `context` is process-global): the test uses processes and `env.py` sets `lock_timeout`. Merged in #28. |
| A1.8 | A | done | 2026-09-27 UTC, Arch Linux, Python 3.12.11, Docker 29.8.0, `pgvector/pgvector:pg16`. Branch `s1/a1.8-endpoints` (QA `187e490`, `8ecaff4`). `pytest tests/integration/test_a18_endpoints.py`: green (201 `awaiting_upload`, 422 without creating rows or echoing values, foreign subject 404, `PUT` 503 without reading the body, 413/410/409, chunked without `Content-Length` 503, `PATCH /me` and `/integrations/status` with `no-store`, logout revokes). API suite: 123 passed. Compose through Caddy without a session: 401 on the five endpoints. Merged in #29. Pending: per-user 429 until D4 sets limits. |
| S1.B1 | S | done | 2026-09-27 UTC, Arch Linux, Python 3.12.11 (`apps/ingest` venv recreated from 3.13), Docker 29.8.0, image `maulwurf-ingest:local`. Branch `s1/b1-hardening` (`9f0b991`, `d6204bd`). Ingest unit tests: 59 passed. `pytest -m integration tests/test_hardening_container.py` against compose's `ingest`: 10 passed (I-S1-SG-06, part B1: `docker inspect`, self-check `[]`, `/readyz` 200, writes outside the tmpfs rejected, `ENOSPC` before 256 MiB, no dump after SIGSEGV, tmpfs empty on restart). Negative control: the non-hardened image does not start (8 failures) and in `report` mode 5 tests fail. Merged in #33. |
| S1.B3 | S | done | 2026-09-27 UTC, Arch Linux, Python 3.12.11, Docker 29.8.0, system ffmpeg and `7:7.1.5` in the image, disposable `pgvector/pgvector:pg16`. Branch `s1/b3-supervisor` (`269691b`). `pytest tests/test_supervisor.py`: 8 passed, 5 of 5 runs (success, SIGKILL to the child with ffmpeg alive, timeout, descriptor to a deleted file → `failed`, expired lease does not publish, 4 concurrent `acquire` → 1 wins, per-user lease). Negative control: without `killpg` the SIGKILL test fails. In the hardened container against compose's DB: `succeeded`, 5 fragments, `verified`, tmpfs empty; test rows deleted. Ingest suite: 77 passed. Merged in #34. |

## Blockers and dependencies

- (every Monday: note whom you are waiting on for a frozen contract §6 or real code, and which day)

## Week notes

- 2026-09-27: S1.B1 does not modify the development host (zram and `core_pattern` set to
  systemd-coredump stay as they are). The guarantees are tested at container level
  (`memswap_limit` = `mem_limit`, `memory.swap.max=0`, `ulimit -c 0`); the real host is
  validated at deployment (J4.x). Recorded as an exception, not as an approval.
- 2026-09-27: S1 isolation by application-level filtering and RLS in S2 (ADR-0006).
- 2026-09-27: pydantic-settings' `extra="forbid"` only validates the `.env`; in Compose the
  variables arrive through the environment and were being ignored. A1.2 adds an explicit
  validation.
- 2026-09-27: resolved in A1.2 (QA): the API did not start with the root `.env` because it
  carries keys without the prefix (`POSTGRES_PASSWORD`, `RIVA_*`, `INGEST_*`). It now reads
  `../../.env` and `.env`, ignores the foreign keys and keeps rejecting unknown
  `MAULWURF_*`; validation errors no longer repeat the received value.
- 2026-09-27: pending A1.7: the `api` image does not copy `alembic/` nor `alembic.ini`, so
  it cannot migrate from the container. A1.5 already includes `alembic.ini` and `env.py` to
  test the schema from empty; A1.7 keeps I-S1-AN-04 and the compose run.
- 2026-09-27: `apps/api/.venv` was on Python 3.13.13 (ADR-0002 pins 3.12); recreated with
  `uv sync --python 3.12` and the A1.x/J1.5 evidence re-run on 3.12.11.
- 2026-09-27: A1.4 QA applied; proposals not applied: `__Host-` prefix on the cookie
  (it changes the `mw_session` name frozen in S1.md §1.1, goes to L1.6) and a global tenant
  filter with `with_loader_criteria` (evaluated with A1.9).
- 2026-09-27: transaction convention: `get_session` does not commit; each handler that
  writes does an explicit `commit` (docstring of `app/core/db.py`).
- 2026-09-27: A1.4 decisions with no source in the spec: 168 h session
  (`MAULWURF_SESSION_TTL_HOURS`) and rejection of mutations without an `Origin` header.
- 2026-09-27: the S1.B1 negative control left a dump of a data-less `python -c` on the host
  (`/var/lib/systemd/coredump/core.python.10001.*`, 1.6 MB). It belongs to root and is
  removed by systemd-coredump; it contains no audio.
- 2026-09-27: S1.B1 did not verify the host's swap (zram) nor `core_pattern`; it is left for
  deployment (J4.x), like the previous exception.
- 2026-09-28: `cleanup_status = verified` is only reached in the ingestion container; on
  WSL or without a container, processes of another UID leave `unreadable_processes > 0` and
  the attempt ends `failed` on purpose (L1.6, exception 5). It is not relaxed without a
  security review.
- (decisions, findings, scope deviations — one line per event, with date)

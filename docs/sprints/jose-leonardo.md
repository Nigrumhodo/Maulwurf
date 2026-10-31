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

### Week 2 (S2) — Knowledge

| Ticket | Area | Status | Evidence (command, date, environment, result) |
|---|---|---|---|
| A2.13 | A | in progress | 2026-10-30 UTC, Arch Linux, Python 3.12, Docker, disposable `pgvector/pgvector:pg16` at `127.0.0.1:55432` and `redis:7.4-alpine` (`--save "" --appendonly no --maxmemory 256mb noeviction`) at `127.0.0.1:56379`. Branch `feature/A2.13-reception-client` (`b270f66`, `b14666a`, `7f377b6`, `98c4d43`). `ruff` + `mypy` green (140 files); unit 698 passed; integration 315 passed. I-S2-AN-12 against an in-memory ingest served by uvicorn: cases 1–4, 6–13 and 12a–12d green; case 13 streams 32 MiB with ~0.6 MB RSS growth. Case 5 partial (D-5, see notes); case 14 (`load`) written, not run. Ingest credential vectors: 4 passed. Negative controls: without the hash comparison case 8 fails; without `abort()` on disconnect case 9 fails. |
| J2.1 | J | in progress | 2026-10-30 UTC, same environment. Branch `feature/J2.1-outbox-dispatcher` (`d85675a`, `7f9efa4`, `605b3bf`). `ruff` + `mypy` green; unit 684 passed (U-S2-JF-01: 22 cases); integration 305 passed (`test_dispatcher.py` 23, A2.10 gate tests unchanged). No migration: uses `attempts`, `max_attempts`, `next_attempt_at`. Negative control: retrying a permanent error makes 5 tests fail. |
| J2.7 | J | in progress | 2026-10-30 UTC. Branch `feature/J2.7-fault-injection` (`c6c0610`). Ingest integration as root in `python:3.12-bookworm` with ffmpeg and espeak-ng (own PID namespace, so `unreadable_processes = 0`): 51 passed, 10 skipped; the 3 new I-S2-JF-05 cases pass 3 times in a row. API `test_fault_injection_outbox.py`: 5 passed. Negative control: removing the fencing-token guard of `record_cleanup` makes case (c) fail; `lease.py` restored byte for byte. |
| J2.8 | J | in progress | 2026-10-30 UTC. Branch `feature/J2.8-ingest-compose-wiring` (`d3accc7`, stacked on A2.13). `python3 scripts/ci/check_compose_ingest_env.py` (I-S2-JF-11): 24 assertions pass with a dummy env (names only, no values). `docker compose config -q` OK. Negative controls: an API secret in `ingest`, a default `NVIDIA_API_KEY` and user `0:0` each fail. Full stack not started. |
| J2.9 | J | in progress | 2026-10-30 UTC. Branch `feature/J2.9-ci-hygiene` (`05e7dfc`, `e2a8b8e`). `scripts/ci/check_no_secrets.sh`: 488 tracked files, 0 findings; a seeded fake `nvapi-` key in a throwaway clone fails it. License report (pip-licenses 5.5.5, license-checker 25.0.1): api 45, ingest 28, web 73 packages, 0 denied. Empty suite (`pytest -m nonexistent`, exit 5) now fails the step; unit suites pass without the tolerance. Redis CI asserts `maxmemory=268435456`, `noeviction`. Pending: a real PR with a seeded secret must turn `secret-scan` red. |

### Week 3 (S3) — Action

| Ticket | Area | Status (`pending/in progress/blocked/done/transferred`) | Evidence (command, date, environment, result) |
|---|---|---|---|
| S3.1 | S | in progress | Claim in `CLAIMS.md` (2026-10-31, directed by MAULW-108). Branch `task/MAULW-108`. Explicit F1 → F2 backfill over the active persisted transcript: `app/services/analysis_backfill.py` upgrades `required_stages`, queues one `analyze` run through the same `runs.ensure_stage_run` authority as retry, and emits the cleanup-gated `analyze_requested` event; `scripts/backfill_analysis.py` is tenant-scoped, bounded and dry-run by default; `GET /audios/{id}` adds `analysis_backfill_available`. No ingestion attempt, audio field, transcript row or segment is written, and the outbox carries only IDs/version. I-S3-SG-01 (`apps/api/tests/integration/test_analysis_backfill.py`, 9 cases) covers persisted-text-only queueing, dry-run, idempotency, cleanup gate, tenant isolation, failed-run requeue, F2 skip and dispatcher args. 2026-10-31, Linux sandbox (no Docker/PostgreSQL/Redis): `cd apps/api && uv run ruff check .` → all checks passed; `uv run mypy .` → success, 184 source files; `NO_PROXY=localhost,127.0.0.1 uv run pytest tests/unit -q` → **872 passed**; `pytest tests/integration/test_analysis_backfill.py --collect-only -q` → 9 collected. Local integration not run (sandbox without services); Quality CI on PR #119 ran `integration (api)` → **404 passed, 873 deselected** (the new `test_analysis_backfill.py` collected in that run) and `integration (ingest)` green. `python3 scripts/docs/check_refs.py` → OK (699 paths). `apps/ingest` unchanged: `ruff`/`mypy` clean (74 files); local unit run 154 passed, 8 environment-only failures in `test_synthetic_audio.py` because this sandbox has no `espeak-ng` (Docker CI provides it, and `python-unit (ingest)` passed). No provider call, real class, audio or PII. L3.6/A3.1 are absent from `develop`, so the queue reaches the S2 `analyze` no-op and extraction output is not claimed. |
| S3.3 | S | in progress | Claim in `CLAIMS.md` (2026-10-31, directed by MAULW-110). Branch `task/MAULW-110`. `app/services/evidence.py` is the no-offset path: `reliable_times` withholds both times for `timestamp_precision=none`, incomplete/non-finite/inverted pairs and pairs past the class duration; `evidence_reference` returns a measured `mm:ss` only when truthful and otherwise `"segment N"`; `resolve_textual_evidence` rebuilds segment/Unicode-span/verbatim-quote evidence with null times; search hydration, RAG source blocks and citations share the same policy. U-S3-SG-03 (`apps/api/tests/unit/test_evidence.py`, 24 cases) covers no offsets, mixed timed/untimed segments without interpolation, untruthful pairs, Unicode spans and rejected spans, and asserts that no `00:00` fallback is serialized. 2026-10-31, Linux sandbox (no Docker/PostgreSQL/Redis, no `espeak-ng`): `cd apps/api && uv run ruff check .` → all checks passed; `uv run mypy .` → success, 186 source files; `NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1 uv run pytest tests/unit -q` → **907 passed**. Without the sanitized `no_proxy`, two unrelated tests (`test_ingest_sink.py::test_sink_is_the_ingest_client_when_configured` and `test_llm.py::test_shared_http_client_is_bounded_reused_and_closed`) fail inside httpx parsing the sandbox's `[::1]` proxy patterns (`Invalid port: ':1]'`); they pass with the sanitized variable and do not touch this change. `python3 scripts/docs/check_refs.py` → OK (724 paths). `apps/ingest` unchanged: `ruff`/`mypy` clean (74 files); local unit run 155 passed, 8 environment-only failures in `test_synthetic_audio.py` because this sandbox has no `espeak-ng` (Docker CI provides it). No provider call, audio or PII. L3.6/A3.1 are absent from `develop`, so this ticket claims the evidence layer, not extraction output; `apps/web` was not touched and the D3 inbox rendering stays out of scope. |

## Blockers and dependencies

- 2026-10-30: J2.4 must resolve the `HELD` attempt left when the owner loses the token after a
  verified cleanup (found in J2.7). J2.2 must revalidate tombstone, owner and version inside the
  `index` job (J2.1 revalidates with a snapshot read).
- (every Monday: note whom you are waiting on for a frozen contract §6 or real code, and which day)

## Week notes

- 2026-10-12: reviewer evidence for A1.3 (owner ARES313), cloud eligibility `verified`.
  Real Google login observed by the lead in local Compose (`develop` at `854ad52`, Caddy on
  8443 because host port 443 is taken), with a test OAuth client in a Google Cloud test
  project (consent screen in Testing, scopes `openid email profile`, redirect URI
  `https://localhost:8443/auth/google/callback`). Result: `/login` → Google → callback →
  session issued and the panel loads. Credentials only in the local `.env`, never in the
  repo, chat or tickets. Setup documented in `docs/RUNBOOK-dev.md` (#61).
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
- 2026-10-30: A2.13 D-5: httpx sends the whole request body before reading the response, so an
  early `413`/`415` from ingest reaches the browser only after the API has read the body up to
  its own size cut. Bounded by that cut; a full fix needs a lower-level HTTP/1.1 client.
  Lead decision: accepted as a bounded risk for S2; a full fix becomes its own ticket only if
  the ingress measurement of J2.10 shows the extra read matters.
- 2026-10-30: J2.8 keeps `MAULWURF_INGEST_URL` and `MAULWURF_INGEST_CREDENTIAL_SECRET` as a
  pass-through pair: a fixed URL without the secret would stop `api` from starting. Without
  both in `.env` the `PUT` keeps answering `503`.
- 2026-10-30: J2.7 finding, decided: `record_cleanup` may write `verified` after the lease
  expired if the fencing token did not change. Lead decision: the code stands. ESPECIFICACION
  §M3 and S1.B6 say an expired lease does not *prove* cleanup and only cleanup evidence does;
  here `verified` comes from `destroy_and_verify` evidence, never from the expiry, and the
  outbox stays closed (`LEASE_LOST`). No contract change.
- 2026-10-30: J2.7 finding, decided: an owner that loses the token after a verified cleanup
  leaves the attempt `HELD` with no automatic exit. Lead decision: J2.4 (reconciler) owns it
  and must detect and resolve that state; it is not closed by J2.7.
- 2026-10-30: J2.1 does not dispatch Calendar, notification or deletion events (no handler
  yet), and its revalidation is a snapshot read; J2.2 must revalidate inside the job.
- (decisions, findings, scope deviations — one line per event, with date)

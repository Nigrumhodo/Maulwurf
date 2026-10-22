# Andres Felipe — sprint logbook

> Suggested profile: API · Database (plan §0.2) ·
> Master plan: [../PLAN_SPRINTS.md](../PLAN_SPRINTS.md) ·
> Tests: [../PLAN_TESTS.md](../PLAN_TESTS.md) ·
> Contracts: [../ESPECIFICACION.md](../ESPECIFICACION.md) (§M1, §M2, §M4, §6) ·
> Gates: [../PLAN_IMPLEMENTACION.md](../PLAN_IMPLEMENTACION.md) (G3, G5-F1, G5-F2)

## Claimed tickets

### Week 1 (S1) — Foundation + spike

> Historical section: rows written before the 2026-09-19 review are candidates, not
> claims; current tickets are recorded first in [CLAIMS.md](CLAIMS.md). Until a row exists
> there, this table stays empty.

| Ticket | Area | Status | Evidence (command, date, environment, result) |
|---|---|---|---|
| — | — | — | No claims yet: the S1 sprint opens through a PR in `CLAIMS.md`. |

### Week 2 (S2) — Knowledge

| Ticket | Area | Status | Evidence (command, date, environment, result) |
|---|---|---|---|
| A2.9 | A- | in progress | Claim in `CLAIMS.md` (2026-10-15). Branch `feature/A2.9-listing-sse-exports`: `GET /audios`, `GET /audios/{id}`, `GET /audios/{id}/progress` (SSE), `GET /audios/{id}/transcript.txt?segment=` and `.srt`. 2026-10-15, WSL/Linux: `uv run ruff check .` and `uv run mypy .` → clean; `uv run pytest -m "not integration and not provider and not load"` → **439 passed**; `MAULWURF_DATABASE_URL=…@127.0.0.1:55432 uv run pytest -m integration` against `pgvector/pgvector:pg16` → **239 passed** (93.80 s), including I-S2-AN-07 (`test_progress_sse.py`, 5) and I-S2-AN-09 (`test_audio_read.py`, 17). PR pending review. |
| L2.4 + A2.11 | L- / A- | done | Claim in `CLAIMS.md` (2026-10-21). Branch `feature/L2.4-search-service-endpoint`: reusable `SearchService.retrieve/page`, `GET /search`, hydration, plain-text snippet, honest segment times, opaque keyset cursor and per-user Redis quota. 2026-10-21, WSL/Linux: `cd apps/api && uv run ruff check .` and `uv run mypy .` → clean; `uv run pytest -m "not integration and not provider and not load"` → **565 passed**; `MAULWURF_DATABASE_URL=…@127.0.0.1:55432 uv run pytest -m integration -q` against `pgvector/pgvector:pg16` and Redis → **257 passed** (178.09 s), including `I-S2-JL-06` (`tests/integration/test_search.py`, 17 passed). PR #73 review fix: `docker build --target runtime -t maulwurf-ingest:l24-fix apps/ingest` → success (sha256:f8b97b9…). PR #73 merged at `f1cd049` (2026-10-22). |
| L2.5 | L- | in progress | Claim in `CLAIMS.md` (2026-10-22). Branch `feature/L2.5-chat-sse`: `RAGService` prepares the turn (embedding retrieval, RRF evidence under the token budget, versioned prompt), streams `snapshot`/`delta`/`citation`/`done`/`error` with heartbeats and `Last-Event-ID` replay from the persisted message, validates citation markers against selected DB rows, and persists the assistant `messages` row plus `message_sources`. Idempotent `client_message_id`, in-process cancel plus DB polling, and client-disconnect finalization leave the partial `cancelled`. Conversation CRUD/history/delete and `I-S2-AN-11` remain A2.12. QA review fixes: the default token count applies `TOKEN_SAFETY_FACTOR=1.5` with `ceil`; evidence is neutralized and wrapped in `<source id="Sn" meta="…">`; each user is limited to two concurrent streams with `429 rate_limited`; and the LLM provider reuses one `httpx.AsyncClient` (`max_connections=20`, `max_keepalive_connections=10`) closed by the app lifespan. 2026-10-22, WSL/Linux with Docker Desktop engine: `cd apps/api && uv run ruff check .` and `uv run mypy .` → clean (128 source files); `uv run pytest -m "not integration and not provider and not load"` → **607 passed** (278 deselected); `MAULWURF_DATABASE_URL=…@127.0.0.1:55432 MAULWURF_REDIS_URL=…@127.0.0.1:6379 uv run pytest -m integration -q` against a temporary `pgvector/pgvector:pg16` and Redis 7.4 → **278 passed** (120.86 s), including `I-S2-JL-07/08` (`tests/integration/test_chat_sse.py`, 21 passed). All providers are deterministic fakes; no real LLM/embedding call. `docker build --target runtime -t maulwurf-api:l25-qa apps/api` → success; `docker run --rm --entrypoint sh maulwurf-api:l25-qa -c 'grep -q "id.*attribute" /app/prompts/v1/chat_system.txt'` confirms the updated prompt is in the runtime image. PR #74 open. |

## Blockers and dependencies

- (every Monday: note whom you are waiting on for a frozen contract §6 or real code, and which day)

## Week notes

- (decisions, findings, scope deviations — one line per event, with date)
- 2026-10-15 · A2.9: the SRT fails closed (`409 srt_unavailable`) for `timestamp_precision=none`,
  missing/unordered/overlapping times, or a segment past `duration_seconds`; `exports.srt`
  in the detail mirrors that same rule so the client can disable the download without probing.
- 2026-10-15 · A2.9: the SSE stream is state, not increment — every connect/reconnect re-sends
  a DB `snapshot` (even with `Last-Event-ID`), reports only measured bytes/fragments, and
  closes with exactly one `done`/`error`. The pure rules live in `services/{exports,progress}`;
  the polling generator lives in `services/library` to keep imports acyclic.
- 2026-10-22 · L2.5: `Last-Event-ID` is the last received character offset; replay always
  starts with `snapshot` and reads the persisted content/citations, never the provider. The
  outer stream explicitly closes the live generator so a client disconnect runs the `finally`
  that stops the producer and persists the partial. Historical markers are stripped from
  prompt history because old `[Sn]` indices point to old evidence, not the new source list.

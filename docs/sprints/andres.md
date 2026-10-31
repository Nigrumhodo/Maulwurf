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
| L2.6 | L- | in progress | Claim in `CLAIMS.md` (2026-10-30). Branch `feature/L2.6-validated-citations`: `SearchService.rehydrate_chunk_ids`/`rehydrate_source` reread a selected chunk with the same tenant/tombstone/active-generation/active-transcript/segment filters as retrieval; `citation_source_matches`, `validate_citation_source`, `validate_citation_record` and `resolve_citation` require the fresh row to match the source the model saw; `RAGService` validates each live `[Sn]` before emitting `citation` and before persisting it, drops invalid markers (fail closed on any `Exception`, including a 2 s validation timeout), memoizes validation per chunk within the stream, and revalidates persisted citations on replay (decision 4: replay revalidation was the safer option even though the original plan left it to A2.12/G5). `rehydrate_source` no longer rebuilds a snippet, and `rehydrate_chunk_ids` filters `Chunk.audio_id` explicitly in SQL. Labels keep `segment N` and now fall back when offsets are partial, non-finite, negative or malformed; the model never supplies IDs, labels, URLs or times. New `U-S2-JL-05` `apps/api/tests/unit/test_citation_validation.py` (47 tests) plus integration regressions in `test_chat_sse.py` for cross-tenant source, stale transcript version, retired generation, foreign segment, live invalidation before `[S1]`, replay invalidation, `citas=[]` and `message_sources` retention, and in `test_search.py` for audio-id filtering. 2026-10-30 UTC, WSL/Linux with Docker Desktop engine and a temporary `pgvector/pgvector:pg16`: `cd apps/api && uv run ruff check .` and `uv run mypy .` → clean (133 source files); `uv run pytest -m "not integration and not provider and not load"` → **662 passed** (297 deselected); `uv run pytest -m integration -q` → **297 passed** (112.59 s), including `test_chat_sse.py` 27. Mutation checks: bypassing the live `resolve_citation` call made `test_chat_drops_citation_after_source_invalidation` fail with `['delta', 'citation', 'done']`; removing the `Chunk.audio_id` filter made `test_rehydrate_chunk_ids_filters_by_audio_id` fail. Both restored and green. No migration, route/schema change, audio, PII or real provider call. PR pending; `done` requires review and Quality CI green. |
| A2.10 | A- | in progress | Claim in `CLAIMS.md` (2026-10-28). Branch `feature/A2.10-atomic-commit-outbox-gate`: API/DB regression `I-S2-AN-10` in `apps/api/tests/integration/test_transcript_commit_gate.py` (11 cases) proving the dispatcher holds `index`/`analyze` while the latest attempt has `cleanup_status=pending|failed` or no attempt, publishes each exactly once after verified cleanup (asserting the dispatcher `Counter`, not only ARQ job dedupe), proves Calendar/notification/deletion rows do not delay or block the gated events and pins `decide()` semantics, selects only the latest attempt and skips malformed gated rows. The S2.5 writer (`apps/ingest/maulwurf_ingest/persistence.py`) and its I-S2-SG-06/07/08 evidence stay unchanged; no production code changed. 2026-10-28, WSL/Linux with Docker Desktop engine: `cd apps/api && uv run ruff check app tests` and `uv run mypy app tests` → clean (128 source files); `uv run pytest -m "not integration and not provider and not load"` → **615 passed** (290 deselected); `MAULWURF_DATABASE_URL=…@127.0.0.1:55432 MAULWURF_REDIS_URL=…@127.0.0.1:6379 uv run pytest -m integration -q` against a temporary `pgvector/pgvector:pg16` and Redis 7.4 → **290 passed** (120.32 s), including `I-S2-AN-10` (11 passed). Red/green by layer: a temporary `decide()` cleanup-check mutation made `U-S1-JF-04` fail (6 failed, 23 passed); a temporary dispatcher `LATEST_CLEANUP` mutation that reports `verified` unconditionally, with the `decide()` layer untouched, made 4 `I-S2-AN-10` cases fail (4 failed, 7 passed); both files restored → 11 passed. QA corrections: explicit `Counter[Outcome]` assertions, non-gated non-interference plus a direct `decide()` assertion, `pending`/`failed` parametrization, missing-attempt fail-closed case, shape assertions in the fixture, the `redis` fixture moved to `tests/integration/conftest.py`, and renamed/clarified the post-commit lease case. Extra `apps/ingest` rerun of `tests/test_commit.py tests/test_reconciler.py`: **9 passed, 1 environment-only failure** in `test_leftover_directory_is_verified_and_enabled_without_asr`; WSL exposes 29 foreign-UID `/proc/*/fd` directories, so cleanup evidence fails closed (`unreadable_processes=29`) as designed for the single-UID hardened container, and no ingest code changed. No audio, no PII, no Riva call. PR #78 open; pending for `done`: PR review and Quality CI green. |
| L2.7 | L- | in progress | Claim in `CLAIMS.md` (2026-10-30). Branch `feature/L2.7-g4-evaluation`: offline G4 harness under `apps/api/tools/eval/` (`dataset`, `corpus`, `retrieval`, `chat`, `metrics`, `report`, `runner`, `cli`), `scripts/eval/g4.py`, frozen `g4-v1` dataset (10 classes per language, 14 segments and 1,058–1,174 words per class; 50 test + 5 development items per language, 35 answerable + 15 unanswerable in `test`), `smoke-v1` for CI, `docs/eval/README.md`, dataset README and unit/integration suites. The harness calls `SearchService.retrieve(top_k=8)` and `RAGService` in-process; it adds no HTTP route, request/response schema, migration or production behavior. Evidence: 2026-10-30 UTC, WSL/Linux with a temporary `pgvector/pgvector:pg16` on `127.0.0.1:55432` and Redis 7.4 on `6379`: `cd apps/api && uv run python -m tools.eval.cli validate --dataset tests/fixtures/g4/datasets/v1 --languages es,en,fr` → `OK: dataset g4-v1 (f4a4cf47…), 3 idiomas, 165 ítems`, exit 0; `uv run pytest tests/unit/test_g4_metrics.py tests/unit/test_g4_dataset.py -q` → **21 passed**; `uv run pytest -m "not integration and not provider and not load" -q` → **658 passed** (311 deselected); `uv run pytest -m integration tests/integration/test_g4_eval.py -q` → **14 passed** (21.46 s); `uv run ruff check .` → all checks passed; `uv run mypy .` → success, 145 source files. Integration cases use a deterministic hashed bag-of-words embedding provider and a scripted LLM; they cover tenant isolation, idempotent seeding/reset, evidence retrievability across all `chunk_segments` links, review-row completeness, unauthorized citations, incomplete adjudication (exit 3), per-language failure without macro compensation, errored/partial runs (exit 4), redaction and stable CLI codes. Dataset fix during implementation: the seeder stores the `"\n"` separator on every segment except the last so segment ranges stay contiguous for the chunker while `Transcript.text == "\n".join(...)` and authored evidence offsets remain valid. No real provider call, no audio, no PII, no human reviewer. **G4 remains `NOT EVALUATED`** and no report is published; `done` requires the authorized real run, human adjudication, PR review and Quality CI. |

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
- 2026-10-28 · A2.10: the atomic writer remains S2.5's `commit_transcript` in `apps/ingest`;
  creating a second writer in `apps/api` would duplicate the single source of truth. The
  missing contract proof is the API/DB regression I-S2-AN-10. Its assertions are scoped to
  the suite's own rows and `outbox:<id>` jobs, and an autouse fixture hides committed rows
  from earlier API tests inside the test's rolled-back transaction, so the dispatcher
  `Counter` is exact even though the integration session shares one migrated database.
- 2026-10-28 · A2.10 QA: red/green is split by layer. `decide()` is unit-tested
  (`U-S1-JF-04`) and the integration file targets the dispatcher's `LATEST_CLEANUP`
  selection; the fixture asserts the columns the gate consumes (`type`, `resource_type`,
  `resource_version`) and the `redis` fixture now lives in
  `tests/integration/conftest.py`.
- 2026-10-30 · L2.6: the label stays `segment N` (decision 1), matching `ESPECIFICACION.md`
  M7, `docs/plan/S2.md` and D2.7; it now falls back to the ordinal when offsets are
  partial, `NaN`/infinite, negative or reversed, so no `00:00` is invented. Replay also
  revalidates persisted citations (decision 4): a message completed before a generation,
  transcript or segment changed no longer re-emits a stale `citation` on reconnect. This
  extends the original plan, which left replay to A2.12/G5; no endpoint, schema or
  migration changed.
- 2026-10-30 · L2.7: G4 stays **`NOT EVALUATED`**. The harness, `g4-v1` and the fake-provider
  suites are complete, but publishing a pass requires authorized real embedding/LLM calls
  and a named human reviewer for every citation and unanswerable row; neither exists. The
  CLI never falls back to fakes, and the smoke dataset cannot reach the official verdict
  path. French v1 uses `simple` FTS; `ABSTENTION_TEXT` is Spanish-only and English/French
  refusals are human-labelled.
- 2026-10-30 · L2.7: the corpus seeder stores the `"\n"` separator on every segment except
  the last. The chunker requires contiguous segment ranges, while authored evidence offsets
  are relative to each segment's text; keeping the separator inside the preceding segment
  satisfies both without changing the chunker or the dataset.

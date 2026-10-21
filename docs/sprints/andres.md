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
| L2.4 + A2.11 | L- / A- | in progress | Claim in `CLAIMS.md` (2026-10-21). Branch `feature/L2.4-search-service-endpoint`: reusable `SearchService.retrieve/page`, `GET /search`, hydration, plain-text snippet, honest segment times and opaque keyset cursor. 2026-10-21, WSL/Linux: `uv run ruff check .` and `uv run mypy .` → clean; `uv run pytest -m "not integration and not provider and not load"` → **560 passed**; `MAULWURF_DATABASE_URL=…@127.0.0.1:55432 uv run pytest tests/integration/test_search.py -q` against `pgvector/pgvector:pg16` → **16 passed** (15.85 s), `I-S2-JL-06`. PR pending review. |

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

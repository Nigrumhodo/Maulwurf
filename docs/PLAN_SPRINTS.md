# Maulwurf — Proposed sprint sequence (S1–S4)

> Status: revision 2026-09-19; status text reconciled 2026-10-30. **Model: open backlog per
> sprint, no pre-assignment.** The
> only accepted ownership registry is [sprints/CLAIMS.md](sprints/CLAIMS.md); personal
> logbooks document work and evidence but do not assign tickets. Only an active sprint can
> open for claiming and future weeks remain blocked. Subordinate to
> [ESPECIFICACION.md](ESPECIFICACION.md) (contracts) and
> [PLAN_IMPLEMENTACION.md](PLAN_IMPLEMENTACION.md) (gates and evidence). The **detailed
> specification of how each sprint must work** lives in [plan/S1.md](plan/S1.md) ·
> [plan/S2.md](plan/S2.md) · [plan/S3.md](plan/S3.md) · [plan/S4.md](plan/S4.md) (frozen
> contracts, per-ticket behavior, acceptance criteria, and tests). If this document
> contradicts the specification, the specification wins. Implementation status is tracked in
> [sprints/CLAIMS.md](sprints/CLAIMS.md); the backlog defines scope, not evidence.

## 0. Planning model

### 0.1 How it works

1. **Backlog by feature:** each week (§2–§5) publishes open tickets grouped by area, with
   dependencies and tests ([PLAN_TESTS.md](PLAN_TESTS.md)). The ID letter does not assign
   a person or team.
2. **A single open sprint:** the opening state lives in
   [sprints/CLAIMS.md](sprints/CLAIMS.md). Only tickets from the active sprint are
   claimed; S2–S4 are not reserved in advance. The next sprint opens only after reviewing
   the previous gate, capacity, and team availability.
3. **Claim accepted by PR:** ownership is created only by merging a valid row in
   `CLAIMS.md`, with ticket, single owner, `claimed_at` UTC, status, non-sensitive cloud
   eligibility, and review. A personal row, comment, or branch does not reserve a ticket.
4. **Declared dependencies:** the provider freezes the contract on Monday and delivers a
   **first integrable implementation** no later than day 3; final code is not required
   that day. Consumers advance against the contract/mock, and integration, tests, and
   closure are completed on days 4–5.
5. **Definition of Done per sprint:** each week closes only if it meets its DoD
   (functional, tests per level, documentation, and demo). The detail of each level is in
   [PLAN_TESTS.md](PLAN_TESTS.md).
6. **Authority and evidence:** `CLAIMS.md` is canonical for ownership/status; logbooks
   keep commands, date, environment, results, and notes. A pre-existing row in a logbook
   without an accepted claim is a historical candidate, not ownership. A `done` without
   evidence goes back to `in progress`.

### 0.2 Areas and profiles

Ticket identifiers keep the historical letter; **the letter designates the area, not a
person**:

| Letter | Area | Typical folders | Suggested profile |
|---|---|---|---|
| `L-` | RAG · prompts · quality · leadership | `apps/api/prompts/`, datasets, ADRs | LLM/embeddings, coordination |
| `A-` | API · database | `apps/api` (routers, services, models, core) | Python/FastAPI/SQLAlchemy |
| `S-` | Ingestion · audio · ASR | `apps/ingest`, Riva client, ffmpeg, tmpfs | Systems, audio, resilience |
| `J-` | Infra · workers · integrations | `infra/`, CI, `apps/api/app/workers/`, Google | Docker/CI, ARQ/Redis, Google APIs |
| `D-` | Web · UX | `apps/web` | React/Vite/TypeScript, UX |

Any member may claim from any area. A ticket whose Tests field includes `P-*`, or whose
execution makes a real call to Riva, LLM/embeddings, or Google, requires the owner to have
authorized eligibility and an approved budget. `CLAIMS.md` records only `no_aplica` or
`verified:<provider>`, never accounts, keys, tokens, or sensitive quotas. Crossing
folders requires review from the owner of that interface or, if there is none, from a
sprint reviewer; that review does not turn the reviewer into an owner.

### 0.3 Anti-collision rules

1. **Interfaces frozen on Monday** (§6): changing them requires review and updating
   consumers the same day. Day 3 requires a first integrable implementation, not the
   final version; closure happens on days 4–5.
2. **One ticket, one owner:** contributors and reviewers are acknowledged in
   PRs/logbooks, but do not share ownership. The current `CLAIMS.md` table does not allow
   two rows for one ticket.
3. **Concurrency:** the first reviewed and merged claim PR wins. Every concurrent PR
   rebases against the target branch; if the ticket already appears, it stays in conflict
   and does not grant ownership. There are no reservations by comment order or prior
   work.
4. **Ownership changes:** transfer and unclaim require an atomic PR, a motive, evidence
   of the work state, and history in `CLAIMS.md`. The previous owner keeps the ticket
   until the transfer merges; when an unclaim merges, the ticket returns to the backlog.
5. **Auditable split:** invented child IDs are not claimed in `CLAIMS.md`. First this
   backlog is updated and merged with the child IDs and the parent's disposition;
   afterwards each child may be claimed in another PR. Out-of-scope ideas go to F4.
6. **Closed statuses:** the canonical vocabulary is the one in
   [sprints/CLAIMS.md](sprints/CLAIMS.md) §2 — `pending`, `in progress`, `blocked`,
   `done`, `transferred`, and `cancelled`. Claiming, transferring, and unclaiming are
   **actions/events**, not statuses; `continuous` describes the bounded nature of a ticket,
   never a status. A continuous-work ticket is bounded to the sprint and closes with
   evidence; it does not remain `in progress` when the next week opens.

## 1. Sprint summary

| Week | Name | Objective in one sentence | Exit gate (summary) |
|---|---|---|---|
| S1 | **Foundation + spike** | Monorepo running, basic auth, initial schema, and a real Riva contract tested | F0: G1 and exactly G2-T or G2-X; decisions D2/D3/D4/D6 |
| S2 | **Knowledge (F1)** | Upload audio → transcript → hybrid index → chat with citations, complete vertical slice | 1 h E2E; G1, G2-T or G2-X, G3, G4, and G5-F1 |
| S3 | **Action (F2)** | LLM extraction, review tray, idempotent Calendar, read-only tools | G5-F2, G6, and G7-CAL: confirmation → one event |
| S4 | **Reminders and close (F3)** | Gmail opt-in, reminders/digests, full dashboard, Ctrl+K, hardening | G7-MAIL and G8; final demo + security/costs reviewed |

The initial state and every opening are audited in [sprints/CLAIMS.md](sprints/CLAIMS.md):
there is no active sprint or claimed ticket until an opening PR records capacity and
availability. S1 opens first; S2–S4 remain blocked until the previous gate closes.

Hard rule: **no real ingestion before S1 closes the F0 gate** (D3 retention, D2/D4
limits, D6 timestamps, G1, and an approved G2 variant).

**Status (reconciled 2026-10-30):** S1 is complete except L1.7 (the formal close), and S2
is open. The repository has a running 8-service compose stack, four Alembic migrations,
sessions/CSRF, Google login, subject CRUD, a hardened ingestion package, search and chat
APIs, tests and quality CI. The web is a Vite + React SPA (ADR-0007), not the original
Next.js scaffold. Exact ownership and state remain in [sprints/CLAIMS.md](sprints/CLAIMS.md).

## 2. Week 1 (S1) — Foundation + feasibility spike (F0)

**Objective:** monorepo running, basic auth, initial schema, and a real Riva contract
tested. **Detailed specification:** [plan/S1.md](plan/S1.md). Suggested sequence per area:
[DISENO_BD_API_SPRINTS.md](DISENO_BD_API_SPRINTS.md) §4.1.

**Interfaces frozen on Monday:** session/cookie and CSRF contract; schema of
`POST /audios` + binary `PUT` (relative upload URL); outbox shape; ingestion states (§M2);
progress SSE response shape.

### S1 backlog — RAG · leadership (`L1.x`)

| Ticket | Task (summary) | Depends on | Tests |
|---|---|---|---|
| L1.1 | Verify/adjust monorepo structure §3.3, versions, and lockfiles *(skeleton already in repo)* | — | — |
| L1.2 | Short ADRs: structure, versions, lockfile policy, commit/PR format | L1.1 | — |
| L1.3 | Spike protocol: what to measure, report format, D2/D4/D6 criteria | L1.1 | — |
| L1.4 | Pydantic draft of the analysis (§M4) and chunk (§M6) schemas for freezing | — | U-S1-JL-01/02 |
| L1.5 | G4 dataset format: ≥50 questions/language, 15 unanswerable, expected evidence | — | U-S1-JL-03 |
| L1.6 | Freeze S1 interfaces (§6) | — | — |
| L1.7 | S1 coordination, board, and reviews; closure with decisions and blockers recorded | — | — |

### S1 backlog — API/DB (`A1.x`)

| Ticket | Task (summary) | Depends on | Tests |
|---|---|---|---|
| A1.1 | Verify the `apps/api` scaffold (FastAPI + Pydantic v2 + async SQLAlchemy) *(base already created)* | L1.1 | — |
| A1.2 | Complete the Pydantic Settings config; secrets only from the environment *(skeleton already created)* | A1.1 | — |
| A1.3 | Google OAuth backend: `state`, PKCE, `nonce`/issuer/audience; identity from `sub` | A1.2 | I-S1-AN-06 |
| A1.4 | Opaque session: `Secure/HttpOnly/SameSite=Lax` cookie, hash in `sessions`, revocation; CSRF/Origin | A1.2 | U-S1-AN-01/02 |
| A1.5 | Core tables `users/sessions/google_credentials/subjects`; AES-GCM with nonce and key version; composite FKs; base RLS. S1 shape of `audios`, `ingestion_attempts`, and `outbox_events` (§6a: the outbox table is created in S1) | A1.1 | U-S1-AN-03, I-S1-AN-05 |
| A1.6 | Subject CRUD with the deletion rule for subjects with active classes | A1.5 | I-S1-AN-07 |
| A1.7 | Alembic migrations from empty and upgrade | A1.5 | I-S1-AN-04 |
| A1.8 | Skeleton `POST /audios` + binary `PUT` (frozen contract); `GET/PATCH /me`, logout, `GET /integrations/status` | A1.4, L1.6 | — |
| A1.9 | Basic isolation tests: user B cannot see A's resources, not even by guessing IDs | A1.5 | I-S1-AN-05 |

### S1 backlog — Ingestion/ASR (`S1.x`)

| Ticket | Task (summary) | Depends on | Tests |
|---|---|---|---|
| S1.A1 | In-RAM synthetic audio generator with known expected text | — | U-S1-SG-01 |
| S1.A2 | First real call to Riva (`nvidia-riva-client==2.27.0`, Bearer backend only) | L1.3; authorized API key | P-S1-SG-07 |
| S1.A3 | Candidate languages one by one; no `multi`, no translation | S1.A2 | P-S1-SG-07 |
| S1.A4 | Input formats and effective sample rate (WAV PCM s16 mono candidate) | S1.A2 | U-S1-SG-02 |
| S1.A5 | Limits: payload, duration, quotas, deadlines, cancellation (budget) | S1.A2 | P-S1-SG-07/08 |
| S1.A6 | Word/segment offsets → `word\|segment\|none` decision | S1.A2 | P-S1-SG-07, U-S1-SG-03 |
| S1.A7 | Latency by duration/language, SDK memory, cost | S1.A2 | P-S1-SG-08 |
| S1.A8 | NVIDIA contract report + review of retention/processing terms — closes D3 (no keys, no audio) | S1.A3–A7 | — |
| S1.A9 | Present the report; close D2/D3/D4/D6 with the team | S1.A8 | — |
| S1.B1 | Ingestion with read-only root, bounded tmpfs, non-root, no swap/core dumps | J1.2 (image) | I-S1-SG-06 |
| S1.B2 | RAM reservation per slot (200 MiB/3 h as a target to validate) | S1.B1 | — |
| S1.B3 | Supervisor, lease, fencing, and cleanup independent of the ASR process | S1.B1, A1.5 (`ingestion_attempts`) | I-S1-SG-04/05 |
| S1.B4 | Failure matrix: 9 scenarios with real injection (SIGKILL, restarts, etc.) | S1.B3 | I-S1-SG-04 |
| S1.B5 | Verification that audio is absent from disk/Redis/logs/traces/caches | S1.B3 | I-S1-SG-06 |
| S1.B6 | Expired lease does not mark cleanup verified; outbox blocked without evidence | S1.B3 | I-S1-SG-05 |

### S1 backlog — Infra/Integrations (`J1.x`)

| Ticket | Task (summary) | Depends on | Tests |
|---|---|---|---|
| J1.1 | `docker-compose.yml` with 8 services; pinned `pgvector/pgvector:pg16`; named volume for PG; secrets outside compose; multi-stage non-root images; no audio volume | L1.1 | I-S1-JF-01 |
| J1.2 | Hardened `ingest` service (read-only, tmpfs, non-root, limits) with ffmpeg/ffprobe | J1.1 | I-S1-SG-06 |
| J1.3 | Local TLS Caddy without buffering/caching for uploads | J1.1 | I-S1-JF-02 |
| J1.4 | CI: ruff/ESLint, mypy/tsc, pytest/Vitest, integration, builds; PR template implementing ADR-0004 | J1.1 | green pipeline |
| J1.5 | ARQ worker+scheduler skeleton, basic outbox dispatcher, `/healthz` `/readyz` | J1.1, A1.5 (outbox table), A1.8 | I-S1-JF-01, U-S1-JF-04 |
| J1.6 | Redis for IDs/state only; persistence only in Postgres | J1.1 | I-S1-JF-03 |
| J1.7 | README with reproducible commands (bring up, test, lint) | J1.1 | — |
| J1.8 | Base `ingest` image with pinned ffmpeg/ffprobe for compose; compare the version against the spike's 8.0.1 | J1.1 | — |

### S1 backlog — Web/UX (`D1.x`)

| Ticket | Task (summary) | Depends on | Tests |
|---|---|---|---|
| D1.1 | Verify the `apps/web` scaffold (Vite + React + TS + Tailwind, ADR-0007) *(base already created; placeholder landing replaced in D1.8)* | L1.1 | — |
| D1.2 | Base routes (`/biblioteca`, `/chat`, `/materias`, `/settings`) and navigation layout | D1.1 | — |
| D1.3 | Google login with redirect/callback; logout; loading/error states | A1.3/A1.4, L1.6 | — |
| D1.4 | HTTP client: CSRF on mutations; 401/403/429 interceptors | L1.6 | U-S1-DA-01 |
| D1.5 | Base components: date/zone/language inputs, status badges, empty states, toasts | D1.1 | U-S1-DA-02, C-S1-DA-03 |
| D1.6 | Subject CRUD against the real API | A1.6 | U-S1-DA-02 |
| D1.7 | Static upload and library drafts (no wiring) to validate UX early | D1.5 | — |
| D1.8 | Public product landing: hero, UPLOAD→FIND→DECIDE flow, privacy panel, FAQ, footer; visible for visitors on `/` (with an active session `/` remains the dashboard, M9). **Pending design decision: there is no current front-end proposal** | D1.5; design decision | — |

### Definition of Done (S1 exit gate)

**Functional (demonstrable in compose):**
- `docker compose up` brings up the 8 services with no audio storage.
- Login/logout/`GET /me` working through Caddy; subject CRUD; migrations from empty.
- Spike report published; prototype failure matrix demonstrated; D2/D3/D4/D6 closed.
- Static public landing on `/` for visitors (D1.8), with no private data or secrets.

**Green tests (see PLAN_TESTS §1):**
- Unit `U-S1-*`; integration `I-S1-*` (compose, no-buffering, Redis, migrations, isolation).
- Provider-contract `P-S1-SG-07/08` executed (protected, with budget) → report.
- E2E smoke: login from the browser.

**Documentation:** ADRs reviewed; spike report; startup README; spec/plan updated if a
contract changed.

**Claims and logbooks:** ownership/status closed in `sprints/CLAIMS.md`; every `done`
ticket links reproducible evidence in a PR or logbook (command, date, environment, result).

## 3. Week 2 (S2) — Knowledge: F1 vertical slice

**Objective:** upload audio → transcript → hybrid index → chat with citations, end to end.
**Detailed specification:** [plan/S2.md](plan/S2.md). Suggested sequence per area:
DISENO_BD_API_SPRINTS.md §4.2.

**Interfaces frozen on Monday:** final chunks/embeddings shape and the `index(audio_id)`
job; chat SSE events `snapshot|delta|citation|done|error` with a monotonic ID and a single
terminal event; citation format `[Subject · Class · mm:ss]`; paginated `/search` contract.

**Implementation-gap tickets.** Rows marked *(gap, P0|P1|P2)* were added after the QA review of
2026-10-11 to cover gaps between the code and the specification that no ticket owned (detail,
evidence and priorities in [plan/S2.md](plan/S2.md) §10). They are claimed like any other ticket;
the lead decides which `P1`/`P2` ones move to S3. `A1.3` (Google login) and `A1.6` (subject CRUD)
were open S1 tickets when this backlog was written; both are now `done` (see
[sprints/CLAIMS.md](sprints/CLAIMS.md)).

### S2 backlog — RAG (`L2.x`)

| Ticket | Task (summary) | Depends on | Tests |
|---|---|---|---|
| L2.1 | Chunking ~800/100 tokens, cuts at sentence/segment, Unicode offsets, `chunk_segments` | L1.4; A2.1 tables (contract) | U-S2-JL-01 |
| L2.2 | Versioned embeddings + cosine HNSW; index generation with atomic activation | A2.1 | U-S2-JL-02, P-S2-JL-10 |
| L2.3 | Per-language FTS + pgvector + RRF fusion; tenant filters in both branches before top-k | A2.1 | U-S2-JL-03/04 |
| L2.4 | `SearchService` + `GET /search` (paired with A2.11) | L2.3 | I-S2-JL-06 |
| L2.5 | Chat SSE: embedding → 30/branch → RRF → ~8 chunks under budget; streaming POST + cancel with minimal turn persistence | L2.4 | I-S2-JL-07/08 |
| L2.6 | Citations only to IDs validated in the DB; "segment N" without offsets; complete `message_sources` | L2.5 | U-S2-JL-05 |
| L2.7 | G4 evaluation on the S1 dataset (Recall@8 with active filters ≥ 0.85, citations ≥ 0.95, abstention ≥ 0.90) | L2.1–L2.6 | G4 |
| L2.8 | Retrieval p95 ≤ 500 ms and chat first token p95 ≤ 5 s, measured | L2.4 | G4 |
| L2.9 | *(gap, P1)* Align the §M6 `Chunk` contract with the `chunks`/`chunk_segments` tables | L1.4; A2.1 | U-S2-JL-11 |
| L2.10 | *(gap, P0)* ADR: web stack (Next.js vs the Vite/Express proposal of PR #42) | — | C-S2-JL-12 |
| L2.11 | *(gap, P2)* Documentation reconciliation: stale status, missing paths, `CLAIMS.md` rows, reference checker | — | C-S2-JL-13 |

### S2 backlog — API/DB (`A2.x`)

| Ticket | Task (summary) | Depends on | Tests |
|---|---|---|---|
| A2.1 | Complete S2 migration: transcript/segments, chunks/embeddings, chat, and adjustments to `audios`/`ingestion_attempts`/`outbox_events` created in A1.5 (tenant-aware FKs) | A1.5 | I-S2-AN-05, I-S2-AN-11 |
| A2.2 | `GET /ingestion/capabilities` with spike limits (no hard-coded constants) | S1.A8 | U-S2-SG-01 |
| A2.3 | `POST /audios`: versioned consent, slot reservation, relative URL; no dedupe here | A2.1, L1.6 | U-S2-AN-01 |
| A2.4 | Authenticated binary `PUT`; SHA-256 and dedupe after cleanup; cut off at the limit | A2.3 | I-S2-AN-05 |
| A2.5 | Dedupe by identity on PUT; opaque one-use variant token; never retain audio | A2.3 | U-S2-AN-02, I-S2-AN-05 |
| A2.6 | Independent per-stage states + aggregate (`processing/ready/partial/failed`) | A2.1 | U-S2-AN-03 |
| A2.7 | Cancellation, tombstone, and fencing; `DELETE /audios/{id}` purging derivatives | A2.6 | U-S2-AN-04, I-S2-AN-06 |
| A2.8 | `retry` (analysis/index only) vs `reupload` (no usable transcript) | A2.6 | I-S2-AN-08 |
| A2.9 | Listing, `GET /audios/{id}`, SSE progress from the DB, conditional `txt`/`srt` exports | A2.6 | I-S2-AN-07/09 |
| A2.10 | Atomic commit + `cleanup_pending` state; outbox gate only for index/analyze | S2.5 | I-S2-AN-10 |
| A2.11 | `GET /search` (paired with L2.4) | L2.3 | I-S2-JL-06 |
| A2.12 | M7 conversation CRUD (all/subject/class modes), paginated history, and delete/purge; L2.5 already provides the streaming `POST .../messages` and `.../cancel` | A2.1, L2.5 (contract) | I-S2-AN-11 |
| A2.13 | *(gap, P0)* API↔ingest reception contract: ADR on where the `PUT` terminates, `ReceptionSink` client, internal credential (pairs with S2.11) | A2.4; S2.1 | I-S2-AN-12 |
| A2.14 | *(gap, P1)* Reservation lifecycle and `PUT` concurrency: row lock and `receiving` transition, atomic quota/slot, stale-reservation expiry | A2.3, A2.4 | I-S2-AN-13 |
| A2.15 | *(gap, P2)* Error envelope on 404, 405 and unhandled exceptions | A2.4 | U-S2-AN-14 |
| A2.16 | *(gap, P1)* Fail-closed settings: `MAULWURF_ENV` required, secret strength and distinctness | A1.2 | U-S2-AN-15 |
| A2.17 | *(gap, P1)* PostgreSQL RLS with `SET LOCAL app.user_id` (ADR-0006 second barrier) | A2.1; A1.9 | I-S2-AN-16 |
| A2.18 | *(gap, P1)* Schema integrity follow-up (new migration after A2.20): FK indexes, version invariants, drift test, and the build-phase fence left by ADR-0009 | A2.1, A2.20 | I-S2-AN-17 |
| A2.19 | *(gap, P1)* Contract conformance of A1.8/A2.3/A2.4: TTL, language, token codes, `/me`, single limit source | A2.4 | U-S2-AN-18 |
| A2.20 | *(gap, P1)* `processing_runs` fencing: a stale worker cannot finish a run or activate a generation after the reconciler reclaimed it | A2.1, J2.2, J2.4 | I-S2-JF-15 |

### S2 backlog — Ingestion/ASR (`S2.x`)

| Ticket | Task (summary) | Depends on | Tests |
|---|---|---|---|
| S2.1 | Streaming reception to tmpfs, SHA-256, full ffprobe validation | A2.2/A2.3; S1.A8 | U-S2-SG-02 |
| S2.2 | ffmpeg with no network or shell, with limits; WAV PCM normalization; fragmentation by real limits | S2.1 | U-S2-SG-03 |
| S2.3 | Sequential processing with backpressure; boundary/overlap reconciliation | S2.2 | U-S2-SG-04 |
| S2.4 | Blocking Riva client outside the event loop; classified retries | S2.2 | U-S2-SG-05, P-S2-SG-12 |
| S2.5 | Atomic transcript+segments commit (contract shared with A2.10) | S2.3/S2.4, S2.10 | I-S2-SG-06 |
| S2.6 | Cleanup in `finally`; separate `cleanup_status`; outbox only after commit+cleanup | S2.5 | I-S2-SG-07 |
| S2.7 | Post-commit reconciler (no retranscription); fencing token | S2.6 | I-S2-SG-08/09 |
| S2.8 | Cancellation: disconnect/lease/tab/post-commit | A2.7 | I-S2-SG-10 |
| S2.9 | E2E 1 h synthetic class with `word\|segment` (G2-T) or `none` with textual UI (G2-X) | S2.1–S2.8, S2.10, S2.11; A2.13; J2.8; D2.2/D2.3 | E-S2-SG-11 |
| S2.10 | *(gap, P0)* Wire the pipeline to the Riva client; silence or empty text is `rejected`, never `succeeded` | S2.3, S2.4 | I-S2-SG-13 |
| S2.11 | *(gap, P0)* Streaming reception endpoint in `ingest`; remove the in-RAM audio buffer | A2.13; S2.1 | I-S2-SG-14 |
| S2.12 | *(gap, P1)* Persisted admission latch, heartbeat failure handling and ffprobe containment | S1.B6, S1.B3, S2.1 | I-S2-SG-15 |
| S2.13 | *(gap, P1)* Error-code catalog and outcome mapping between ingest and API | S2.1; A2.13 | U-S2-SG-16 |

### S2 backlog — Infra/Integrations (`J2.x`)

| Ticket | Task (summary) | Depends on | Tests |
|---|---|---|---|
| J2.1 | Real outbox dispatcher: cleanup gate only for index/analyze; other types enabled | A2.10; U-S1-JF-04 | U-S2-JF-01 |
| J2.2 | Idempotent `index(audio_id)` job; retries with backoff+jitter | J2.1 | I-S2-JF-02 |
| J2.3 | Recovery after losing Redis (regeneration from DB/outbox) | J2.1 | I-S2-JF-03 |
| J2.4 | Reconciler: dead attempts, stuck outbox, lease duplicates | J2.2 | I-S2-JF-04 |
| J2.5 | Observability: JSON logs with IDs, RAM/tmpfs/slot metrics, cleanup alert | J2.1 | C-S2-JF-10 |
| J2.6 | Automated E2E smoke in the pipeline | J2.2; S2.9; D2.2; D2.6 | E-S2-JF-06 |
| J2.7 | Fault injection with the commit tickets (S2.5/A2.10) | S2.5, A2.10 | I-S2-JF-05 |
| J2.8 | *(gap, P0)* Compose/env wiring for `ingest` (Riva credentials, `INGEST_*`, origin override, `INGEST_SLOTS`) | A2.13 | I-S2-JF-11 |
| J2.9 | *(gap, P2)* CI hygiene: no empty-suite tolerance, Redis `maxmemory`, secret and license scan | J1.4 | C-S2-JF-12 |
| J2.10 | *(gap, P1)* Ingress decision (Cloudflare Tunnel dropped), its upload limit and timeouts, and Caddy body/timeout limits | Ingress decision | C-S2-JF-13 |
| J2.11 | *(gap, P1)* Reconciler: keep Redis I/O outside database row locks so a degraded Redis cannot hold up to 200 rows or trigger idle-in-transaction | J2.4 | I-S2-JF-14 |

### S2 backlog — Web/UX (`D2.x`)

| Ticket | Task (summary) | Depends on | Tests |
|---|---|---|---|
| D2.1 | M2 upload form: language with no preselection, visible consent | A2.3, L1.6 | U-S2-DA-01 |
| D2.2 | Streaming `PUT` upload with real progress; canceling the upload cancels the attempt | A2.4 | U-S2-DA-02 |
| D2.3 | SSE progress + polling fallback; real bytes, zero invented percentages | A2.9 | U-S2-DA-03 |
| D2.4 | Library with honest per-stage states; "upload again" ≠ "retry" | A2.9 | E-S2-DA-06 |
| D2.5 | `TranscriptReader` with `?segment=`, conditional export, empty summary when `not_requested` | A2.9 | C-S2-DA-04 |
| D2.6 | Chat UI: conversation history, SSE with `fetch`, `delta/citation/done/error` events, cancellation | L2.5, A2.12 | C-S2-DA-05 |
| D2.7 | Clickable citations → reader with highlighted segment; sanitized Markdown | L2.6, D2.5 | C-S2-DA-05, E-S2-JL-09 |
| D2.8 | Browser privacy: `no-store`, releasing `File`/object URLs, no private cache | continuous | C-S2-DA-07 |

### Definition of Done (S2 exit gate)

**Functional:** real browser → proxy → services E2E with a 1 h synthetic class and the
published effective limit; audio absent before indexing; chat answers with valid citations and
persistent history (conversations/messages, idempotent `client_message_id`).

**Green tests (PLAN_TESTS §2):** unit `U-S2-*`; integration `I-S2-*` (concurrent dedupe,
cleanup, fencing, reconciler); E2E smoke `E-S2-JF-06` on every MR;
`E-S2-SG-11` with the 1 h class. G1, G2-T or G2-X, and G3 with evidence; G4 and G5-F1 complete
(Recall@8, citations, per-language abstention with numerator/denominator).

**Implementation gaps (proposed, needs the lead's approval):** the `P0` *(gap)* tickets are `done`,
or the lead records a reason for deferring each one; `P1`/`P2` ones are done or explicitly moved
to S3.

**Documentation:** S2 frozen contracts unchanged; `timestamp_precision` decision
documented; D3-Text closed for the embeddings/LLM provider before processing non-synthetic
text; spec/plan updated if a contract changed; logbooks with evidence.

## 4. Week 3 (S3) — Action: extraction and Calendar (F2)

**Objective:** proposal → review → confirmation → **a single event**; no write without
human confirmation. **Detailed specification:** [plan/S3.md](plan/S3.md).
Suggested sequence per area: DISENO_BD_API_SPRINTS.md §4.3.

**Interfaces frozen on Monday:** the `tasks` + revisions contract; the `calendar_events`
shape; read-only chat tools (`get_calendar_events`, `get_pending_tasks`,
`propose_task`) and iteration limits; prompts v1.

### S3 backlog — RAG (`L3.x`)

| Ticket | Task (summary) | Depends on | Tests |
|---|---|---|---|
| L3.1 | Versioned `apps/api/prompts/v1/` + regression tests in CI | L1.4 | R-S3-JL-06 |
| L3.2 | Final §M4 Pydantic schema (Unicode spans, verbatim quote, `date_status`) | L1.4 | U-S3-JL-01 |
| L3.3 | Deterministic post-LLM validation: membership, literal evidence against the DB | L3.2 | U-S3-JL-01/02 |
| L3.4 | Date resolution against the class time zone (DST, relative, ambiguous → `needs_review`) | L3.2 | U-S3-JL-03 |
| L3.5 | Windows with overlap + dedupe by evidence; idempotency with `processing_runs` | S3.4 | U-S3-JL-04 |
| L3.6 | `analyze(audio_id)` + backend orchestrator for read-only tools; reanalysis with review | J2.2, A3.1 | U-S3-JL-05, I-S3-AN-04 |
| L3.7 | G6 evaluation (≥50 activities/language; precision ≥ 0.95, recall ≥ 0.85) | L3.1–L3.5 | P-S3-JL-07 |
| L3.8 | Summary (3–5 sentences) and versioned topics | L3.1 | R-S3-JL-06 |

### S3 backlog — API/DB (`A3.x`)

| Ticket | Task (summary) | Depends on | Tests |
|---|---|---|---|
| A3.1 | Complete `tasks` table: exclusive CHECKs, `date_status`, version, separate `calendar_sync_status` | A2.1; L3.2 (contract) | U-S3-AN-01 |
| A3.2 | Evidence + `task_sources` (manual without sources; corpus with validated sources) | A3.1 | I-S3-AN-05 |
| A3.3 | M4 endpoints with optimistic locking (`confirm/dismiss/complete`, manual/chat POST) | A3.1 | U-S3-AN-02 |
| A3.4 | Atomic confirmation: task + outbox in one transaction; 202 with `pending` | A3.3, J1.5 | I-S3-AN-03 |
| A3.5 | Reanalysis creates a new review; does not touch edited/confirmed tasks | A3.1 | I-S3-AN-04 |
| A3.6 | Evidence reconstruction from the DB to validate L3.3 spans | L3.3 | I-S3-AN-05 |
| A3.7 | Status rules: confirming requires a date, a Calendar block, and evidence; `blocked→pending` only on reconnect | A3.3 | I-S3-AN-03 |
| A3.8 | Calendar `settings` + `calendar_events` mirror (write/read calendars) | A3.1 | I-S3-JF-05 |
| A3.9 | Idempotency tests: double analysis/confirmation does not duplicate | A3.4 | I-S3-AN-06 |

### S3 backlog — Ingestion/ASR (`S3.x`)

| Ticket | Task (summary) | Depends on | Tests |
|---|---|---|---|
| S3.1 | Analysis backfill over F1 classes: only from persisted text | L3.6 (day-1 contract), A3.1 | I-S3-SG-01 |
| S3.2 | Efficient windows with measured token cost | S3.1 | U-S3-SG-02 |
| S3.3 | Robustness without ASR offsets: textual evidence, zero invented minutes | S3.1 | U-S3-SG-03 |
| S3.4 | `processing_runs` per transcript/config version | S3.1 | I-S3-SG-04 |
| S3.5 | ASR and RAM/tmpfs cost measurement under load | S3.2 | C-S4-SG-04 baseline |
| S3.6 | Hardening: backoff+jitter (max 3 transient); ASR quality warnings | S3.1 | U-S3-SG-05 |

### S3 backlog — Infra/Integrations (`J3.x`)

| Ticket | Task (summary) | Depends on | Tests |
|---|---|---|---|
| J3.1 | Verify real scopes (D7) with the protected account; record findings | authorized credentials | P-S3-JF-01 |
| J3.2 | `GoogleCalendarService`: isolated blocking; `Retry-After`; `invalid_grant` disconnects | J3.1 | P-S3-JF-02 |
| J3.3 | `POST /integrations/google/connect\|reconnect` endpoints (incremental OAuth) + single "Maulwurf" secondary calendar; store the ID; reconcile ambiguous creation | J3.2, A3.8 | I-S3-JF-06 |
| J3.4 | `sync_task_event(task_id, version)` job with deterministic ID; timeout→query; 409→reconcile | A3.4 (outbox) | I-S3-JF-03, E-S3-DA-05 |
| J3.5 | `[Maulwurf]` event with an authenticated link to the segment; correct all-day vs instant | J3.4 | I-S3-JF-03 |
| J3.6 | Patch with ETag; do not overwrite external changes; completing cancels reminders | J3.4 | I-S3-JF-04 |
| J3.7 | -7/+60 day read with pagination; atomic publication into `calendar_events`; `GET /calendar/events` | A3.8 | I-S3-JF-05 |
| J3.8 | Conflicts (overlap ≠ clustering); `GET /calendar/conflicts`, `POST /calendar/sync` | J3.7 | I-S3-JF-07 |
| J3.9 | Protected test account; test resources with cleanup (bounded to the sprint) | D7-CAL | C-S3-JF-08 |

### S3 backlog — Web/UX (`D3.x`)

| Ticket | Task (summary) | Depends on | Tests |
|---|---|---|---|
| D3.1 | "To confirm" tray: statuses, evidence with citation, `date_status` badges | A3.3; L3.2 (schema) | C-S3-DA-01 |
| D3.2 | Editing with separate due-date/schedule blocks (editable default duration) | A3.3 | C-S3-DA-02 |
| D3.3 | Confirm/discard/complete with version; 409 → refresh; write preview | A3.3/A3.4 | C-S3-DA-03 |
| D3.4 | "View transcript segment" from the task | D2.5 | C-S2-DA-04, E-S2-JL-09 |
| D3.5 | Class page: summary, topics, items, analysis/index statuses | D2.5, L3.8 | C-S3-DA-06 |
| D3.6 | Dashboard with agenda without duplicating task/event; visible sync freshness | J3.7, A3.8 | C-S3-DA-07 |
| D3.7 | Per-task sync states + Google reconnection UI | J3.4 | C-S3-DA-08 |
| D3.8 | Read-only chat tools; `propose_task` creates a proposal, never writes the agenda | L3.6 (tools) | C-S3-DA-04 |

### Definition of Done (S3 exit gate)

**Functional:** E2E with a Google test account: proposal → review → confirmation →
a single event; agenda reading and confirmed editing; rejected scopes do not break
library/chat.

**Green tests (PLAN_TESTS §3):** unit `U-S3-*`; regression `R-S3-JL-06` in CI;
integration `I-S3-*` (idempotency, ETag, review, reconnect); provider `P-S3-JF-01/02` and
`P-S3-JL-07` (protected, budget). G5-F2, G6, and G7-CAL with evidence.

**Documentation:** prompts v1 registered with `prompt_version`; D7 findings documented;
spec/plan updated if a contract changed; logbooks with evidence.

## 5. Week 4 (S4) — Reminders, close-out, and demo (F3)

**Objective:** reliable reminders, full dashboard, hardening, and the final demo.
**Detailed specification:** [plan/S4.md](plan/S4.md). Suggested sequence per area:
DISENO_BD_API_SPRINTS.md §4.4.

**Interfaces frozen on Monday:** preferences/notifications; reminder dedupe; final UI
routes.

### S4 backlog — API/DB (`A4.x`)

| Ticket | Task (summary) | Depends on | Tests |
|---|---|---|---|
| A4.1 | `notifications` + Gmail/digests extension of `settings` (the Calendar base already exists from S3) | A3.8 | U-S4-AN-01/02 |
| A4.2 | `PATCH /me/notifications` + `POST /notifications/test` | A4.1 | I-S4-JF-05 |
| A4.3 | Changing due date/preferences cancels only obsolete future occurrences | A4.1 | U-S4-AN-03 |
| A4.4 | Account deletion with an observable operation: `DELETE /me` + `GET /me/deletion/{id}` | A3.8, J3.3 | I-S4-AN-04 |
| A4.5 | D8 already approved: backup and restore validation with reapplied tombstones | A4.4, D8 approved | I-S4-AN-05 |
| A4.6 | Hardening: ownership in API/SSE/tools/jobs; `no-store`; `429/503` rate limits | A4.4, J4.2 | I-S4-AN-06 |
| A4.7 | Final G3 tests + orphan sweep (chunks/embeddings without a class) | A4.4 | I-S4-AN-07 |
| A4.8 | Demo rehearsal support | — | — (support: evidenced with E-S4-DA-04) |

### S4 backlog — Infra/Integrations (`J4.x`)

| Ticket | Task (summary) | Depends on | Tests |
|---|---|---|---|
| J4.1 | Opt-in Gmail `gmail.send`; escaped HTML+text templates; verified recipient | D7-MAIL; credentials | U-S4-JF-03, P-S4-JF-07 |
| J4.2 | ARQ scheduler with a single role; atomic claiming; 15 min SLA | A4.1 | I-S4-JF-04 |
| J4.3 | Escalating reminders T-48/T-24/T-2 h; T-3d/T-1d all-day local time | J4.2 | U-S4-JF-01 |
| J4.4 | Persistent dedupe + DST (nonexistent/repeated hour) | J4.2 | U-S4-JF-02 |
| J4.5 | `delivery_unknown` without blind retries; manual reshipping warns about duplicates | J4.2 | I-S4-JF-05 |
| J4.6 | Daily 07:00 and weekly digests by local date, with conflicts | J4.2 | I-S4-JF-06 |
| J4.7 | Reference deployment; versioned images; migration as a single job; rollback | J1.1 | L-S4-JF-08 |
| J4.8 | Alerts/budget + runbooks (cleanup, provider down, tokens, restore) | J4.7, S4.1, A4.5 | C-S4-JF-10 |
| J4.9 | G8 closure: builds/tests for the 3 apps, restore, reminders within SLA | all | E-S4-JF-09 |
| J4.10 | Observed real Gmail send with later cleanup | J4.1; authorized account | P-S4-JF-07 |

### S4 backlog — Ingestion/ASR (`S4.x`)

| Ticket | Task (summary) | Depends on | Tests |
|---|---|---|---|
| S4.1 | Cleanup/recovery runbook executable as written | S2.6, S2.7 | I-S4-SG-03 |
| S4.2 | Supervisor hardening: 60 s sweeper; failed cleanup blocks admission | S2.7 | L-S4-SG-01 |
| S4.3 | G1 re-verification under load with injected failures | S2.x | L-S4-SG-01 |
| S4.4 | Effective TTLs (10/30/60 min) published with a benchmark | S2.1 | L-S4-SG-02 |
| S4.5 | Table of real costs per class hour | S3.5 | C-S4-SG-04 |
| S4.6 | Demo rehearsal support | — | — (support: evidenced with E-S4-DA-04) |

### S4 backlog — RAG (`L4.x`)

| Ticket | Task (summary) | Depends on | Tests |
|---|---|---|---|
| L4.1 | G4/G6 closure with final numbers and hard slices | L2.7, L3.7 | R-S4-JL-01 |
| L4.2 | Cross security review (isolation, evidence, tools, secrets) | — | C-S4-JL-02 |
| L4.3 | PLAN_IMPLEMENTACION §9 checklist with item-by-item evidence | — | C-S4-JL-03 |
| L4.4 | Spec + plan + design updated in the same revision if a contract changed | — | C-S4-JL-03 |
| L4.5 | Demo go/no-go and full script | — | E-S4-DA-04 |
| L4.6 | Final retro + prioritized F4 backlog | — | C-S4-JL-04 |

### S4 backlog — Web/UX (`D4.x`)

| Ticket | Task (summary) | Depends on | Tests |
|---|---|---|---|
| D4.1 | Notification preferences in `/settings` | A4.2 | C-S4-DA-01 |
| D4.2 | Deliveries panel: `delivery_unknown` explained; reshipping warns | J4.5 | C-S4-DA-02 |
| D4.3 | Global Ctrl+K against `GET /search` | L2.4 | C-S4-DA-03 |
| D4.4 | Per-subject statistics without exposing audio | A2.9 | C-S4-DA-06 |
| D4.5 | Optional PWA: service worker excluding upload/API/transcripts/chat/SSE | D4.6 | C-S4-DA-05 |
| D4.6 | Polish: empty states, actionable errors, basic accessibility | D4.1, D4.2, D4.3 | C-S4-DA-05 |
| D4.7 | Demo rehearsal ×2 (Thursday and Friday) | L4.5 | E-S4-DA-04 |
| D4.8 | G1 checklist on the final client (no private caching) | D2.8 | C-S4-DA-05 |

### Definition of Done (S4 exit gate)

**Functional:** observed real test send with the authorized account; dedupe/DST/uncertain
delivery tested; full dashboard + Ctrl+K; final demo running on the reference deployment.

**Green tests (PLAN_TESTS §4):** full suite for the 3 apps; load `L-S4-*`
(G1 under load, TTLs); provider `P-S4-JF-07` (controlled real send); G8 closed with
evidence.

**Documentation:** published runbooks; cost table; final quality report;
§9 checklist marked; consistent spec/plan; logbooks closed with evidence.

## 6. Interfaces frozen per week

Changing anything in this table during the week requires the leader's approval and updating
consumers the same day.

| Week | Frozen |
|---|---|
| S1 | Session/cookie and CSRF; `POST /audios` + binary `PUT`; outbox shape; ingestion states; progress SSE |
| S2 | Chunks/embeddings; chat SSE events; citation format `[Subject · Class · mm:ss]`; `/search` |
| S3 | `tasks` + revisions; `calendar_events`; read-only chat tools; prompts v1 |
| S4 | Preferences/notifications; reminder dedupe; final UI routes |

### 6a. Dependency topology (per area)

The flow is acyclic: the provider of an interface freezes it on Monday and delivers code no
later than day 3; the consumer builds against the frozen contract.

```text
API/DB (session, schema, endpoints) ──> Infra (compose, workers) ──> everyone (environment)
API/DB (schema/endpoints) ──> RAG (index/chat), Web (UI), Infra (workers)
Ingestion (limits/transcript) ──> API/DB (capabilities), RAG (chunking), Web (states)
RAG (prompts/citations/tools) ──> Web (UI), API/DB (tasks), Infra (jobs)
Infra (outbox/jobs/runner) ──> RAG (analyze), API/DB (outbox consumption), Web (sync states)
```

Apparent crossings resolved (the pattern: the frozen **contract** first, then the code):

| Apparent crossing | Why it is NOT a cycle |
|---|---|
| API/DB needs the outbox (Infra) and Infra needs tables (API/DB) | API/DB defines the outbox shape on S1 Monday; the table is created in S1; Infra implements the dispatcher in S2. |
| RAG needs `tasks` (API/DB) and API/DB validates RAG spans | RAG delivers the Pydantic schema + validators on day 1–2 (runs without the DB); API/DB builds the table and later runs that validation (A3.6). |
| Web needs endpoints (API/DB) and API/DB tests E2E (Web) | Web builds against the frozen contract with a local mock; the crossed E2E belongs to Wednesday. |
| Infra needs the `analyze` job (RAG) and RAG needs the runner (Infra) | RAG delivers the function; Infra only wraps it in the worker on top of the generic idempotent S2 runner. |
| Ingestion needs an image with ffmpeg (Infra) and Infra needs spike data (Ingestion) | Infra delivers the base image on day 2; the measured limits feed capabilities in S2, not S1. |

### 6b. What is NOT a dependency within the same week

- **API/DB** does not wait for Ingestion in S1: the binary `PUT` is a skeleton; real
  reception arrives in S2 on the frozen contract.
- **Ingestion** does not wait for API/DB in S2: it consumes the frozen shape and uses its
  F0.2 prototype.
- **RAG** does not wait for Ingestion in S2: chunking/index are developed with synthetic/
  drafted transcripts; real transcripts only feed the final measurement.
- **Web** waits for nobody: local mock against frozen contracts.
- **Infra** does not wait for the RAG index in S2: `index(audio_id)` is tested with a stub that
  records the call; the real logic is wired on day 4–5.
- **Nobody** waits for Google/NVIDIA: tickets with a paid provider (`P-*`) use mocks to
  develop and the frozen contract to switch.

## 7. Weekly routines

- **Monday (45 min, whole team):** sprint planning, interface freezing, and
  **ticket claiming** via PR in `sprints/CLAIMS.md` (the logbook only collects later
  evidence).
- **Daily (15 min):** done/yesterday, today, blockers; the lead unblocks.
- **Wednesday (30 min):** cross integration — everyone tests someone else's work in their
  environment; switching from mocks to real code.
- **Friday (45 min):** demo, sprint DoD verification, retro, and logbook closure.
- **Anti-blocking rule:** whoever waits on someone else's code keeps building on the frozen
  contract's mock and reports it in the daily; the leader decides between accepting the mock
  for the demo or reassigning tickets.
- **Definition of done per ticket:** code + tests + updated document/contract +
  demonstrable in local compose + evidence in the logbook.

## 8. Risk management

| Risk | Early signal | Mitigation |
|---|---|---|
| Riva does not deliver reliable timestamps | S1 spike with `none` | Decide D6 **in S1**: textual product accepted or ephemeral alignment; the UI never invents minutes |
| Memory/3 h limit does not fit | S1 benchmark | Lower effective limits in capabilities with the leader's approval; never introduce disk |
| Provider retention without guarantees (D3) | S1 contract review | Blocks real classes; E2E with in-RAM synthetic audio; decide with the user |
| Ticket without an owner at Monday's close | Backlog with gaps | The leader reassigns, splits, or lowers the DoD scope explicitly and visibly |
| One person overloaded | Tickets not moving in the daily | Redistribution on day 2–3; a ticket with a `P-*` test requires verified eligibility in `CLAIMS.md` |
| Scope creep in F2/F3 | Tickets crossing folders | The leader rejects PRs outside claiming; new features → F4 backlog |
| Landing without a current design | D1.8 without visual direction | Decide the minimal direction (tokens + hero) before claiming; the previous front-end proposal was discarded and is not binding |
| Google Testing expires tokens after 7 days | Frequent reconnect in S3 | Test reconnection from S3; record limits in docs |
| A member drops out | — | Areas have natural pairs (API/DB↔Ingestion, Infra↔Web, RAG covers decisions); the leader redistributes the backlog |

## 9. Non-negotiable rules (from the specification)

1. Zero durable audio: nothing on disk, S3, Redis, logs, traces, caches, or backups. Losing
   audio = reupload.
2. Mandatory language chosen before uploading; no `multi`, no translation, no silent
   fallback.
3. Writing to Calendar only with explicit human confirmation; the chat never writes or
   sends email.
4. Identity always from the backend session, never from a payload or the LLM; everything
   filtered by `user_id`.
5. Citations only to IDs validated in the DB; the LLM does not generate timestamps or
   sources.
6. `NVIDIA_API_KEY` and tokens only in the backend; never in the frontend, logs, or
   commits.

## 10. Links

- [ESPECIFICACION.md](ESPECIFICACION.md) — M1–M9 contracts, data model, NFRs.
- [PLAN_IMPLEMENTACION.md](PLAN_IMPLEMENTACION.md) — gates G1–G8, decisions D1–D8, test layers.
- [PLAN_TESTS.md](PLAN_TESTS.md) — test catalog (unit, integration, provider, E2E, load) per sprint.
- [DISENO_BD_API_SPRINTS.md](DISENO_BD_API_SPRINTS.md) — DB/API design and suggested sequence per area.
- [plan/](plan/S1.md) — detailed per-sprint specification: [S1](plan/S1.md) ·
  [S2](plan/S2.md) · [S3](plan/S3.md) · [S4](plan/S4.md).
- [NVIDIA_RIVA.md](NVIDIA_RIVA.md) — audited connection parameters for the S1 spike.
- [PROPUESTA_PROYECTO_FINAL.md](PROPUESTA_PROYECTO_FINAL.md) — vision and roles.
- Logbooks: [sprints/README.md](sprints/README.md) — claiming rules and template ·
  [sprints/jose-leonardo.md](sprints/jose-leonardo.md) · [sprints/andres.md](sprints/andres.md) ·
  [sprints/santiago.md](sprints/santiago.md) · [sprints/jefferson.md](sprints/jefferson.md) ·
  [sprints/daniel.md](sprints/daniel.md).

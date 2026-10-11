# Maulwurf — Sprint Test Catalog

> Status: operational plan pending execution, revision 2026-09-19. Subordinate to
> [ESPECIFICACION.md](ESPECIFICACION.md) (contracts M1–M9, §6 data model) and
> [PLAN_IMPLEMENTACION.md](PLAN_IMPLEMENTACION.md) (§7 test layers and G1–G8 gate variants).
> Each ticket in the [PLAN_SPRINTS.md](PLAN_SPRINTS.md) backlog references here the tests that
> evidence it; whoever claims a ticket records its execution in their logbook at
> [sprints/](sprints/README.md). **No test exists yet:** this document defines what must exist
> and when.

---

## 0. Conventions

### 0.1 Layers (from PLAN_IMPLEMENTACION §7)

| Layer | Code | What it validates | Where it runs | Tool |
|---|---|---|---|---|
| Unit | `U-` | Pure logic: dates/DST, Unicode offsets, chunking, RRF, states, dedupe | CI (every MR) | pytest / Vitest |
| Local integration | `I-` | Real compose services: Postgres/pgvector, Redis, ffmpeg, outbox, leases, cleanup, restarts | CI (MR) + local | pytest against compose |
| Provider-contract | `P-` | Real contracts of NVIDIA Riva, LLM/embeddings, Google | **Protected**: manual or scheduled, never in MR | dedicated scripts |
| E2E | `E-` | Browser → proxy → services → DB/provider | CI (smoke) + manual demo | Vitest/Playwright + manual |
| Load | `L-` | Memory, tmpfs, latency, concurrency, absence of audio persistence | Local/protected, before closures | dedicated scripts |
| Component/manual | `C-` | Isolated components (Vitest) or annotated manual checklist (demo, security, client privacy) | CI (components) + manual | Vitest + checklist |
| Prompt regression | `R-` | Validators/aggregation against recorded LLM outputs (no keys or PII); spends no quota | CI (every MR) | pytest |

### 0.2 Identifiers

`<layer>-<sprint>-<legacy suffix>-<n>`, e.g. `I-S2-AN-05` = integration, sprint 2,
stable suffix `AN`, case 03. The **JL/AN/SG/JF/DA** suffixes are part of legacy IDs already
referenced: **they do not express ownership, assignment or current responsibility and are not
renamed**. Responsibility comes only from the ticket claim, recorded in
[sprints/CLAIMS.md](sprints/CLAIMS.md); the logbook only documents the subsequent evidence.

### 0.3 Location (per §3.3 of the specification)

- Python: `apps/api/tests/unit/`, `apps/api/tests/integration/`, `apps/ingest/tests/…`,
  `workers/tests/…` — pytest, markers `@pytest.mark.integration` / `provider` / `load`.
- Web: `apps/web/tests/unit/`, `apps/web/tests/e2e/` — Vitest (+ Playwright once E2E exists).
- Load and provider scripts: `scripts/load/`, `scripts/provider/` (no embedded secrets).

### 0.4 Fixture rules (non-negotiable, from AGENTS.md and the spec)

1. **Synthetic audio in RAM only**, generated at execution time; never versioned nor retained
   as a CI artifact. Only non-sensitive metrics and expected text are persisted.
2. Text fixtures: **synthetic or authored transcripts** (nobody's real voice, no PII).
3. Forbidden: API keys, tokens, raw provider payloads, DB dumps, audio, in fixtures, snapshots,
   test logs or CI recordings.
4. Provider-contract uses **authorized protected accounts + documented budget**; it creates only
   test resources and cleans them up. A mock **is not evidence** of a provider contract.
5. Date/DST tests use **injectable clocks**, never system time.
6. Suite decision rule: if a test needs the network, it is `I-` or `P-`; if it needs a paid
   provider, it is `P-`; if it needs a browser, it is `E-`; everything else is `U-`.

### 0.5 Naming

`test_<module>_<behavior>.py` / `<module>.<behavior>.test.ts`. One test, one composed assertion
at most. Every `I-`/`P-`/`E-` test has a docstring line stating **which gate or contract it
evidences** (G1, G2-T/G2-X, G3, G4, G5-F1/G5-F2, G6, G7-CAL/G7-MAIL, G8, M1–M9 or the frozen
interface §6 of [PLAN_SPRINTS.md](PLAN_SPRINTS.md)).

### 0.6 Claiming and minimum criterion per ticket

This catalog is organized by area/capability, not by person. When a table needs to indicate
responsibility, `Area/Claimed owner` refers to the current claiming and its evidence in
[sprints/](sprints/README.md); the legacy ID suffix never decides that responsibility.

Documentation tickets can be closed with `C-` evidence or a checklist that is defined, executed
and recorded with date, environment and result. **No functional ticket may be left without at
least one traceable test from this catalog**; a document or a checklist does not replace the
validation of a feature, infrastructure or provider contract.

### 0.7 Traceability added for previously orphaned tests

These associations complete the backlog references without changing the stable IDs:

| Test | Natural tickets |
|---|---|
| U-S1-SG-03 | S1.A6, S2.3 |
| P-S2-SG-12 | S2.4 |
| E-S2-JL-09 | L2.6, D2.7 |
| U-S3-JL-05 | L3.6, A3.5 |
| I-S3-JF-06 | J3.2, J3.3 |
| E-S3-DA-05 | D3.3, J3.4 |
| U-S4-JF-03 | J4.1 |

---

## 1. Sprint 1 (S1) — Foundation + F0 spike

### 1.1 Ingestion/ASR — spike and ingestion prototype

| ID | Level | What it tests | Contract/gate |
|---|---|---|---|
| U-S1-SG-01 | U | In-RAM synthetic audio generator produces known expected text (intelligible voice, not tones); deterministic with a seed | F0.1, fixture rule |
| U-S1-SG-02 | U | Format validator: accepts WAV PCM s16 mono; rejection decision table (playlist, URL, unexpected container, multi-channel) | M2 |
| U-S1-SG-03 | U | Boundary math: global timestamp = fragment offset + local time, without duplicating overlap | M2/§6 |
| I-S1-SG-04 | I | Complete failure matrix: success, incomplete upload, rejection, timeout, cancellation, ffmpeg/Riva/DB error, SIGKILL, container and host restart; injected failure during and after the commit | F0.2, G1 |
| I-S1-SG-05 | I | Lease expiration does **not** mark verified cleanup; without cleanup evidence the outbox stays blocked | F0.2, G1 |
| I-S1-SG-06 | I | Absence of original/converted/fragments on disk, Redis, logs, traces and caches; inspection of descriptors/processes/mounts, not only file names | G1 |
| P-S1-SG-07 | P | NVIDIA contract spike: enabled languages, formats, sample rate, payload/duration limits, quotas, deadlines, offset precision (`word/segment/none`) | F0.1, D2/D4/D6 |
| P-S1-SG-08 | P | Latency per duration/language and SDK memory within the authorized budget | F0.1, G8 |

**Closed-suite criterion:** P-S1-SG-07 produces the contract report; without it, S2 does not start.

### 1.2 API/data — authentication and core schema

| ID | Level | What it tests | Contract/gate |
|---|---|---|---|
| U-S1-AN-01 | U | Session identifier hash and revocation/expiration; cookie with `Secure/HttpOnly/SameSite=Lax` flags issued correctly | M1 |
| U-S1-AN-02 | U | CSRF bootstrap and rotation: `GET /me` with `no-store` delivers the plain token whose hash the session stores; every mutation, including the `PUT`, requires token and same-origin Origin; creating/rotating a session invalidates the previous token and logout/revocation voids it | M1 |
| U-S1-AN-03 | U | AES-GCM encryption of Google tokens: unique nonce per encryption, key version recorded, nothing in plaintext in the DB | M1/§7 |
| I-S1-AN-04 | I | Alembic migrations: from empty and from the previous version, with a connection pool | G3 |
| I-S1-AN-05 | I | Isolation: user B does not see A's resources, not even by guessing IDs (subjects, sessions, credentials) | G3 |
| I-S1-AN-06 | I | Google OAuth with a test Google or local double: `state`, PKCE, `nonce`/issuer/audience; identity from `sub`, not email | M1, D1 |
| I-S1-AN-07 | I | Subject CRUD: deleting a subject with active classes requires moving them or confirming | M1 |

### 1.3 Knowledge contracts — preparation for S2

| ID | Level | What it tests | Contract/gate |
|---|---|---|---|
| U-S1-JL-01 | U | Pydantic draft §M4: `due_date`/`due_at` mutually exclusive, nullable `confidence_score` in [0,1], `date_status` enum | M4 |
| U-S1-JL-02 | U | Pydantic draft §M6: chunk with valid Unicode offsets `[start, end)` and links to `chunk_segments` | M6 |
| U-S1-JL-03 | U | G4 dataset: format validator (question, answerable yes/no, expected evidence); 15 mandatory non-answerables per language | G4 |

### 1.4 Infrastructure/CI — operating base

| ID | Level | What it tests | Contract/gate |
|---|---|---|---|
| I-S1-JF-01 | I | Compose smoke: 8 services up; `/healthz` per process; API `/readyz` (Postgres/Redis) and ingest `/readyz` (DB, safe temporaries, capacity) **without** invoking paid providers | G8 |
| I-S1-JF-02 | I | Proxy no-buffering: large file upload via Caddy while verifying that no file appears on any filesystem | G1 |
| I-S1-JF-03 | I | Redis contains only IDs/status; no key with a payload above the documented threshold | G1 |
| U-S1-JF-04 | U | Outbox policy by type: `index_requested`/`analyze_requested` are blocked by `cleanup_pending` until verified cleanup; Calendar, notifications and deletions are born enabled in their own transaction after validating authorization, tombstone and version | M3/§6/G5-F1/G5-F2 |

### 1.5 Web/UX — client and base components

| ID | Level | What it tests | Contract/gate |
|---|---|---|---|
| U-S1-DA-01 | U | HTTP client: obtains the CSRF via the non-cacheable `GET /me`, injects it into every mutation and replaces the value when the session rotates; interceptors 401→login, 403→message, 429→deferred retry | M1/M9 |
| U-S1-DA-02 | U | Subject form: required-field validation and rendered API errors | M1 |
| C-S1-DA-03 | C | Base components: status badges and empty states show the values of the status contract | M9 |

---

## 2. Sprint 2 (S2) — Knowledge: F1 vertical slice

### 2.1 Ingestion/ASR — real pipeline

| ID | Level | What it tests | Contract/gate |
|---|---|---|---|
| U-S2-SG-01 | U | `/ingestion/capabilities` reflects the limits measured in the spike (no hardcoded constants) | D2/D4 |
| U-S2-SG-02 | U | ffprobe validation: complete decision table (container, codecs, duration, channels; playlists/URLs/malformed rejected) | M2 |
| U-S2-SG-03 | U | ffmpeg invocation: argv built without shell interpolation; CPU/memory/output/time limits present | M2/security |
| U-S2-SG-04 | U | Boundary reconciliation: words/text duplicated by the overlap removed; stable order; correct global offsets | M2 |
| U-S2-SG-05 | U | Retry classification: transient gRPC retries (max 3, backoff+jitter); auth/language/invalid format does **not** retry | M2 |
| I-S2-SG-06 | I | Atomic transcript+segments commit in one transaction; `not_requested` ≠ success; empty response = result without usable text, not filler | M2/§6 |
| I-S2-SG-07 | I | Cleanup in `finally` on success and on every S1 error; `cleanup_status=pending\|verified\|failed` kept separate from the ASR; the normal path does not reach `succeeded` without verification and a failure stops admission and blocks the text outbox | G1/G5-F1 |
| I-S2-SG-08 | I | Post-commit reconciler: a process dead after the commit leaves `transcript_committed_cleanup_pending`; only verification on the same host/namespace enables the outbox **without retranscribing** | F0.2/G1/G5-F1 |
| I-S2-SG-09 | I | Fencing: an expired owner does not publish the transcript (stale token rejected) | M2 |
| I-S2-SG-10 | I | Cancellation: disconnection during reception cancels+cleans; after 202 it continues under lease; closing the tab does not cancel; cancelling after commit → 409 | M2 |
| E-S2-SG-11 | E | 1 h synthetic class (generated in RAM) transcribed end to end via the proxy; demonstrates `word\|segment` for G2-T or `none` with the temporal UI/SRT disabled for G2-X | G1/G2-T or G2-X/F1 |
| P-S2-SG-12 | P | Minimal regression against real Riva after blocking client changes: contract, cancellation, deadlines and error classification with a fixed budget and protected suite | F0.1/G2-T or G2-X |

### 2.2 API/data — M2 schema and endpoints

| ID | Level | What it tests | Contract/gate |
|---|---|---|---|
| U-S2-AN-01 | U | Validation before bytes: mandatory language in the allowlist, no `multi`; consent with the current `privacy_notice_version`, cloud acceptance and third-party voice acknowledgment; missing/stale version rejects before reserving capacity | M2/rules 2 and 7 |
| U-S2-AN-02 | U | Dedupe is decided at the `PUT` by hash+language+subject+date+zone; exact returns the canonical after cleanup; variant returns a short opaque single-use token bound to tenant/hash/metadata/class and requires a second `POST` + reupload | M2 |
| U-S2-AN-03 | U | ASR states `awaiting_upload→receiving→transcribing→transcript_committed_cleanup_pending→succeeded` or typed finals; independent cleanup; analysis/index `not_requested\|pending\|running\|succeeded\|failed\|cancelled` and honest aggregation; invalid transitions rejected | M2/M3 |
| U-S2-AN-04 | U | Tombstone/fencing: token invalidated; late commit rejected | M2 |
| I-S2-AN-05 | I | Concurrent dedupe on `PUT`: simultaneous exacts converge on the canonical; variant cleans up before the 409, rejects an expired/reused/foreign token or a different hash on the second `PUT`, and **never retains audio** while awaiting a decision | M2/G3 |
| I-S2-AN-06 | I | `DELETE /audios/{id}` purges derivatives; deleting while ASR runs produces a clean cancellation without orphans | G3 |
| I-S2-AN-07 | I | SSE `GET /audios/{id}/progress`: reconnectable snapshot from the DB; owner only; shows real bytes and fragments | M2/G3 |
| I-S2-AN-08 | I | `retry` vs `reupload` matrix: retry only for analysis/index; reupload only without usable transcript; correct error messages in each case | M2 |
| I-S2-AN-09 | I | Export `transcript.txt` always; `transcript.srt` **only** with valid times; `?segment=` filters by owner | M2/M9 |
| I-S2-AN-10 | I | `index_requested`/`analyze_requested` outbox stays disabled until `cleanup_status=verified`; the gate is not blocked by an error in the Calendar, notification or deletion outbox created by their transactions | M3/G5-F1/G5-F2 |
| I-S2-AN-11 | I | M7 chat: conversation CRUD with all/subject/class modes isolated by tenant; idempotent `client_message_id`; stream cancellation leaves a `cancelled` partial; deleting a conversation purges sources | M7/G3 |

### 2.3 Retrieval/RAG — chunking, index and chat

| ID | Level | What it tests | Contract/gate |
|---|---|---|---|
| U-S2-JL-01 | U | Chunking: ~800 token target, ~100 overlap, cuts at sentence/segment boundaries; Unicode offsets preserved (emoji, accents, CJK); correct `chunk_segments` | M6 |
| U-S2-JL-02 | U | Embedding versioning: new index generation with atomic activation; never mixes model/dimension/version | M6/D5 |
| U-S2-JL-03 | U | RRF: deterministic fusion given the same input; documented `k`; tie-break by `chunk_id` (stable cursor); dedupe and diversity applied before the top-k | M6 |
| U-S2-JL-04 | U | `user_id`/version/tombstone filters applied **in both branches** (lexical and vector) before the top-k | G3 |
| U-S2-JL-05 | U | Citations: only towards IDs validated in the DB; without reliable offsets → "segment N"; the model never invents sources | M7/rule 5 |
| I-S2-JL-06 | I | Hybrid search against real Postgres+pgvector with a test corpus (authored text) | M6 |
| I-S2-JL-07 | I | SSE chat pipeline: `delta/citation/done/error` events in order; mid-stream cancellation cleans state | M7 |
| I-S2-JL-08 | I | `message_sources` contains **all** sources given to the model, not only the cited ones | M7 |
| E-S2-JL-09 | E | Question from the browser → answer with a clickable citation that opens the reader at the segment | G4/F1 |
| P-S2-JL-10 | P | Embedding/LLM provider: real contract, dimensions and quotas (protected) — feeds D5 | D5 |

**G4 criterion (weekly, reproducible script):** Recall@8 ≥ 0.85 (answerables, with tenant/version
filters active), citation precision
≥ 0.95, abstention ≥ 0.90; retrieval p95 ≤ 500 ms. Numbers per language with
numerator/denominator, versioned dataset with a frozen `test` partition, formula and adjudication
published per the PLAN_IMPLEMENTACION §7.1 protocol.

### 2.4 Durable work — outbox, workers and observability

| ID | Level | What it tests | Contract/gate |
|---|---|---|---|
| U-S2-JF-01 | U | Backoff with bounded jitter (max 3 transients); non-transient cases do not retry | M3 |
| I-S2-JF-02 | I | Idempotent `index(audio_id)` job: double delivery → a single index; no partially active versions | G5-F1 |
| I-S2-JF-03 | I | Redis `FLUSHALL` in the test environment → jobs regenerated from DB/outbox end to end | G5-F1 |
| I-S2-JF-04 | I | Reconciler: dead attempts marked, stuck outbox re-published, duplicates resolved by uniqueness/lease | G5-F1 |
| I-S2-JF-05 | I | Injected crash before and after the commit between ingestion and API: outcome per contract | G5-F1 |
| E-S2-JF-06 | E | Pipeline E2E smoke: upload → transcript → cleanup → index → question with citation | G4/G5-F1 |
| C-S2-JF-10 | C | Minimal observability: drafted alert for failed cleanup, failure panel per stage, and no sensitive payload in metrics | G1/G8 |

### 2.5 Web/UX — upload, library and chat

| ID | Level | What it tests | Contract/gate |
|---|---|---|---|
| U-S2-DA-01 | U | Form: language without preselection and current notice version, cloud acceptance and voice acknowledgment mandatory before creating a reservation or starting the `PUT`; a stale version error forces reviewing and accepting again | M2/M9 |
| U-S2-DA-02 | U | Cancelling the upload cancels the attempt (API call + releasing `File`/object URLs) | M9/G1 |
| U-S2-DA-03 | U | Progress: real bytes and completed/total fragments; **zero** invented percentages; optional polling fallback | M9 |
| C-S2-DA-04 | C | Reader: `?segment=` anchor highlights; timestamps only when valid; conditional export; empty summary if `not_requested` | M9 |
| C-S2-DA-05 | C | Citations `[Subject · Class · mm:ss]` → correct route; without times → "segment N"; sanitized Markdown | M7/M9 |
| E-S2-DA-06 | E | Browser E2E path of the week, including the `partial` state distinguishing the failed stage | F1 |
| C-S2-DA-07 | C | Global sweep: no audio player nor download button; no IndexedDB/Cache Storage with private data (`no-store` verified) | G1 on the client |

---

## 3. Sprint 3 (S3) — Action: extraction and Calendar (F2)

### 3.1 LLM analysis — prompts, validation and dates

| ID | Level | What it tests | Contract/gate |
|---|---|---|---|
| U-S3-JL-01 | U | Post-LLM validation: enums, ranges, segment membership in class/tenant/version; out-of-range Unicode span → rejection | M4/G6 |
| U-S3-JL-02 | U | Evidence: **literal** check of the quoted text against the segment reconstructed from the DB | M4/rule 5 |
| U-S3-JL-03 | U | Date resolution against the **class's** date/zone: DST (nonexistent time → next valid; repeated time → a single one), "next Friday", relative dates, ambiguous → `needs_review`, never an invented midnight | M4/DST |
| U-S3-JL-04 | U | Overlapping windows: task dedupe by identical evidence; `processing_runs` makes the analysis idempotent | M4 |
| U-S3-JL-05 | U | Reanalysis: publishes a new complete revision and creates `task_revisions` without overwriting edited/confirmed tasks nor activating partial results | M4/G5-F2 |
| R-S3-JL-06 | R | **Prompt regression v1**: recorded LLM outputs (no keys or PII) → validators and aggregation verified without spending quota; runs in CI | G6 |
| P-S3-JL-07 | P | Real G6 execution with the authorized provider (protected, budget) | G6 |

**G6 criterion:** ≥50 annotated activities per language (ambiguities/DST included): precision ≥ 0.95,
recall ≥ 0.85, exact date ≥ 0.95 on answerables, evidence validated 100 %. Applies the
§7.1 protocol: matching unit (type + normalized date + evidence), frozen `test` partition and
approval per language, not by global average.

### 3.2 API/data — tasks, evidence and revisions

| ID | Level | What it tests | Contract/gate |
|---|---|---|---|
| U-S3-AN-01 | U | `tasks` CHECKs: exclusive due constraint; the Calendar block uses exactly one instant or all-day pair, with end after start; `scheduled` is **not** a status and `calendar_sync_status` is kept separate | M4/M5/§6 |
| U-S3-AN-02 | U | Optimistic locking: PATCH with the wrong version → 409 | M4 |
| I-S3-AN-03 | I | Atomic confirmation promotes `revision_id` with `expected_version`, increments the version and creates one outbox; double confirmation does not duplicate; without the integration it stays confirmed + `blocked` | M4/G5-F2/G7-CAL |
| I-S3-AN-04 | I | Editing a confirmed task creates `task_revision(pending_confirmation)` without mutating active fields/event; stale base version → 409; reanalysis does not touch activated revisions nor edited/confirmed tasks | M4/G5-F2 |
| I-S3-AN-05 | I | Tenant-aware `task_sources` allows zero sources only for a manual task and one or several validated sources for extraction/chat; invalid membership or span is rejected and the evidence is reconstructible | M4/G6 |
| I-S3-AN-06 | I | Double analysis does not duplicate tasks/revisions by evidence; double confirmation returns 202 with a single outbox and `calendar_sync_status=pending` | M4/G5-F2 |

### 3.3 Text processing — analysis support

| ID | Level | What it tests | Contract/gate |
|---|---|---|---|
| I-S3-SG-01 | I | Backfill over F1 classes: only from persisted text, without reupload or retranscription | M4 |
| U-S3-SG-02 | U | Overlapping windows: bounded token cost and dedupe by evidence with real spike transcripts (textual, authored) | M4 |
| U-S3-SG-03 | U | Without ASR offsets: the analysis works with textual evidence; zero invented minutes | M4/D6 |
| I-S3-SG-04 | I | `processing_runs` per transcript/config version: reanalysis reprocesses only what is needed | M4 |
| U-S3-SG-05 | U | Analysis hardening: bounded backoff+jitter (max 3 transients), non-transient errors without retry; ASR quality warnings (low confidence, empty segments) flagged for the inbox | M4/G6 |

### 3.4 Integration — Google Calendar

| ID | Level | What it tests | Contract/gate |
|---|---|---|---|
| P-S3-JF-01 | P | Real scopes (D7-CAL) with a protected account: what `calendar.app.created` + `calendar.events.readonly` + `calendar.calendarlist.readonly` allow; findings log; **do not assume** | D7-CAL/G7-CAL |
| P-S3-JF-02 | P | Insert with timeout → look up the deterministic ID; 409 → verify identity and reconcile; `Retry-After` respected; `invalid_grant` disconnects (do not retry) | G7-CAL |
| I-S3-JF-03 | I | Sync idempotency: double click / lost response / concurrent retry → **exactly one event** | G5-F2/G7-CAL |
| I-S3-JF-04 | I | Patch with ETag: an external conflict is not overwritten; completing/discarding cancels reminders but keeps the event | G7-CAL |
| I-S3-JF-05 | I | -7/+60 day read: full pagination, expanded recurrences, deletions; **atomic** publication into `calendar_events`; never mixing `syncToken` with filters | G7-CAL |
| I-S3-JF-06 | I | Missing scopes or `invalid_grant` leave sync `blocked` without breaking library/chat; on reconnect, only current blocked versions move atomically to `pending` and create/reuse outbox, never stale revisions/jobs | G5-F2/G7-CAL |
| I-S3-JF-07 | I | Conflicts: time overlap ≠ delivery pile-up; correct `GET /calendar/conflicts` | M5 |

### 3.5 Web/UX — review inbox and dashboard

| ID | Level | What it tests | Contract/gate |
|---|---|---|---|
| C-S3-DA-01 | C | Inbox: task statuses, `date_status` badge (resolved/ambiguous/missing), low ASR quality warning | M4/M9 |
| C-S3-DA-02 | C | Editing: due separate from the Calendar block; exclusive all-day/instant pairs, end after start, default duration visible/editable, and pending revision without mutating what is active | M4/M5/M9 |
| C-S3-DA-03 | C | Confirmation shows **what will be written** to Calendar before writing it; 409 refreshes without losing the edit | M4/M9 |
| C-S3-DA-04 | C | Chat `propose_task` creates a proposal in the inbox, **never** writes the calendar directly | rule 3 |
| E-S3-DA-05 | E | E2E with a test Google account: proposal → review/versioning → preview → confirmation → one visible event via an idempotent job | G5-F2/G6/G7-CAL |
| C-S3-DA-06 | C | Full class page: summary, topics, items, per-stage analysis/index statuses, visible backfill/reanalysis and quality warnings without exposing audio | M4/M9 |
| C-S3-DA-07 | C | Dashboard: does not draw the task and its linked event twice; `last_synced_at` and coverage visible | M5/M9 |
| C-S3-DA-08 | C | Per-task sync statuses (`pending/synced/failed/blocked`) and reconnection UI: on reconnect only current versions move to `pending` and the UI communicates the resumption without auto-confirming | M5/M9 |
| C-S3-JF-08 | C | Protected account: eligibility, budget and signed test-resource cleanup | D7-CAL/G7-CAL |

---

## 4. Sprint 4 (S4) — Reminders, closure and demo (F3)

### 4.1 API/data — notifications, deletion and G3 closure

| ID | Level | What it tests | Contract/gate |
|---|---|---|---|
| U-S4-AN-01 | U | `notifications` statuses (`pending\|sending\|sent\|failed\|delivery_unknown\|cancelled`) and valid transitions | M8 |
| U-S4-AN-02 | U | `dedupe_key` (user, type, task, due_version, occurrence) prevents duplicates by construction | M8 |
| U-S4-AN-03 | U | Changing due date/preferences cancels **only** stale future occurrences; never a retrospective burst | M8 |
| I-S4-AN-04 | I | `DELETE /me` returns 202 + `deletion_operation_id`; tombstone, blocked sessions/jobs and idempotent purge; the initiating session is restricted to `GET /me/deletion/{id}` and logout, the rest are revoked; remote deletion before revoking tokens; `GET /me/deletion/{id}` exposes `pending\|completed\|completed_with_remote_failures` and only the expired minimal residue | security/G3 |
| I-S4-AN-05 | I | Backup restore (D8): only encrypted text/metadata; tombstones reapplied; nothing deleted reappears | D8/G8 |
| I-S4-AN-06 | I | Basic rate limits: `429/503` with correct headers on sensitive endpoints; ownership in API/SSE/tools/jobs | G3/NFR |
| I-S4-AN-07 | I | Orphan sweep: zero chunks/embeddings/derived rows without a class after deletions | G3 |

### 4.2 Notifications/operations — Gmail, scheduler and deployment

| ID | Level | What it tests | Contract/gate |
|---|---|---|---|
| U-S4-JF-01 | U | T-48/T-24/T-2 h escalation (instants) and T-3d/T-1d at local time (all-day); undated tasks without reminders | M8 |
| U-S4-JF-02 | U | DST: nonexistent time → next valid instant; repeated time → a single notification | M8 |
| U-S4-JF-03 | U | HTML + escaped plain-text templates; fixed recipient to the verified email; no mail GET mutates state | M8/security |
| I-S4-JF-04 | I | Scheduler: single active role; atomic row claiming with multiple workers; target SLA 15 min | M8 |
| I-S4-JF-05 | I | `delivery_unknown`: lost response or expired lease → **no automatic retry**; `POST /notifications/{id}/resend` requires `acknowledge_duplicate_risk=true`, creates a new audited occurrence and does not set the original back to `pending` | M8/G7-MAIL |
| I-S4-JF-06 | I | Daily 07:00 configurable digest and weekly (Sunday) by local date, with the week's conflicts | M8 |
| P-S4-JF-07 | P | Real Gmail delivery observed end to end with an authorized account + subsequent cleanup (once, controlled) | G7-MAIL/G8 |
| L-S4-JF-08 | L | Restore verified in a clean environment; image rollback tested; Alembic migration as a single job | G8 |
| E-S4-JF-09 | E | Full G8: builds/tests of the 3 apps; reminders within the target with healthy dependencies | G8 |
| C-S4-JF-10 | C | Runbooks executed as written in the reference environment with authored evidence | G1/G8 |

### 4.3 Web/UX — final dashboard, Ctrl+K and client privacy

| ID | Level | What it tests | Contract/gate |
|---|---|---|---|
| C-S4-DA-01 | C | Notification preferences: Gmail opt-in, digest time, visible thresholds | M8/M9 |
| C-S4-DA-02 | C | Delivery panel: `delivery_unknown` explained; resending requires explicit acknowledgment of the risk and keeps the original delivery visible | M8/M9/G7-MAIL |
| C-S4-DA-03 | C | Ctrl+K: snippet + subject + class + valid timestamp; reachable from any screen | M6/M9 |
| E-S4-DA-04 | E | Full demo rehearsal ×2 (Thursday and Friday) with the versioned sprint script | demo |
| C-S4-DA-05 | C | G1 checklist on the final client: no private-data cache on any route; the service worker (if any) excludes upload/API/transcripts/chat/SSE | G1 |
| C-S4-DA-06 | C | Per-subject statistics: transcribed classes, original duration and covered topics from text metadata, without exposing audio | M9 |

### 4.4 Ingestion/ASR — load and final hardening

| ID | Level | What it tests | Contract/gate |
|---|---|---|---|
| L-S4-SG-01 | L | G1 re-verification under load: concurrent uploads + injected failures → zero persistence, zero orphaned temporaries, RAM within the per-slot reservation | G1 |
| L-S4-SG-02 | L | TTL benchmark (proposed 10/30/60 min) published in capabilities with backing data | M2 |
| I-S4-SG-03 | I | Runbook executed as written: failed cleanup → admission stopped + alert; host loss → restoration per the document | G1/G8 |
| C-S4-SG-04 | C | Table of real costs per class hour (ASR, RAM, ffmpeg CPU) with reproducible measurements | G8 |

### 4.5 Quality/security — closure

| ID | Level | What it tests | Contract/gate |
|---|---|---|---|
| R-S4-JL-01 | R | Final G4 and G6 published per language with difficult slices documented | G4/G6 |
| C-S4-JL-02 | C | Security review executed: tenant isolation in SSE/tools/jobs, validated evidence, no writes without confirmation, secrets out of logs/traces | security |
| C-S4-JL-03 | C | PLAN_IMPLEMENTACION §9 checklist checked item by item with evidence | closure |
| C-S4-JL-04 | C | Final retro executed and F4 backlog prioritized and recorded (minutes or linked document) | closure |

---

## 5. Gate coverage and variants G1–G8

| Gate | Brief definition | Planned main evidence | Area/Claimed owner |
|---|---|---|---|
| G1 | No audio persistence + F0/F1 recovery | I-S1-SG-04/05/06, I-S1-JF-02/03, I-S2-SG-06/07/08/09/10, E-S2-SG-11, C-S2-DA-07, L-S4-SG-01 | Ingestion and privacy / per claiming |
| G2-T | ASR with reliable, in-range time offsets | U-S1-SG-03, P-S1-SG-07, U-S2-SG-04, I-S2-SG-06, E-S2-SG-11, I-S2-AN-09 | Temporal ASR contract / per claiming |
| G2-X | Approved textual product, no invented times nor temporal SRT/links | P-S1-SG-07, I-S2-SG-06, I-S2-AN-09, U-S2-JL-05, C-S2-DA-04/05, E-S2-SG-11 | Textual ASR contract / per claiming |
| G3 | Complete tenant isolation | I-S1-AN-05, U-S2-JL-04, I-S2-AN-05/06/07, I-S4-AN-04/06/07 | API, data and security / per claiming |
| G4 | RAG quality (published metrics) | U-S2-JL-01/03/04/05, I-S2-JL-06/07/08, E-S2-JL-09, R-S4-JL-01 | Retrieval/RAG / per claiming |
| G5-F1 | Durable recovery of transcript, index and chat | U-S1-JF-04, I-S2-AN-10, I-S2-JF-02/03/04/05, E-S2-JF-06 | Durable work F1 / per claiming |
| G5-F2 | Durable recovery of analysis, revisions and Calendar outbox | U-S3-JL-05, I-S3-AN-03/04/06, I-S3-JF-03/06, E-S3-DA-05 | Durable work F2 / per claiming |
| G6 | Extraction with validated evidence | U-S3-JL-01/02/03/05, U-S3-SG-05, R-S3-JL-06, P-S3-JL-07, I-S3-AN-05 | Analysis/evidence / per claiming |
| G7-CAL | Idempotent Calendar, reconciliation, scopes and reconnect | I-S3-AN-03, P-S3-JF-01/02, I-S3-JF-03/04/05/06, E-S3-DA-05, C-S3-DA-08 | Calendar integration / per claiming |
| G7-MAIL | Gmail opt-in, uncertain delivery and warned manual resend | U-S4-JF-03, I-S4-JF-04/05, P-S4-JF-07, C-S4-DA-01/02 | Gmail/notifications / per claiming |
| G8 | Delivery: builds, restore, deployment, costs | I-S1-JF-01, P-S1-SG-08, L-S4-JF-08, E-S4-JF-09, C-S4-SG-04, I-S4-AN-05 | Operations/delivery / per claiming |

---

## 6. Weekly execution (what runs where)

| Moment | What runs | Area/Claimed owner |
|---|---|---|
| Every commit/MR | `U-` + `I-` + `E-` smoke + prompt regression `R-` (with recorded fixtures) | CI/infrastructure / per claiming |
| Before each Friday demo | Full local suite in compose + manual checklist of the week | Ticket areas / claimed owners |
| Protected manual, with budget | `P-` (Riva, LLM/embeddings, Google) — findings recorded in `docs/` | Affected integration / claimed owner |
| Before week/project closures | `L-` (load, RAM/tmpfs, G1 under load) | Ingestion and infrastructure / per claiming |
| Weekly | Reproducible G4/G6 metrics published with numerator/denominator | Retrieval and analysis / per claiming |

> Honest rule: a mock validates logic, not contracts. No G gate is closed with mocks as the only
> evidence; `P-` tests require an authorized protected account and budget, and their findings
> are documented (observed capabilities and limits, not assumptions).


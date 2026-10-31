# Canonical claims registry — Maulwurf

> Status: **S2 open**. Review: 2026-10-09.
>
> This file is the single source of truth for ticket ownership. Personal logbooks keep
> evidence, blockers, and notes, but a row there does not assign a ticket by itself. Rows
> pre-filled before this review are historical candidates and **are not valid claims**
> until they appear here.

## 1. Usage rules

1. Only the active sprint's section opens after verifying the previous gate, the measured
   capacity, and the team's availability. S2–S4 are not claimed in advance.
2. A claim is accepted through a single planning PR that adds one complete row. The
   leader's or the team-designated person's review resolves editing conflicts.
3. `Owner` is the only person responsible for the ticket. `Contributors` and `Reviewers`
   may collaborate, but they do not share ownership or closure.
4. If the ticket has a `P-*` test or a real call to Riva, LLM/embeddings, Google, or
   Gmail, `Cloud eligibility` must state `verified` with date and reviewer, no secrets.
5. When transferring a ticket, the historical row is kept and another one is added with
   `action = transfer`. When splitting it, first update the backlog with approved child
   IDs; parts are not claimed with informal IDs.
6. A `continuous` ticket uses the operating status `in progress` during the sprint and must
   close as `done`, `blocked`, or `transferred` with weekly evidence. `continuous` is not a
   state.

## 2. Allowed states and actions

| Field | Values |
|---|---|
| `Action` | `claim`, `transfer`, `unclaim`, `split` |
| `Status` | `pending`, `in progress`, `blocked`, `done`, `transferred`, `cancelled` |
| `Cloud eligibility` | `not applicable`, `pending`, `verified` |

An `unclaim` is only valid before work starts or if it records the evidence handover.
A `split` references the PR that created the child IDs. No action removes history.

These six values are **all** the status vocabulary: there are no `claimed`, `in_progress`,
`in_review`, or `continuous` statuses. `continuous` describes the bounded nature of a ticket
(see rule 6) and the table's actions are recorded as dated events.

## 3. Active sprint

**S2 is open.** S1 has every implementation ticket `done`; only L1.7 (coordination and the
formal S1 close) is unclaimed. S3 and S4 remain closed. D2 and D6
remain `pending`; D3-Audio and D4 remain `blocked`.

### S1 — Foundation + F0 spike

> Logbooks keep the "Claimed tickets" section as history. Only this table's rows are
> valid claims; when accepting a claim, the PR links the logbook evidence but does not
> replace it.

| Action | Ticket | Owner | Status | Claimed at (UTC) | Cloud eligibility | Contributors / reviewers | Reason, evidence, or PR |
|---|---|---|---|---|---|---|---|
| claim | L1.1 | satnovaOG | done | 2026-09-23 | not applicable | — / lead | Pins and lockfiles; merged in #7 |
| claim | L1.2 | satnovaOG | done | 2026-09-23 | not applicable | — / lead | ADRs 0001–0004; merged in #7 |
| claim | L1.3 | satnovaOG | done | 2026-09-23 | not applicable | — / lead | `docs/spike/F0.1-protocolo.md`; merged in #7 |
| claim | S1.A1 | satnovaOG | done | 2026-09-23 | not applicable | — / lead | Synthetic generator in RAM; merged in #6 |
| claim | S1.A2 | satnovaOG | done | 2026-09-24 | pending | — / lead | Observed gRPC call `OK`; server `NO VERIFICADO`. Cloud without a reviewer. |
| claim | S1.A3 | satnovaOG | done | 2026-09-24 | pending | — / lead | Proposed allowlist `es`, `en`, `fr`. Cloud without a reviewer. |
| claim | S1.A4 | satnovaOG | done | 2026-09-24 | pending | — / lead | WAV `accept`; mp3/ogg/opus/flac/webm `normalize`. m4a `NO VERIFICADO`. 16 kHz `OK`, not approved. Cloud without a reviewer. |
| claim | S1.A5 | satnovaOG | done | 2026-09-24 | pending | — / lead | Three calls: local wait `DEADLINE_EXCEEDED`, cancellation `CANCELLED`, 30 s floor `OK`. 200 MiB and 3 h not approved. Quota, concurrency and maxima `NO VERIFICADO`. Cloud without a reviewer. |
| claim | S1.A6 | satnovaOG | done | 2026-09-27 | pending | — / lead | Offsets: gRPC `OK` with no words nor segments. D6 proposal `none`, status `pending`. Cloud without a reviewer. |
| claim | S1.A7 | satnovaOG | done | 2026-09-27 | pending | — / lead | `es`/`en` latency and PCM peak ~330 MiB. Cost `NO VERIFICADO`. 200 MiB and 3 h not approved. Cloud without a reviewer. |
| claim | S1.A8 | satnovaOG | done | 2026-09-27 | pending | — / lead | Report `docs/spike/F0.1-informe-riva.md`. Audio retention `NO VERIFICADO`. Cloud without a reviewer. |
| claim | S1.A9 | satnovaOG | done | 2026-09-27 | pending | — / lead | Decision record: D2 and D6 `pending`; D3-Audio and D4 `blocked`. Nothing `approved`. Cloud without a reviewer. |
| claim | J1.8 | jpinillaz | done | 2026-09-25 | not applicable | — / lead | `base` target with pinned ffmpeg and 7.1.5 vs 8.0.1 parity verified; merged in #12 |
| claim | J1.1 | jpinillaz | done | 2026-09-25 | not applicable | — / lead | 8-service compose; evidence in the logbook; merged in #13 |
| claim | J1.2 | jpinillaz | done | 2026-09-25 | not applicable | — / lead | Hardened ingest; evidence in the logbook; host pending operations; merged in #14 |
| claim | J1.3 | jpinillaz | done | 2026-09-25 | not applicable | — / lead | Caddy without buffering; SSE pending A1.8/S2; merged in #15 |
| claim | J1.4 | jpinillaz | done | 2026-09-25 | not applicable | — / lead | CI + PR template (ADR-0004); pipeline green; merged in #16 |
| claim | J1.6 | jpinillaz | done | 2026-09-25 | not applicable | — / lead | ADR-0005 and Redis policy; merged in #17 |
| claim | J1.7 | jpinillaz | done | 2026-09-25 | not applicable | — / lead | Development runbook; verified from a clean clone; merged in #18 |
| claim | J1.5 | jpinillaz | done | 2026-09-27 | not applicable | — / area A | ARQ worker/scheduler in #25; dispatcher with cleanup gate (U-S1-JF-04) in #30. Ack/retries move to J2.1 |
| claim | L1.6 | jpinillaz | done | 2026-09-27 | not applicable | — / architecture | `S1-v1` freeze of §1.1–§1.5 of `docs/plan/S1.md`; merged in #38 |
| claim | A1.1 | jpinillaz | done | 2026-09-27 | not applicable | — / area A | Health router and test base; merged in #21 |
| claim | A1.2 | jpinillaz | done | 2026-09-27 | not applicable | — / area A | Fail-fast settings; merged in #23 and #26 |
| claim | A1.5 | jpinillaz | done | 2026-09-27 | not applicable | — / area A, security | Core schema, S1 shape of ingestion/outbox, AES-GCM, ADR-0006; merged in #24 |
| claim | A1.4 | jpinillaz | done | 2026-09-27 | not applicable | — / area A, security | Opaque session, CSRF and `GET /me`; merged in #22 |
| claim | A1.7 | jpinillaz | done | 2026-09-27 | not applicable | — / area A | Migrations in compose and runbook section; merged in #28 |
| claim | A1.8 | jpinillaz | done | 2026-09-27 | not applicable | — / area A, Web | Contract §1.2 (`PUT` answers 503 in S1); merged in #29. The per-user 429 waits for D4 |
| claim | S1.B1 | jpinillaz | done | 2026-09-27 | not applicable | — / area S | Hardening self-check and I-S1-SG-06 (part B1); merged in #33. Development host unmodified (exception) |
| claim | S1.B2 | satnovaOG | done | 2026-09-27 | not applicable | — / lead | Table `docs/spike/F0.2-capacidad-slot.md`. Two slots do not fit in the 256 MiB tmpfs. 200 MiB and 3 h not approved. |
| claim | S1.B3 | jpinillaz | done | 2026-09-27 | not applicable | — / area S | Lease, fencing and cleanup verified with real ffmpeg; merged in #34. `verified` only in a container (L1.6, exception 5) |
| claim | S1.B4 | satnovaOG | done | 2026-09-27 | not applicable | — / lead | Matrix I-S1-SG-04: 10 passed in an Ubuntu container (own PID namespace). On the host's WSL, root's `unreadable_processes` prevents `verified`. |
| claim | S1.B5 | satnovaOG | done | 2026-09-27 | not applicable | — / area S | I-S1-SG-06: 9 passed in WSL. Sweep at zero. On this host the success does not end up `verified` because of `unreadable_processes` (same as S1.B4). |
| claim | S1.B6 | satnovaOG | done | 2026-09-27 | not applicable | — / area S | I-S1-SG-05: a `pending` lease does not close admission; cleanup `failed` alerts and closes it. Outbox: 1 passed. |
| claim | A1.9 | jpinillaz | done | 2026-10-04 | not applicable | — / area A | Suite I-S1-AN-05 against Postgres; merged in #44. The A1.6 and A1.3 routes stay outside the matrix. |
| claim | L1.4 | jpinillaz | done | 2026-10-04 | not applicable | — / lead | Pydantic drafts §M4/§M6 (U-S1-JL-01/02); merged in #43. No LLM calls. |
| claim | L1.5 | jpinillaz | done | 2026-10-04 | not applicable | — / lead | G4 dataset format and validator (U-S1-JL-03); merged in #45. No real content. |
| claim | A1.6 | <danielsam171> | done | 2026-10-06| not applicable | jpinillaz / area A | Subject CRUD (I-S1-AN-07); merged in #50 with the `DELETE`/`POST /audios` row-lock fix. Force deferred to S2 |
| claim | A1.3 | ARES313 | done | 2026-10-11 | verified (2026-10-12, jpinillaz) | jpinillaz / area A, security | Google OAuth backend (I-S1-AN-06) merged in #42. Real login with a test OAuth client in local compose observed by the lead on 2026-10-12; setup in `docs/RUNBOOK-dev.md` (#61); evidence in `jose-leonardo.md` (week notes, 2026-10-12). No credentials in the repo. |
| claim | D1.1 | ARES313 | done | 2026-10-11 | not applicable | jpinillaz / lead | Vite + React scaffold under ADR-0007; merged in #42 |
| claim | D1.2 | ARES313 | done | 2026-10-11 | not applicable | — / lead | Base routes and shared layout; merged in #42 |
| claim | D1.3 | ARES313 | done | 2026-10-11 | not applicable | — / lead | Google login, logout and loading/error states; merged in #42 |
| claim | D1.4 | ARES313 | done | 2026-10-17 | not applicable | — / area D | Same-origin HTTP client with CSRF on mutations and 401/403/429 interceptors (`ad75e7c`); U-S1-DA-01: 15 tests green. Pending PR to `develop`. Merged in #80 (`db7a7be`). |
| claim | D1.5 | ARES313 | done | 2026-10-18 | not applicable | — / area D | Base components: `DateInput`/`TimezoneSelect`/`LanguageSelect` (no language preselection), `StatusBadge` (exact contract values), `EmptyState`, `Toast`+`ToastProvider`; `lib/{status,languages,timezones,validation}.ts`. U-S1-DA-02 (9) and C-S1-DA-03 (5) green. Pending PR to `develop`. |
| claim | D1.6 | ARES313 | done | 2026-10-18 | not applicable | — / area D | Subject CRUD against the real A1.6 API: `SubjectForm` (required-field validation + API error mapping) and `MateriasPage` list/create/edit/delete with the active-classes dialog (`409 subject_has_active_audios`). U-S1-DA-02 extended (6 tests); 35 web tests green. Verified live in local compose against `/subjects`. Closed retroactively on 2026-10-27 (S1 was already closed); PR to `develop` pending. |
| claim | D1.7 | ARES313 | transferred | 2026-10-18 | not applicable | — / area D | Static drafts delivered as `in progress` (`582dbe9`): `UploadForm` (versioned consent + two mandatory acknowledgements, no language preselection, "audio not kept") and `BibliotecaPage` (per-stage states + aggregate, "audio not kept", differentiated buttons). Transferred to S2 on 2026-10-27: the Friday review had no reviewers and S1 is closed. See the S2 `transfer` row. |

### S2 — Knowledge

| Action | Ticket | Owner | Status | Claimed at (UTC) | Cloud eligibility | Contributors / reviewers | Reason, evidence, or PR |
|---|---|---|---|---|---|---|---|
| claim | A2.1 | angalindog | done | 2026-10-05 | not applicable | — / area A | Migration `0002_s2_schema`; merged in #48 |
| claim | A2.2 | angalindog | done | 2026-10-05 | not applicable | — / area A | `GET /ingestion/capabilities`; merged in #47 |
| claim | A2.3 | angalindog | done | 2026-10-07 | not applicable | — / area A | `POST /audios` with consent and slot; merged in #51 |
| claim | A2.4 | angalindog | done | 2026-10-07 | not applicable | — / area A | Streaming `PUT`, SHA-256 and dedupe; merged in #52. The default sink is still 503 (C2). |
| claim | A2.5 | angalindog | done | 2026-10-15 | not applicable | — / area A | Recorded by the lead on 2026-10-30 from the merged PR: merged in #64 (`c709aa6`); owner per Plane. Evidence in the owner's logbook |
| claim | A2.6 | angalindog | done | 2026-10-15 | not applicable | — / area A | Recorded by the lead on 2026-10-30 from the merged PR: merged in #65 (`896e8ff`); owner per Plane. Evidence in the owner's logbook |
| claim | A2.7 | angalindog | done | 2026-10-15 | not applicable | — / area A | Recorded by the lead on 2026-10-30 from the merged PR: merged in #66 (`96e1c2b`); owner per Plane. Evidence in the owner's logbook |
| claim | A2.8 | angalindog | done | 2026-10-15 | not applicable | — / area A | Recorded by the lead on 2026-10-30 from the merged PR: merged in #67 and #68 (`cc3b947, a4e295a`); owner per Plane. Evidence in the owner's logbook |
| claim | A2.9 | angalindog | done | 2026-10-15 | not applicable | — / area A | Library reads, SSE progress and conditional txt/srt exports (I-S2-AN-07/09); branch `feature/A2.9-listing-sse-exports`; PR pending Merged in #69 (`34bd214`). |
| claim | S2.1 | satnovaOG | done | 2026-10-09 | not applicable | — / lead | Reception and ffprobe; merged in #54 |
| claim | S2.2 | satnovaOG | done | 2026-10-09 | not applicable | — / lead | Bounded ffmpeg and 30 s fragmentation; merged in #54 |
| claim | S2.3 | satnovaOG | done | 2026-10-09 | not applicable | — / lead | Backpressure and boundary reconciliation (U-S2-SG-04); merged in #55 |
| claim | S2.4 | satnovaOG | done | 2026-10-09 | pending | — / lead | Isolated Riva client; merged in #55. `P-S2-SG-12` is not executed. Cloud without a reviewer. |
| claim | S2.5 | satnovaOG | done | 2026-10-22 | not applicable | — / lead | Atomic transcript+segments commit (I-S2-SG-06). Merged in #75. No Riva call. |
| claim | S2.6 | satnovaOG | done | 2026-10-22 | not applicable | — / lead | Cleanup `finally` gate and outbox enable after verified cleanup (I-S2-SG-07). Merged in #75. No Riva call. |
| claim | S2.7 | satnovaOG | done | 2026-10-22 | not applicable | — / lead | Post-commit reconciler and fencing (I-S2-SG-08/09). Merged in #76. No Riva call. |
| claim | S2.8 | satnovaOG | done | 2026-10-22 | not applicable | — / lead | Cancellation matrix (I-S2-SG-10). Merged in #76. No Riva call. |
| claim | S2.9 | satnovaOG | done | 2026-10-22 | not applicable | — / lead | In-RAM harness for a short synthetic class (E-S2-SG-11 reduced). The 1 h synthetic class was not used. This does not close G2-T or G2-X. The 1 h Caddy demo stays blocked on J2.8, D2.2/D2.3 and D2/D6. No Riva call. Merged in #76. |
| claim | S2.10 | satnovaOG | done | 2026-10-23 | not applicable | — / lead | Pipeline calls one Riva client per attempt (I-S2-SG-13). Test double only; no NVIDIA channel. Blank text is `rejected`/`no_usable_text`. Branch `feature/S2.10-S2.13-riva-reception`. |
| claim | S2.11 | satnovaOG | done | 2026-10-23 | not applicable | — / lead | Internal streaming reception (I-S2-SG-14): `PUT/POST /internal/v1/receptions`, hold, admit, discard. `run_attempt` takes a stream. No whole-file buffer. Same branch. |
| claim | S2.12 | satnovaOG | done | 2026-10-23 | not applicable | — / lead | Admission latch restored from `cleanup_status=failed`; any heartbeat error marks the lease lost; ffprobe runs in its own process group (I-S2-SG-15). Same branch. |
| claim | S2.13 | satnovaOG | done | 2026-10-23 | not applicable | — / lead | Error-code catalog and API mapping (U-S2-SG-16). `probe_unavailable` is `requires_reupload`. Same branch. |
| claim | L2.10 | jpinillaz | done | 2026-10-11 | not applicable | — / lead | Web stack decision, ADR-0007; merged in #42 |
| claim | A2.13 | jpinillaz | done | 2026-10-12 | not applicable | — / area A, area S | Client code merged in #83 (`IngestReceptionSink`, credential, `/internal/*` 404); I-S2-AN-12 green against an in-memory ingest; D-5 accepted as a bounded risk; case 14 (`load`) not run |
| claim | L2.1 | angalindog | done | 2026-10-16 | not applicable | — / area A, lead | Recorded by the lead on 2026-10-30 from the merged PR: merged in #70 (`3dfb859`); owner per Plane. Evidence in the owner's logbook |
| claim | L2.2 | angalindog | done | 2026-10-19 | not applicable | — / area A, lead | Recorded by the lead on 2026-10-30 from the merged PR: merged in #71 (`3318b78`); owner per Plane. Evidence in the owner's logbook |
| claim | L2.3 | angalindog | done | 2026-10-19 | not applicable | — / area A, lead | Hybrid search: per-language FTS + pgvector cosine + deterministic RRF, tenant/version/tombstone filters in both branches before top-k. Branch `feature/L2.3-hybrid-search-rrf`, commits `1eb6c64`/`8f1e423`, PR #72 merged at `c061b2c`. `U-S2-JL-03/04` green (538 unit passed); migration `0003_backfill_chunks_tsv`. `I-S2-JL-06` moves to L2.4/A2.11. |
| claim | L2.4 | angalindog | done | 2026-10-21 | not applicable | — / area A, lead | Reusable `SearchService` (`retrieve`/`page`), hydration, plain-text snippet, honest segment times and opaque keyset cursor over the bounded RRF window. Branch `feature/L2.4-search-service-endpoint`, PR #73 merged at `f1cd049`; `I-S2-JL-06` (`apps/api/tests/integration/test_search.py`) 17 passed against `pgvector/pgvector:pg16` and Redis. |
| claim | A2.11 | angalindog | done | 2026-10-21 | not applicable | — / area A, lead | `GET /search` route, response schemas, optional embeddings-provider dependency and per-user Redis quota (pair with L2.4). Branch `feature/L2.4-search-service-endpoint`, PR #73 merged at `f1cd049`; `I-S2-JL-06` 17 passed against `pgvector/pgvector:pg16` and Redis. |
| claim | L2.5 | angalindog | done | 2026-10-22 | not applicable | — / area A, lead | Chat SSE pipeline: embedding retrieval, RRF evidence under the token budget, versioned prompt, buffered citations, `snapshot`/`delta`/`citation`/`done`/`error` events, reconnect by `Last-Event-ID`, idempotency by `client_message_id`, and cancellation/disconnect with a durable partial. QA corrections: conservative `TOKEN_SAFETY_FACTOR`, `<source id="Sn">` evidence envelope with marker/tag/`Question:` neutralization, two concurrent streams per user (`429 rate_limited`), and one bounded shared `httpx.AsyncClient`. Minimal `messages`/`message_sources` turn persistence is included; A2.12 keeps conversation CRUD/history/delete and `I-S2-AN-11`. Branch `feature/L2.5-chat-sse`; `ruff`/`mypy` clean (128 files), 607 unit passed; `I-S2-JL-07/08` 21 passed and the full integration suite 278 passed against `pgvector/pgvector:pg16` and Redis on 2026-10-22. API image `maulwurf-api:l25` builds and contains `/app/prompts/v1/chat_system.txt`. No real provider calls. PR #74 open. Merged in #74 (`1220d17`). |
| claim | L2.6 | angalindog | done | 2026-10-30 | not applicable | — / area A, lead | Validated citations: every live `[Sn]` marker is rehydrated with the retrieval filters (tenant, tombstone, audio id, active index/transcript version, segment overlap) and emitted only when the fresh identity matches the source the model saw; rejected markers emit no `citation`/`citas` entry, and persisted citations are revalidated on replay. Validation runs under a 2 s `asyncio.timeout` and fails closed on any `Exception`; repeated markers validate once per stream, `rehydrate_source` does not rebuild a snippet, and replay rejects malformed offsets before use. `U-S2-JL-05` (`apps/api/tests/unit/test_citation_validation.py`, 47 tests) plus `I-S2-JL-07/08` regressions for cross-tenant, stale transcript, retired generation, foreign segment, live invalidation, replay invalidation and audio-id filtering. Branch `feature/L2.6-validated-citations`; `ruff`/`mypy` clean (133 files), 662 unit passed; full integration 297 passed against `pgvector/pgvector:pg16` on 2026-10-30 UTC, including `test_chat_sse.py` 27 and the new `rehydrate_chunk_ids` audio-filter test. Mutation checks: bypassing live validation made the gated invalidation test fail (it emitted `citation`); removing the `Chunk.audio_id` filter made the audio-filter test fail. Both restored and green. `message_sources` is unchanged and keeps every delivered source. No migration, route change or real provider call. Pending for `done`: PR review and Quality CI green. Merged in #79 (`8e71cc4`). |
| claim | A2.10 | angalindog | done | 2026-10-28 | not applicable | — / area A | Cleanup gate for the text outbox (I-S2-AN-10) in `apps/api/tests/integration/test_transcript_commit_gate.py` (11 cases): the dispatcher holds `index`/`analyze` while the latest attempt has `cleanup_status=pending|failed` or no attempt, publishes each exactly once after verified cleanup (dispatcher `Counter`, not only ARQ job dedupe), proves Calendar/notification/deletion rows do not delay or block the gated events and pins `decide()` semantics, selects only the latest attempt and skips malformed gated rows. The single writer stays in `apps/ingest` (S2.5); no production code changed. Branch `feature/A2.10-atomic-commit-outbox-gate`; PR #78 open. Red/green by layer: `decide()` mutation → U-S1-JF-04 6 failed; dispatcher `LATEST_CLEANUP` mutation alone → 4 integration cases failed; both restored → 11 passed. `ruff`/`mypy` clean (128 files); 615 unit and 290 integration passed against `pgvector/pgvector:pg16` + Redis. No Riva call. Pending for `done`: PR #78 review and Quality CI green. Merged in #78 (`7d7bc9e`). |
| transfer | D1.7 | ARES313 | in progress | 2026-10-18 | not applicable | — / area D, Web | Transferred from S1 on 2026-10-27 (S1 closed; the Friday review had no reviewers). Static upload and library drafts stay valid, now with the cleanup-pending state and a mock error state. Mandatory verification pending: product/privacy/accessibility/Web checklist and screenshots in `docs/sprints/D1.7-review.md`. Feeds D2.1/D2.4. |
| claim | D2.1 | ARES313 | in progress | 2026-10-29 | not applicable | — / area A, area D, Web | Upload form (M2) wired to A2.3/A2.2: subject/date/timezone/language without preselection, versioned notice + two mandatory acknowledgements, honest capabilities limits (submit disabled when `limits_validated=false`). Adds `privacy_notice_version` to `GET /ingestion/capabilities` (cross-area, area-A review pending). Plan `docs/plan/tickets/D2.1.md`; U-S2-DA-01 green (50 web tests), API unit 615 passed. No bytes sent yet (D2.2). PR to `develop` and live UI check pending. |
| claim | J2.1 | jpinillaz | done | 2026-10-30 | not applicable | — / area A | Real outbox dispatcher merged in #85: bounded backoff+jitter for transients, infra errors spend no attempts, revalidation before publish (U-S2-JF-01) |
| claim | J2.7 | jpinillaz | done | 2026-10-30 | not applicable | — / area S, area A | I-S2-JF-05 merged in #86 (pre-commit rollback, post-commit reconcile without ASR, stale token cannot record cleanup); ingest suite as root in a container |
| claim | J2.8 | jpinillaz | done | 2026-10-30 | not applicable | — / area S | Explicit `ingest` env list, no API secrets, no `edge`, `replicas: 1`; I-S2-JF-11 in CI; merged in #88 |
| claim | J2.9 | jpinillaz | done | 2026-10-30 | not applicable | — / lead | No exit-5 tolerance, Redis `maxmemory` asserted, secret scan and license report; merged in #87. Real-CI proof: draft #89 with a seeded fake key failed `secret-scan` (closed unmerged) |
| claim | J2.2 | jpinillaz | done | 2026-10-30 | not applicable | — / area A | Merged in #91: in-job revalidation, `processing_runs` lease, atomic activation, max 3 transient retries (I-S2-JF-02). Indexes nothing while D3-Text is open |
| claim | J2.5 | jpinillaz | in progress | 2026-10-30 | not applicable | — / area S, area A | Merged in #92: JSON logs with ids and redaction, `/internal/metrics`, `cleanup.failed` alert. Not `done`: the mandatory C-S2-JF-10 compose capture, alert destination and scrape config are still pending |
| claim | J2.4 | jpinillaz | done | 2026-10-30 | not applicable | — / area A, area S | Merged in #94 (11a4b0d): dead attempts and expired `running` index runs, stuck outbox re-published, lease duplicates, the `HELD` attempt left after token loss (J2.7), orphan `building`/`retired` generations (I-S2-JF-04). Retired generations cited by `message_sources` are retained. Follow-up #104 (875fba6) bounds lost-dispatch retries and recovers lease-expired runs; C3/C4/C5 (Redis I/O under row locks, API run fencing, periodic ingest sweep) remain documented follow-ups |
| claim | J2.3 | jpinillaz | done | 2026-10-30 | not applicable | — / area A | Merged in #95 (3cc50fb): Redis-loss recovery after losing the queued job; jobs regenerate from DB/outbox, never audio (I-S2-JF-03). Uses a dedicated Redis DB and `FLUSHDB`, so DB 0 is untouched |
| claim | A2.14 | jpinillaz | done | 2026-10-31 | not applicable | — / area A | Merged in #101 (53ab840): atomic reservation lifecycle, per-user/global capacity locks, pre-byte `receiving` claim, immediate release on failure/disconnect and `expire_stale_reservations` (I-S2-AN-13). Deploy `ingest` before `api` |
| claim | A2.15 | jpinillaz | done | 2026-10-31 | not applicable | — / area A | Merged in #99 (7206df0), follow-up #103 (15e62a1): 404/405/500 envelope, `bad_request` for unmapped 4xx, uvicorn traceback redaction and no exception text in the body (U-S2-AN-14) |
| claim | A2.16 | jpinillaz | done | 2026-10-31 | not applicable | — / area A, security | Merged in #98 (25060fd), follow-up #102 (cc0e8d7): `MAULWURF_ENV` required; 32-byte floor only for boundary secrets; boundary/provider alias checks; fail-closed outside `local` (U-S2-AN-15) |
| claim | L2.11 | jpinillaz | done | 2026-10-31 | not applicable | — / lead, area L | Merged in #100 (e97a86d): status/path reconciliation and `scripts/docs/check_refs.py` in the CI `docs` job; 652 paths checked. `CLAIMS.md` discrepancies were reported, not rewritten (C-S2-JL-13) |

## 4. Change history

| Date (UTC) | Decision | Reference |
|---|---|---|
| 2026-09-19 | A canonical registry is adopted; the pre-filled personal lists become historical candidates. | Document review |
| 2026-09-23 | S1 opens; L1.1, L1.2, L1.3 and S1.A1 are claimed (done). | This PR |
| 2026-09-24 | S1.A2 is claimed. The real call is observed (gRPC `OK`); status `done`. Cloud eligibility stays `pending` (no reviewer). | This change |
| 2026-09-24 | S1.A3 and S1.A4 are claimed. Runs observed; status `done`. Cloud eligibility stays `pending`. | This change |
| 2026-09-24 | S1.A5 is claimed. The series of three calls is observed; status `done`. 200 MiB and 3 h are not approved. Cloud eligibility `pending`. | This change |
| 2026-09-25 | J1.1–J1.4 and J1.6–J1.8 are claimed (J1.5 stays pending in S1). Work and evidence recorded before the merge; status `in progress` until review. | This PR |
| 2026-09-26 | J1.1–J1.4 and J1.6–J1.8 are closed as `done` after merging #12–#18 into `develop` (quality pipeline green at `4aa8ced`). J1.5 stays pending in S1. | This PR |
| 2026-09-27 | J1.5, L1.6, A1.1, A1.2, A1.4, A1.5, A1.7, A1.8, S1.B1 and S1.B3 are claimed (critical path and tickets with the most dependents). A1.3, A1.6, A1.9, L1.4, L1.5, L1.7 and S1.B2, S1.B4–B6 stay free; S1.A6–A9 still keep their owner in Plane. | This PR |
| 2026-09-27 | A1.1 and A1.5 move to `done` (#21, #24). J1.5 stays `in progress` with the partial from #25. A1.2 and A1.4 in review. | This PR |
| 2026-09-27 | S1.A6 and S1.A7 are claimed. Runs observed; D6 stays `pending` with proposal `none`. Cost `NO VERIFICADO`. Cloud eligibility `pending`. | This change |
| 2026-09-27 | S1.A8 and S1.A9 are claimed. Report and decision record published. D2 and D6 `pending`; D3-Audio and D4 `blocked`. Cloud eligibility `pending`. | This change |
| 2026-09-27 | The cloud eligibility of S1.A2–A7 stays `pending` until there is a reviewer. S1.A8 and S1.A9 do not call NVIDIA. | This change |
| 2026-09-27 | S1.B2 (done) and S1.B4 are claimed. The matrix passes in a container (10 passed). Not on the WSL host, because of unreadable root processes. S1.B1 and S1.B3 stay `in progress` under jpinillaz. | This change |
| 2026-09-27 | S1.B5 and S1.B6 are claimed (`in progress`). | This change |
| 2026-09-27 | S1.B5 and S1.B6 move to `done`. I-S1-SG-06: 9 passed in ingest. I-S1-SG-05: the outbox gate, 1 passed. On WSL the success does not end up `verified` because of `unreadable_processes`. | This change |
| 2026-09-28 | A1.2, A1.4, A1.7, A1.8, J1.5, S1.B1 and S1.B3 move to `done` after merging #22, #23, #26, #28–#30, #33 and #34 into `develop`. L1.6 stays `in progress` until its PR is merged. | This PR |
| 2026-09-28 | L1.6 moves to `done`: the `s1/l1.6-freeze` PR (#38) was merged into `develop` (35e4d70) after #39/#40. The `S1-v1` freeze is published in `docs/plan/S1.md`. | This PR |
| 2026-10-04 | A1.9, L1.4 and L1.5 are claimed (free since 2026-09-27). | This PR |
| 2026-10-05 | A1.6 is claimed (free since 2026-09-27). | This PR |
| 2026-10-09 | S2 opens. S2.1 and S2.2 are claimed (`done`, #54) and S2.3 and S2.4 (`in progress`). D2 and D6 stay `pending`; D3-Audio and D4 stay `blocked`. S2.4 stays with cloud `pending`: `P-S2-SG-12` is not run. A1.6, A1.9, L1.4 and L1.5 stay `in progress` in S1. | This PR |
| 2026-10-09 | A1.9 (#44), L1.4 (#43) and L1.5 (#45) move to `done`. A1.6 stays `in progress`: #49 only claims and the CRUD is not in `develop`. A1.3 and L1.7 are not implemented. A2.1 (#48), A2.2 (#47), A2.3 (#51) and A2.4 (#52) are claimed as `done` (`angalindog`). A2.5 stays open. | This change |
| 2026-10-11 | The lead accepts the Vite + React web stack (ADR-0007, in #42). A1.3, D1.1, D1.2 and D1.3 are claimed by ARES313 (`in progress` until #42 merges); A1.3 keeps cloud eligibility `pending`. | This PR |
| 2026-10-12 | A1.3, D1.1, D1.2 and D1.3 move to `done` (#42); A1.3 cloud eligibility `verified` after the lead observed a real Google login in local compose. A1.6 moves to `done` (#50). S2.3 and S2.4 move to `done` (#55); S2.4 keeps cloud `pending`. L2.10 is recorded as `done` (ADR-0007). A2.13 is claimed (`in progress`) with ADR-0008. | This PR |
| 2026-10-15 | A2.9 is claimed (`in progress`): tenant listing, `GET /audios/{id}` detail, SSE progress and conditional txt/srt exports; tests I-S2-AN-07/09 green locally. | This PR |
| 2026-10-18 | D1.5 (Web area) of ARES313 is claimed and closed: base components (date/timezone/language without preselection, contract-value badges, empty states, toasts) with U-S1-DA-02 (9) and C-S1-DA-03 (5) green. | This change |
| 2026-10-18 | D1.6 (Web area) of ARES313 is claimed (`in progress`): subject CRUD against the real A1.6 API with `SubjectForm` and `MateriasPage`. U-S1-DA-02 extended (6 tests); 35 web tests green. | This change |
| 2026-10-18 | D1.6 moves to `done` after a live CRUD check in local compose. D1.7 is claimed (`in progress`): static upload and library drafts pending the Friday team review. | This change |
| 2026-10-19 | L2.3 is claimed by angalindog (`in progress`): hybrid search (FTS + pgvector + RRF), QA fixes and data migration `0003_backfill_chunks_tsv`; `U-S2-JL-03/04` green (538 unit passed), PR #72. `I-S2-JL-06` stays with L2.4/A2.11. Rows for L2.1/L2.2 remain missing from the registry. | This PR |
| 2026-10-21 | L2.3 moves to `done` after PR #72 merged at `c061b2c`. L2.4 and A2.11 are claimed by angalindog (`in progress`) as one functional pair: reusable `SearchService` and `GET /search`. Unit support is green (19 passed) and `I-S2-JL-06` is green against `pgvector/pgvector:pg16` (16 passed). | This PR |
| 2026-10-22 | L2.4 and A2.11 move to `done` after PR #73 merged at `f1cd049`. L2.5 is claimed by angalindog (`in progress`): chat SSE pipeline with minimal turn persistence; QA fixes add conservative token budgeting, the source envelope, the two-stream limit and the shared HTTP pool. Unit support and `I-S2-JL-07/08` are green (607 unit, 278 integration). A2.12 keeps CRUD/history/delete. | This PR |
| 2026-10-22 | S2.5 and S2.6 are claimed by satnovaOG (`in progress`) on `feature/S2.5-S2.6-atomic-commit-cleanup`. The commit and the cleanup gate do not call Riva; S2.10 stays open. | This change |
| 2026-10-22 | S2.7, S2.8 and S2.9 are claimed by satnovaOG (`in progress`) on `feature/S2.7-S2.9-reconciler-cancel-e2e`. S2.9 is the in-RAM harness; the 1 h Caddy demo stays blocked. No Riva call. | This change |
| 2026-10-23 | S2.5–S2.8 move to `done` (merged in #75 and #76). S2.9 moves to `done` for the short in-RAM harness only: the 1 h synthetic class was not used, and that does not close G2-T or G2-X. S2.10–S2.13 are claimed by satnovaOG (`done`) on `feature/S2.10-S2.13-riva-reception`. No NVIDIA channel. | This change |
| 2026-10-27 | D1.6 closes retroactively as `done` (S1 was already closed) after the live subject CRUD check; the PR to `develop` is still pending. D1.7 is `transferred` from S1 to S2 because the mandatory Friday review had no available reviewers; it stays `in progress` with the checklist and states in `docs/sprints/D1.7-review.md`, and screenshots deferred. | This change |
| 2026-10-29 | D2.1 is claimed by ARES313 (`in progress`): upload form wired to A2.3/A2.2 with versioned consent, no language preselection and honest capabilities limits; adds `privacy_notice_version` to `GET /ingestion/capabilities` (area-A review pending). U-S2-DA-01 green (50 web tests); API unit 615 passed. No bytes sent yet (D2.2). | This change |
| 2026-10-30 | J2.1, J2.7, J2.8 and J2.9 are claimed by jpinillaz (`in progress`; dependencies A2.10, S2.5, A2.13 and J1.4 are met or paired). The A2.13 row records the client-code branch; it stays `in progress` until its PR merges. The L2.6 and A2.10 rows (angalindog), dropped by the #80 merge, are restored unchanged. | This PR |
| 2026-10-30 | A2.13, J2.1, J2.7, J2.8 and J2.9 move to `done` after #83, #85, #86, #88 and #87 merged into `develop` (#84 landed on the A2.13 branch after it closed and was re-opened as #88). J2.2 and J2.5 are claimed by jpinillaz (`in progress`; J2.1 is merged). | This PR |
| 2026-10-30 | Lead reconciliation against merged PRs and Plane: A2.9 (#69), A2.10 (#78), L2.5 (#74), L2.6 (#79) and D1.4 (#80) move to `done`; A2.5–A2.8 (#64–#68), L2.1 (#70) and L2.2 (#71) get the `done` rows they never had. L2.7 is `In Progress` in Plane with no claim row; it stays unrecorded until its owner claims it. | This PR |
| 2026-10-30 | J2.2 moves to `done` after #91 merged into `develop`. J2.5 stays `in progress` after #92 merged because its mandatory C-S2-JF-10 compose capture, alert destination and scrape config are still pending; it closes only with that evidence or an explicit reduced-DoD decision. J2.4 and J2.3 are claimed by jpinillaz (`in progress`; J2.2 is merged). | This PR |
| 2026-10-31 | J2.4 and J2.3 move to `done` after #94 and #95 merged into `develop` (11a4b0d, 3cc50fb). J2.2 stays `done`; J2.5 stays `in progress` until the C-S2-JF-10 evidence or a reduced-DoD decision. Plane is aligned: MAULW-78/79 Done, MAULW-80 In Progress. | This PR |
| 2026-10-31 | A2.14, A2.15, A2.16 and L2.11 are claimed by jpinillaz (`in progress`); A2.3/A2.4, A1.2 and L2.11 have their dependencies met. J2.5 keeps its separate C-S2-JF-10 closure. | This PR |
| 2026-10-31 | A2.14 (#101, 53ab840), A2.15 (#99 7206df0 + follow-up #103 15e62a1), A2.16 (#98 25060fd + follow-up #102 cc0e8d7) and L2.11 (#100 e97a86d) move to `done`. J2.4's follow-up #104 (875fba6) bounds lost-dispatch retries; C3/C4/C5 remain documented follow-ups. J2.5 stays `in progress` until C-S2-JF-10. | This PR |

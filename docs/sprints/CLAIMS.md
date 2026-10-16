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

### S2 — Knowledge

| Action | Ticket | Owner | Status | Claimed at (UTC) | Cloud eligibility | Contributors / reviewers | Reason, evidence, or PR |
|---|---|---|---|---|---|---|---|
| claim | A2.1 | angalindog | done | 2026-10-05 | not applicable | — / area A | Migration `0002_s2_schema`; merged in #48 |
| claim | A2.2 | angalindog | done | 2026-10-05 | not applicable | — / area A | `GET /ingestion/capabilities`; merged in #47 |
| claim | A2.3 | angalindog | done | 2026-10-07 | not applicable | — / area A | `POST /audios` with consent and slot; merged in #51 |
| claim | A2.4 | angalindog | done | 2026-10-07 | not applicable | — / area A | Streaming `PUT`, SHA-256 and dedupe; merged in #52. The default sink is still 503 (C2). |
| claim | A2.9 | angalindog | in progress | 2026-10-15 | not applicable | — / area A | Library reads, SSE progress and conditional txt/srt exports (I-S2-AN-07/09); branch `feature/A2.9-listing-sse-exports`; PR pending |
| claim | S2.1 | satnovaOG | done | 2026-10-09 | not applicable | — / lead | Reception and ffprobe; merged in #54 |
| claim | S2.2 | satnovaOG | done | 2026-10-09 | not applicable | — / lead | Bounded ffmpeg and 30 s fragmentation; merged in #54 |
| claim | S2.3 | satnovaOG | done | 2026-10-09 | not applicable | — / lead | Backpressure and boundary reconciliation (U-S2-SG-04); merged in #55 |
| claim | S2.4 | satnovaOG | done | 2026-10-09 | pending | — / lead | Isolated Riva client; merged in #55. `P-S2-SG-12` is not executed. Cloud without a reviewer. |
| claim | L2.10 | jpinillaz | done | 2026-10-11 | not applicable | — / lead | Web stack decision, ADR-0007; merged in #42 |
| claim | A2.13 | jpinillaz | in progress | 2026-10-12 | not applicable | — / area A, area S | API↔ingest contract: ADR-0008 and plan `docs/plan/tickets/A2.13.md` in this PR; client code in a follow-up PR (I-S2-AN-12) |

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

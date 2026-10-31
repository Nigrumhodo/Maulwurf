# Maulwurf — Implementation and validation plan

> Status: normative plan pending execution. Revision: 2026-09-19.
> Functional and technical contracts: [ESPECIFICACION.md](ESPECIFICACION.md). This plan
> defines dependencies, deliverables, and exit gates; it does not replace those contracts.

## 1. Objective and invariant decisions

Build a React web app (Vite SPA, ADR-0007) with a FastAPI backend to turn classes into queryable knowledge and
activity proposals, without persistently storing audio.

- Audio only in memory/tmpfs during reception, validation, conversion, and ASR; verified
  deletion before publishing text jobs. This includes copies, fragments, buffers, proxy,
  host, and observability.
- Language chosen explicitly before uploading; allowlist validated with NVIDIA. No `multi`
  autodetection, translation, or hidden fallback.
- ASR: `nvidia-riva-client`, Whisper large-v3, gRPC TLS to `grpc.nvcf.nvidia.com:443`,
  function-id `b702f636-f60c-4a3d-a6f4-f3568c13bd7d`. Bearer only on the server. Values subject to endpoint
  testing, never credentials in the frontend.
- Losing audio before a complete transcript is persisted requires a new upload. ARQ only
  retries analysis/indexing and integrations from durable data, never ASR from a temporary path.
- Citations to text, not a player. Times only from verified offsets; a correct citation does
  not demonstrate ASR acoustic accuracy.
- Human confirmation before every calendar write, including task revisions; chat does not
  write Calendar or send email.
- Cloud retention of audio/text requires contractual review. Own-side deletion does not
  guarantee deletion at NVIDIA, the LLM, Google, or the user's devices.

## 2. Starting point and scope per delivery

The repository contains documentation and a minimal skeleton: a FastAPI API with health
checks, ingestion/web packages, and manifests, but no compose, migrations, features, or test
suites. The phases and their checkboxes are **pending**; a placeholder or a written contract
does not demonstrate implementation.

| Delivery | Usable scope | Dependencies |
|---|---|---|
| F0 | Feasibility, contracts, secure technical foundation | Test credentials, secure RAM infrastructure, and privacy decisions |
| F1 | Library, transcript, hybrid search, and chat with citations | All F0 gates; validated timestamps or an explicitly accepted change |
| F2 | Extraction, human review, Calendar, agenda, and limited tools | F1; Google contract and extraction evaluation |
| F3 | Opt-in Gmail, reminders/digests, full dashboard, and Ctrl+K | F2; Gmail contract and the uncertain-delivery policy |
| F4 | Uncommitted exploratory backlog | Independent later prioritization |

F1 is the first knowledge delivery, not the complete action product. Knowledge + action
MVP = F1/F2; v1 adds F3. The installable PWA and the exploratory capabilities of §13 are
not activated by appearing in the stack.

### Coordination rules

1. The specification is the source of contracts; changes to phases, states, API, or
   guarantees update both documents in the same revision.
2. Every gate requires evidence: real command, version, date, environment, result, and
   redacted metrics. A mock does not validate a provider, nor does a unit test validate
   infrastructure.
3. A provider/tool blockage is recorded as **`NO VERIFICADO`** ("not verified"), never as approved. Do not use
   real classes while privacy is blocked.
4. S1–S4 are a planning sequence, not a calendar promise. Only the active sprint opens for
   claiming; the next one opens after reviewing capacity, the previous gate, and team
   availability. Auth/schema can be built in parallel with the spike, but real ingestion
   must not be enabled before F0.

## 3. F0 — Feasibility and foundation

### F0.1 NVIDIA contract and privacy (blocking)

- [ ] Review retention/processing terms, regions, subprocessors, and consent for third-party
  voices. Resolve D3-Audio before real classes; record D3-Text separately for each
  embeddings/LLM provider/model before sending real transcripts.
- [ ] Create a minimal spike with the official client and synthetic audio generated in RAM,
  with known expected text. Generation must produce intelligible speech, not just tones, to
  test ASR. Do not retain audio or publish it as a CI artifact.
- [ ] Run transcription, not translation, for each candidate language; test an invalid code,
  a missing language, and the wrong language. Do not infer support for Spanish or other
  languages from the `en`/`fr` example.
- [ ] Verify the normalized 16-bit mono PCM WAV format and effective sample rate. Test UI
  formats with real ffprobe/ffmpeg, distinguishing them from what Riva receives.
- [ ] Measure effective payload/duration limits, quotas, concurrency, deadlines, cancellation,
  and gRPC errors within the authorized budget. Do not run provider saturation tests without
  authorization.
- [ ] Request offsets and verify semantics/units/order/range and precision on known phrases,
  silences, and boundaries. Choose `word|segment|none` based on evidence; do not assume
  forced alignment is available.
- [ ] Measure latency, SDK memory, PCM expansion, overlap, and cost. Set fragment size,
  overlap, input limits, and deadlines from the results.

**Deliverable:** contract report with versions, redacted calls/schemas, enabled languages,
formats, measured limits, timestamps, and known restrictions. Do not publish API keys,
Authorization metadata, voice, or private transcripts.

**Exit:** D2, D3-Audio, and D4 resolved for the selected deployment. If there are no
reliable timestamps, explicitly approve G2-X/D6 (text-only product) or validate ephemeral
alignment for G2-T; update spec, API, and UI in the same revision. No silent fallback.

### F0.2 Non-persistence and recovery test (blocking)

- [ ] An ingestion process with a read-only root filesystem, bounded tmpfs, non-root user,
  no persistent swap/core dumps/snapshots/hibernation. Verify the host configuration, not
  only Compose.
- [ ] Real proxy without buffering/caching/capturing bodies to disk; direct binary path to
  FastAPI, with a `fetch` streaming `PUT` and without the default `UploadFile` spooling. The
  API streams the body to ingestion over the internal network (ADR-0008); neither hop spools.
- [ ] Measure RAM reservation per slot: original + active fragments + SDK/ffmpeg buffers +
  safety margin. The 200 MiB/3 h target is not an approved limit until the test is passed;
  reduce and publish capacity if it does not fit.
- [ ] Prototype supervisor, lease, fencing, and cleanup independent of the ASR process;
  check descriptors/processes/mount, not just deleting file names.
- [ ] Test success, incomplete upload, rejection, timeout, cancellation, ffmpeg/Riva/DB error,
  process SIGKILL, and container/host restart. Inject failure during and after the transcript
  commit.
- [ ] Verify the original, converted files, and fragments are absent; no audio bytes on disk,
  Redis, logs, traces, caches, or artifacts. Persist inspection evidence without audio.
- [ ] Check that lease expiration does not mark cleanup as verified. Without evidence, block
  the outbox; on cleanup failure, stop local admission and alert.

**Exit:** immediate normal cleanup and supervisor recovery demonstrated; limits/TTL
published. Sweeper target every 60 s, not an instant-deletion promise when the host is
unreachable. Any detected persistence blocks the delivery.

### F0.3 Application foundation

- [ ] Monorepo from §3, pinned supported versions, and lockfiles. FastAPI/Pydantic/SQLAlchemy
  async, React/Vite/TypeScript (ADR-0007), PostgreSQL/pgvector, Redis/ARQ, and ffmpeg.
- [ ] Services `web`, `api`, `ingest`, `worker`, `scheduler`, `postgres`, `redis`, `caddy`;
  no audio storage. Initial single-instance ingestion; scaling with reservation affinity
  remains pending design/testing.
- [ ] Google OAuth in the backend, opaque session, incremental scopes, encryption/rotation,
  `GET /me` as a non-cacheable CSRF bootstrap, CSRF/Origin on every mutation, and
  logout/revocation. Do not condition chat on Calendar/Gmail scopes.
- [ ] Tenant-aware migrations and subject CRUD. RLS, composite FKs, and pool/worker tests.
  Full model for attempts, transcript/segments, index/analysis versions, and outbox; later
  tables may be delivered in their phase.
- [ ] CI: lint, types, tests, integration, and real builds per §9. Protected cloud tests,
  separated from untrusted MRs. Add reproducible instructions to the README.

**Exit:** session/isolation and CRUD work, migrations from empty and upgrade pass, tests
and builds observed in the reference environment. None of this is considered implemented
because this plan exists.

## 4. F1 — Knowledge: complete vertical slice

### F1.1 Ingestion, text, and library

- [ ] Implement `/ingestion/capabilities` with effective language/formats/limits/TTL. Form
  without a preselected language, required metadata, and consent.
- [ ] `POST /audios` records versioned consent and reserves slot + attempt; it does not
  promise deduplication before knowing the hash. Authenticated `PUT` (terminated in the API
  and streamed to ingestion, ADR-0008) computes SHA-256 and
  answers 202 only after reception/admission, or 200/409 from dedup, always after cleanup.
  Compensate reservations if the DB fails.
- [ ] Exact-identity deduplication in the `PUT`; a variant returns an opaque single-use
  token, requires a second `POST` and re-upload, and verifies the hash again. Do not retain
  audio while waiting for confirmation. Test concurrency, token expiration/reuse, and
  separation between users.
- [ ] Transcribe sequential fragments with backpressure; reconcile boundaries and offsets
  without double-counting overlap. Empty/silence and poor-quality text are not presented as
  reliable transcription.
- [ ] Atomic commit of full transcript/segments/disabled outbox; verified cleanup; enable
  **index only in F1**. Recovery after the commit does not ask for retranscription.
- [ ] Cancellation, tombstone, fencing, and protection against late commits. Closing the tab
  after the 202 does not cancel; disconnection during reception does. Re-upload only without
  a usable transcript.
- [ ] Library with reader, TXT export, and SRT conditioned on valid offsets; SSE GET progress
  with snapshot/polling. No player, audio download, or service-worker private cache.

### F1.2 Retrieval and chat

- [ ] Token-based chunking with traceable spans; the §M6 `Chunk` maps field by field to
  `chunks`/`chunk_segments` (`content`, `transcript_id`, `index_version`, `char_start`,
  `char_end`, `token_count`) and each link carries the validated `overlap_span` JSON
  (`segment_ordinal`, segment-local `inicio_char`/`fin_char`, optional overlap region).
  Versioned embeddings, HNSW cosine, and per-language FTS with RRF. Tenant/version/tombstone
  filters in both branches.
- [ ] `GET /search` exposes the shared `SearchService` with plain-text snippets, nullable
  segment times only when reliable, and an opaque `next_cursor` over the bounded 60-hit RRF
  window (`limit` 1..50, default 8; no deep pagination). A per-user Redis quota is checked
  before the embeddings call; the provisional limit waits for D5/F0.
- [ ] Publish the complete index atomically; idempotent retries from text and outbox-based
  reconstruction if Redis is lost. Evaluate HNSW recall with selective filters against exact
  search.
- [ ] Chat `fetch` + SSE POST with snapshot, monotonic IDs, and one terminal `done|error`;
  context budget, idempotent `client_message_id`, cancellation, and partial states. At most
  two concurrent streams per user; a third returns `429 rate_limited` before persisting.
  Reconnection consults the persisted result and does not re-execute. Citations from the
  allowed registry, safe Markdown, and links only after validation.
- [ ] Persist provenance for all sources from context and history; delete/invalidate derived
  responses and block late publication when a source is removed.
- [ ] Versioned text dataset per enabled language: answerable/unanswerable questions, similar
  subjects, dates, technical terms, and embedded-instruction attacks.

**Exit:** real E2E through the proxy and services for a 1 h synthetic class and the
published effective limit. If at least 1 h is not demonstrated, explicit approval is
required to reduce the use case. Audio absent before indexing; reader/chat work without
analysis or summary (`not_requested`). G1, one variant of G2 (G2-T or G2-X), G3, G4, and
G5-F1 pass. Real ASR integration remains mandatory even though deterministic scenarios use
a test gRPC server.

## 5. F2 — Action: extraction and Calendar

- [ ] Windowed analysis, Pydantic schema, aggregation/dedupe, and atomic active review.
  Extraction based on class date/timezone; Unicode spans per segment and literal evidence
  checking.
- [ ] Versioned analysis runs: each `processing_runs` row records the transcript,
  configuration and prompt version it applied; a version change creates a new run while
  old runs remain queryable, and reanalysis reprocesses only the stages whose version
  changed.
- [ ] New classes require independent analysis and index. Explicit backfill over text for F1
  classes; no re-upload. Analysis failure allows chat if the index is valid, and vice versa
  for task review.
- [ ] Review inbox and summaries/topics; ambiguous dates or doubtful quality force review.
  No score-based auto-confirmation. Re-analysis does not overwrite edited/confirmed tasks.
- [ ] Separate the due date from the Calendar interval, all-day from instants, `task_sources`,
  pending revision of changes, and the sync status from the task lifecycle. A confirmed edit
  does not mutate active fields; confirming promotes `revision_id` with version control and
  an atomic outbox.
- [ ] Validate real scopes and secondary calendar creation; coordinated reconnect/refresh.
  Test scope rejection without breaking library/chat.
- [ ] Deterministic event ID, reconciled timeout/409, stale jobs discarded, and ETag for
  external conflicts. Local editing requires reconfirming before writing to Google.
  Reconnecting Calendar reactivates only current `blocked` versions and creates/reuses the
  outbox.
- [ ] Complete paginated reading of the -7/+60 day window, recurrences/deletions, atomic
  publication, and visible coverage/freshness. Do not combine filters incompatible with
  syncToken.
- [ ] Bound the manual refresh: per-user cooldown (`429` with `Retry-After`), no row lock
  held across the Google reads, and a bounded lock wait answered as a transient `503`.
- [ ] Task/agenda dashboard without duplicating the linked event; reading tools and
  `propose_task`, never direct writing. Time conflict distinct from per-day deadline
  concentration.
- [ ] Completing/dismissing cancels future reminders; the event is kept unless explicitly
  deleted. Remote deletion operations survive local purging without losing authorization or
  the IDs needed.

**Exit:** E2E with a Google test account: proposal → review → confirmation → one event;
reading and confirmed editing. Evidence of no writes before confirmation. Tests for
double-click, response failure, concurrent retries, DST/all-day, revocation, and external
changes; G5-F2, G6, and G7-CAL approved. Remote tests create only authorized resources and
clean them up at the end.

## 6. F3 — Reminders and v1 close-out

- [ ] Incremental/opt-in Gmail; recipient fixed to the verified email. Escaped templates with
  authenticated links; no email GET mutates tasks.
- [ ] Single ARQ scheduler with atomic claim/leases and persistent dedupe. T-48/T-24/T-2 h for
  instants; T-3d/T-1d at local time for all-day; no retrospective burst.
- [ ] Digests by local date; DST, due-date/preference changes, and cancellation of stale
  occurrences. Confirm task deletion/completion immediately before claiming a send.
- [ ] Ambiguous Gmail failure or expired `sending` lease → `delivery_unknown`, no blind
  retry. State that an already sent/in-flight email cannot be recalled when deleting a task.
- [ ] Delivery/failed-sync panel with warned manual resend, preferences, statistics, and
  Ctrl+K. Re-ranker only through a comparative experiment with quality/cost/latency.
- [ ] Runbooks for cleanup, provider outage, budget, revoked tokens, restore, and account
  deletion. D8 must already be approved before non-synthetic data; F3 checks restore,
  expiration, and reapplied deletions.

**Exit:** real test send observed and G7-MAIL/G8 approved; uncertain delivery visible, not
an exactly-once guarantee. Restore verified without reintroducing deleted sources. Security
and cost review before production.

## 7. Acceptance matrix and evidence

The following thresholds are **proposed gates**, not results. Agree on them before running
evaluation; do not lower them a posteriori to hide a failure. Publish numerator/denominator
per language and difficult slices, not only a global average.

| ID | Test/criterion | Minimum environment |
|---|---|---|
| G1 Privacy | Zero audio persistence observed in all F0.2 paths; cleanup confirmed before text jobs exit; incidents block admission | Real proxy, ffmpeg, processes, containers, and host; inspection of writes/logs/Redis/caches |
| G2-T ASR timing | Language/format/limits tested; requested offsets ordered, in range, and with accepted precision; zero invented times | Real Riva + synthetic voice per language; annotated phrases, silences, and boundaries |
| G2-X ASR text-only | D6 decision approved; `timestamp_precision=none`, null times, SRT and temporal links disabled; citations only by segment | Real Riva for text + API/UI/E2E demonstrating absence of invented times |
| G3 Isolation | Zero cross-tenant access, including guessed IDs, hybrid search, SSE, tools, jobs, and concurrent deletion | Two users minimum, real DB/queue, and E2E |
| G4 RAG | At least 50 questions per language (minimum 15 unanswerable); evidence Recall@8 ≥ 0.85 on answerables; reviewed-citation precision ≥ 0.95; correct abstention ≥ 0.90 on unanswerables; zero unauthorized citation IDs | Real selected embeddings/LLM, annotated dataset, and human review; record the small size and statistical limitation |
| G5-F1 Knowledge retrieval | No partial/duplicate active transcripts/indexes/messages; deleting a source prevents late responses/jobs; crash before/after commit is recovered | Fault injection with real Postgres/Redis/processes and F1 E2E |
| G5-F2 Action retrieval | No partial analysis/revisions/Calendar outbox; stale versions neither publish nor reactivate | Fault injection over analysis, confirmation, reconnect, and F2 Calendar |
| G6 Extraction | At least 50 annotated activities per language with ambiguities/DST; proposal precision ≥ 0.95 and recall ≥ 0.85; exact normalized date ≥ 0.95 on resolvable ones; every ambiguous case in the set goes to review; validated evidence in 100% of published proposals | Real LLM, deterministic temporal fixtures, and human evaluation; the model's score does not replace these metrics |
| G7-CAL Calendar | No write without confirmation; single event under retries; all-day/instants, scopes, ETag, reconnect, and reconciliation checked | Real Calendar on a protected account + deterministic failures |
| G7-MAIL Gmail | Opt-in and verified recipient; visible `delivery_unknown` and no blind retry; manual resend warned | Real Gmail on a protected account + deterministic failures |
| G8 Operation | API/ingest/web builds/tests approved; restore tested; reminders claimed/sent within the 15 min objective with healthy dependencies; budget/alerts active | Reference deployment and documented load |

### 7.1 Reproducible metrics protocol

G4 and G6 use versioned datasets with `development` and frozen `test` partitions. Before
running, publish: matching unit, formula, micro/macro average, tolerances, handling of
multiple valid answers, and the human adjudication procedure. Each enabled language must
pass separately; a global average does not compensate a failed language and prompts are not
tuned against `test`. A citation counts as correct only if its source belongs to the
tenant/version, the span reconstructs relevant literal text, and it answers the associated
claim. A proposal is matched by type, normalized date, and evidence, not only by title
similarity.

Performance: measure retrieval p95 ≤ 500 ms and chat first token p95 ≤ 5 s with published
corpus, concurrency, and cold/warm conditions. These are §8 objectives, not guarantees while
the benchmark is missing. For ASR, do not set an SLA or production limit without F0. Do not
assume acoustic precision from synthetic-voice tests: before expanding real usage, evaluate
representative consented material with annotations and equally ephemeral audio; a
synthetic-only evaluation leaves that generalization unverified.

### Test layers (all required)

1. **Unit:** dates/DST, Unicode offsets (including emoji/accents), overlap, evidence,
   chunking, RRF, states, dedupe, and reminders.
2. **Local integration:** real Postgres/pgvector/Redis and ffmpeg, proxy, auth/CSRF, outbox,
   tombstone/fencing, restarts, and lost responses. Provider doubles only for controlled
   failures.
3. **Cloud contract:** real Riva, LLM/embeddings, and Google with test permissions/budget.
   Record observed capabilities; do not claim support based on documentation or mocks.
4. **E2E and load:** browser → proxy → services → DB/provider, with per-stage failures,
   filter/context combinations, and effective limits. Measure resources and non-persistence
   under load too.
5. **Build/delivery:** lint, types, suites, and builds of all apps/images; migrations and
   restore. Commands are documented in [RUNBOOK-dev.md](RUNBOOK-dev.md); do not invent
   commands that do not exist today.

## 8. Risks and closing decisions

### 8.1 D1–D8 closing matrix

| Decision | Limit | Required gate/artifact |
|---|---|---|
| D1 Audience/deployment | Before opening access to third parties | Approved privacy review, OAuth consent, and tenant model |
| D2 Riva contract | F0 | Versioned provider-contract report |
| D3-Audio | Before real audio | Approved terms, region, retention, and consent |
| D3-Text | Before real transcript to each LLM/embedding | Sheet per provider/model; changing provider reopens the decision |
| D4 Capacity | F0 | RAM/tmpfs benchmark, published limits and deadlines |
| D5 Quality/cost | RAG in F1; extraction in F2 | G4 and G6 per language + budget |
| D6 Temporal alignment | F0/F1 | Approve exactly G2-T or G2-X and reflect it in API/UI |
| D7-CAL | Before F2 | Calendar contract and real scopes |
| D7-MAIL | Before F3 | Gmail contract, opt-in, and uncertain delivery |
| D8 Local retention | Before non-synthetic data | Active policy/backups/deletion; restore validated in F3 |

A decision is recorded with state `pending|approved|blocked`, the closure owner, date,
evidence, and consequences. `blocked` does not equal approved and stops the affected real
usage.

| Risk | Response / blocker |
|---|---|
| NVIDIA without the required retention guarantee | Block real audio; decide provider/deployment with the user (D3) |
| Insufficient timestamps | Block the temporal promise; decide ephemeral alignment or a product change (D6) |
| 200 MiB/3 h exceeds memory/deadline/quota | Reduce effective limits with approval and a consistent UI; do not introduce disk as a workaround (D4) |
| Lost host without cleanup evidence | Pending state, blocked outbox, and an infrastructure procedure; TTL is not deletion |
| ASR confuses dates or technical terms | Visible evidence, human review, per-language evaluation, and warnings; no automatic trust |
| Cloud API changes, Testing token expires, or quota runs out | Scheduled contracts, reconnection, and typed errors; do not promise a swappable provider without tests |
| Deleted text reappears via history, index, or backup | Transitive provenance, tombstone, version control, and deletions reapplied on restore |
| Gmail ambiguity | `delivery_unknown`; no exactly-once and no automatic retry |

## 9. Consistency checklist for every revision

- [ ] ESPECIFICACION and PLAN link existing files; phases F0–F3, gate variants, and
  decisions D1–D8 match.
- [ ] No new flow requires audio after cleanup, or introduces storage/player/retranscription
  without re-upload.
- [ ] Mandatory language before bytes; UI formats/limits equal to validated capabilities.
- [ ] ASR, cleanup, analysis, index, task, Calendar, and notification states remain separate;
  F1 does not depend on F2 analysis.
- [ ] The schema supports every API contract: dedupe decided in the PUT, versioned consent,
  spans/task_sources, agenda start/end, revisions, versions, and traceable deletion.
- [ ] Consent, isolation, and human confirmation also apply to jobs, tools, and later changes.
- [ ] All pending or blocked gates are reported; no documentary test is presented as
  application/provider validation.

# Process Diagrams — Maulwurf

> **Status:** processes derived from [ESPECIFICACION.md](ESPECIFICACION.md) (modules M1–M9 and
> §5), from [DISENO_BD_API_SPRINTS.md](DISENO_BD_API_SPRINTS.md) (§2.10 and §3) and from the
> plans [S1](plan/S1.md)–[S4](plan/S4.md), contrasted with the `develop` code at `16cf45b`
> (2026-10-30). They create no contracts: on any difference, the specification and the
> [implementation plan](PLAN_IMPLEMENTACION.md) prevail. The architecture is in
> [ARCH.md](ARCH.md).

## 1. What a Process Diagram Is

A **process diagram** describes a workflow from start to finish: who acts, in what order, which
decisions open branches and how each one ends (success, error or cancellation). It is the "how
it happens" of a use case. It does not replace the project's other diagrams:

| Diagram | Answers | Where it is |
|---|---|---|
| Architecture | Which pieces exist and how they connect | [ARCH.md](ARCH.md) |
| **Process** | Which steps, decisions and error branches a flow has | This document |
| Sequence | Which messages the participants exchange and in what order | [ESPECIFICACION §5](ESPECIFICACION.md#5-end-to-end-pipeline) |
| States | Which states an entity has and which transitions are valid | [DISENO §2.10](DISENO_BD_API_SPRINTS.md#210-state-machines-frozen-contracts) |
| Data | Which tables and relations exist | [ESPECIFICACION §6.1](ESPECIFICACION.md#61-er-diagram) |

## 2. How to Read Them

| Style | Meaning |
|---|---|
| Green fill | Implemented and in use |
| Amber fill | Exists in the code, but the process does not work end to end |
| Gray fill, dashed border | Specified or planned only |
| Red fill | Error or rejection terminal |
| Blue fill | External actor or system |
| Diamond | Decision; branches carry their condition |
| Rounded terminal | Outcome, with its HTTP code or status |

| Process | Phase and sprint | Status (reconciled 2026-10-30) | SVG |
|---|---|---|---|
| P0 · Process map | All | — | [proc-00-mapa.svg](diagrams/proc-00-mapa.svg) |
| P1 · Upload: reservation, reception and dedupe | F1 · S2 | Partial: default sink answers 503 | [proc-01-subida.svg](diagrams/proc-01-subida.svg) |
| P2 · Transcription, commit and cleanup | F1 · S2 | Partial: internal routes and library; default sink 503 | [proc-02-transcripcion.svg](diagrams/proc-02-transcripcion.svg) |
| P3 · Indexing and hybrid search | F1 · S2 | Implemented API-side; no browser flow | [proc-03-indexacion-busqueda.svg](diagrams/proc-03-indexacion-busqueda.svg) |
| P4 · Chat with citations | F1 · S2 | Implemented API-side; no browser flow | [proc-04-chat.svg](diagrams/proc-04-chat.svg) |
| P5 · Extraction, review and Calendar | F2 · S3 | Planned | [proc-05-extraccion-calendar.svg](diagrams/proc-05-extraccion-calendar.svg) |
| P6 · Reminders and digests | F3 · S4 | Planned | [proc-06-recordatorios.svg](diagrams/proc-06-recordatorios.svg) |
| P7 · Class and account deletion | S2 and S4 | Planned | [proc-07-borrado.svg](diagrams/proc-07-borrado.svg) |
| P8 · Team process | All | In force | [proc-08-equipo.svg](diagrams/proc-08-equipo.svg) |

## 3. P0 · Process Map

<!-- svg: proc-00-mapa -->
```mermaid
---
title: "P0 · Maulwurf process map"
config:
  flowchart:
    wrappingWidth: 330
    nodeSpacing: 24
    rankSpacing: 30
    padding: 8
---
flowchart TB
    subgraph F1["F1 · Knowledge (S2)"]
        direction LR
        p1["P1 · Upload a class<br/>reservation, reception and dedupe"]:::parcial
        p2["P2 · Transcribe and delete the<br/>audio<br/>commit and verified cleanup"]:::parcial
        p3["P3 · Index and search<br/>hybrid index"]:::plan
        p4["P4 · Chat with citations<br/>streamed response"]:::plan
        p1 --> p2
        p2 --> p3
        p3 --> p4
        p2 -.->|"no transcript:<br/>upload again"| p1
    end
    subgraph F2["F2 · Action (S3)"]
        p5["P5 · Extract, review and<br/>sync<br/>with Google Calendar"]:::plan
    end
    subgraph F3["F3 · Reminders and closure (S4)"]
        p6["P6 · Reminders and digests<br/>via Gmail"]:::plan
    end
    subgraph TR["Cross-cutting"]
        direction LR
        p7["P7 · Delete a class or the<br/>account<br/>S2 and S4"]:::plan
        p8["P8 · Team process<br/>sprint, claim, PR and done"]:::impl
    end
    F1 -->|"transcript with<br/>verified cleanup"| F2
    F2 -->|"confirmed task"| F3

    classDef impl fill:#e6f4ea,stroke:#2e7d32,color:#111
    classDef parcial fill:#fff4d6,stroke:#b26a00,color:#111
    classDef plan fill:#f3f3f3,stroke:#777,stroke-dasharray:5 4,color:#111
    style F1 fill:#fafafa,stroke:#9aa0a6,color:#111
    style F2 fill:#fafafa,stroke:#9aa0a6,color:#111
    style F3 fill:#fafafa,stroke:#9aa0a6,color:#111
    style TR fill:#fafafa,stroke:#9aa0a6,color:#111
```

Indexing (P3) and analysis (P5) hang off the same transcript and advance with independent
states: F1 does not depend on F2 extraction.

## 4. P1 · Class Upload: Reservation, Reception and Dedupe

<!-- svg: proc-01-subida -->
```mermaid
---
title: "P1 · Class upload: reservation, reception and deduplication"
config:
  flowchart:
    wrappingWidth: 330
    nodeSpacing: 24
    rankSpacing: 30
    padding: 8
---
flowchart LR
    subgraph F1["Phase 1 · Reservation<br/>POST /audios"]
        direction TB
        u0(["Student picks subject,<br/>date, IANA zone and language and<br/>accepts the consent"]):::ext
        a1["API validates session, CSRF,<br/>owned subject, zone, language<br/>from the allowlist and<br/>versioned consent"]:::impl
        d1{"Valid data?"}:::impl
        e1(["422 consent_required,<br/>language_not_allowed or<br/>validation_failed"]):::err
        d2{"Quota and slot?"}:::impl
        e2(["429 rate_limited or 503<br/>capacity_unavailable"]):::err
        a2["Creates the class and the<br/>awaiting_upload attempt. 201:<br/>audio_id, attempt_id, relative<br/>URL and expiration"]:::impl
        u0 --> a1
        a1 --> d1
        d1 -->|"no"| e1
        d1 -->|"yes"| d2
        d2 -->|"no"| e2
        d2 -->|"yes"| a2
    end
    subgraph F2["Phase 2 · Reception<br/>PUT /audios/{id}/content"]
        direction TB
        b1["API validates session, CSRF and<br/>that the attempt is current"]:::impl
        d3{"Valid attempt?"}:::impl
        e3(["409 attempt_not_active or 410<br/>upload_expired"]):::err
        b2["Opens the reception sink<br/>(C2 port)"]:::parcial
        d4{"Sink available?"}:::parcial
        e4(["503 capacity_unavailable<br/>before reading a byte. TODAY:<br/>always"]):::hoy
        b3["Receives streaming without<br/>spool, computes the SHA-256 and<br/>cuts off when the limit is exceeded"]:::parcial
        d5{"Size and format?"}:::parcial
        e5(["413 payload_too_large or 415<br/>unsupported_format"]):::err
        x1(["Disconnection: attempt<br/>cancelled and cleanup"]):::err
        b1 --> d3
        d3 -->|"no"| e3
        d3 -->|"yes"| b2
        b2 --> d4
        d4 -->|"no"| e4
        d4 -->|"yes"| b3
        b3 --> d5
        d5 -->|"no"| e5
        b3 -.-> x1
    end
    subgraph F3["Phase 3 · Dedupe<br/>SHA-256 + language + subject + date + zone"]
        direction TB
        d6{"SHA-256 already known for the<br/>tenant?"}:::parcial
        b4["Discards the temporary file and<br/>verifies cleanup before<br/>responding"]:::parcial
        d7{"Identical identity?"}:::parcial
        r1(["200 duplicate_exact: canonical<br/>class and reservation released"]):::parcial
        r2(["409 duplicate_variant:<br/>existing_audio_id and one-time<br/>token"]):::parcial
        c1["The student confirms: a<br/>second POST consumes the<br/>token and repeats the PUT"]:::ext
        d8{"Same SHA-256?"}:::parcial
        e6(["422 variant_hash_mismatch:<br/>rejected, no ASR"]):::err
        ok(["202 admitted by the<br/>supervisor: receiving →<br/>transcribing (P2)"]):::parcial
        d6 -->|"no"| ok
        d6 -->|"yes"| b4
        b4 --> d7
        d7 -->|"yes"| r1
        d7 -->|"no"| r2
        r2 --> c1
        c1 --> d8
        d8 -->|"no"| e6
        d8 -->|"yes"| ok
    end
    F1 -->|"201: uploads the file"| F2
    F2 -->|"size and format OK"| F3

    classDef impl fill:#e6f4ea,stroke:#2e7d32,color:#111
    classDef parcial fill:#fff4d6,stroke:#b26a00,color:#111
    classDef hoy fill:#fff4d6,stroke:#b26a00,stroke-width:4px,color:#111
    classDef err fill:#fde8e8,stroke:#c0392b,color:#111
    classDef ext fill:#e8eefc,stroke:#3b5bdb,color:#111
    style F1 fill:#fafafa,stroke:#9aa0a6,color:#111
    style F2 fill:#fafafa,stroke:#9aa0a6,color:#111
    style F3 fill:#fafafa,stroke:#9aa0a6,color:#111
```

**Status: partial.** `POST /audios` (A2.3) and the `PUT` validations (A2.4) are implemented.
The API has an `IngestReceptionSink` client (A2.13) and `ingest` serves the internal reception
routes (S2.11), but the default configuration does not set `MAULWURF_INGEST_URL` or the ingest
credential, so the `PUT` responds `503` before reading the body. Phase 2 and phase 3 are tested
with an injected sink (`test_put_content_dedupe.py`); the default local configuration cannot
reach them.

| Rule | Detail |
|---|---|
| Identity | SHA-256, language, subject, date and time zone; the title and the teacher do not participate |
| Order on duplicates | First the temporary file is discarded and verified, then `200` or `409` is answered |
| Quota and slot | `429` and `503` are conditional mechanisms (A2.3, D4): without configuration they do not limit |
| Variant token | Opaque, single-use and with TTL; only its hash lives in the DB, bound to tenant, hash and metadata |
| Confirmed variant | Does not mutate the evidence or the tasks of the existing class |
| Tab closing | Before the `202` it cancels; afterwards it does **not** cancel the ASR (see P2) |

## 5. P2 · Transcription, Transactional Commit and Verified Cleanup

<!-- svg: proc-02-transcripcion -->
```mermaid
---
title: "P2 · Transcription, transactional commit and verified cleanup"
config:
  flowchart:
    wrappingWidth: 330
    nodeSpacing: 24
    rankSpacing: 30
    padding: 8
---
flowchart LR
    subgraph A["Preparation<br/>supervisor with lease and fencing"]
        direction TB
        in0(["Attempt admitted, with the<br/>audio in the ingestion<br/>tmpfs (comes from P1)"]):::parcial
        i1["Takes the attempt: lease with<br/>fencing token and admission<br/>by capacity"]:::parcial
        i2["ffprobe validates container,<br/>duration and streams. Bounded<br/>ffmpeg normalizes to mono WAV"]:::parcial
        d1{"Valid format?"}:::parcial
        r1(["rejected: invalid format"]):::err
        i3["Splits into fragments of up to<br/>30 s with 0.25 s overlap,<br/>one active fragment at a time"]:::parcial
        in0 --> i1
        i1 --> i2
        i2 --> d1
        d1 -->|"no"| r1
        d1 -->|"yes"| i3
    end
    subgraph B["Transcription<br/>by fragments"]
        direction TB
        i4["Sends the fragment to Riva<br/>over gRPC TLS with the chosen<br/>language, never translate"]:::parcial
        d2{"Response?"}:::parcial
        d3{"Transient, with attempts and<br/>deadline?"}:::parcial
        r2(["requires_reupload: without audio<br/>there is no retranscription"]):::err
        d4{"Fragments remaining?"}:::parcial
        i6["Reconciles boundaries and<br/>overlap: global offsets and<br/>timestamp_precision"]:::parcial
        d5{"Usable text?"}:::parcial
        r3(["rejected: no_usable_text<br/>(silence or empty)"]):::err
        cx(["cancelled: cancellation before<br/>the commit"]):::err
        i4 --> d2
        d2 -->|"error"| d3
        d2 -->|"transcribed"| d4
        d3 -->|"yes: retries, max.<br/>3"| i4
        d3 -->|"no"| r2
        d4 -->|"yes"| i4
        d4 -->|"no"| i6
        i6 --> d5
        d5 -->|"no"| r3
        i4 -.->|"POST …/cancel"| cx
    end
    subgraph C["Commit and close<br/>closing always runs, also after rejected, requires_reupload or cancelled"]
        direction TB
        c1["Atomic commit: transcript,<br/>segments, language, model,<br/>version and precision, plus<br/>index and analyze events<br/>disabled with<br/>cleanup_pending. Attempt:<br/>transcript_committed_cleanup_pending.<br/>Today it only changes the status and<br/>stores counts"]:::parcial
        k1["Closes buffers and clients,<br/>kills the process group and<br/>deletes original, converted<br/>files and fragments"]:::parcial
        k2["Verifies in /proc<br/>descriptors, processes and<br/>mounts, and that the path no<br/>longer exists: cleanup_status =<br/>verified"]:::parcial
        d6{"Cleanup verified?"}:::parcial
        ok(["succeeded: the supervisor already<br/>writes it. Enabling the<br/>outbox (index and, from F2,<br/>analyze) is pending<br/>(S2.5 and S2.6)"]):::parcial
        pend(["Still<br/>transcript_committed_cleanup_pending,<br/>outbox blocked"]):::parcial
        rec["Reconciler (S2.7):<br/>verifies the cleanup and<br/>enables the outbox without<br/>retranscribing. If the host<br/>is lost, it requires evidence<br/>of tmpfs destruction"]:::plan
        c1 --> k1
        k1 --> k2
        k2 --> d6
        d6 -->|"yes"| ok
        d6 -->|"no, or the process<br/>dies"| pend
        pend --> rec
        rec --> ok
    end
    A -->|"valid format"| B
    B -->|"there is text"| C

    classDef parcial fill:#fff4d6,stroke:#b26a00,color:#111
    classDef plan fill:#f3f3f3,stroke:#777,stroke-dasharray:5 4,color:#111
    classDef err fill:#fde8e8,stroke:#c0392b,color:#111
    style A fill:#fafafa,stroke:#9aa0a6,color:#111
    style B fill:#fafafa,stroke:#9aa0a6,color:#111
    style C fill:#fafafa,stroke:#9aa0a6,color:#111
```

**Status: partial.** The supervisor, lease with fencing, ffprobe/ffmpeg, fragmentation,
reconciliation, Riva client, atomic transcript commit, cleanup gate and post-commit reconciler
exist with tests. `ingest` exposes the internal reception routes (S2.11) and the API has the
`IngestReceptionSink` client (A2.13), but the default local configuration answers `503` before
reading a byte (C2), so the upload flow is not demonstrated end to end in Compose. `asr_job`
uses the Riva client with stub or scripted transports in tests; no real NVIDIA channel is
exercised.

**Retry or reupload** (A2.8). Both endpoints exist:

| Situation | Action | Response |
|---|---|---|
| Usable transcript exists | `POST /audios/{id}/retry` with `{stages, transcript_version}` (`index` or `analyze`): idempotent runs over the text. The button says "retry analysis/index" | `202`; `409 transcript_unavailable` or `409 invalid_transition` |
| No usable transcript | `POST /audios/{id}/reupload`: new ephemeral session with the same identity; the student uploads again. The button says "upload again" | `201` with a new URL; `409` if a transcript already exists |

**Cancellation** (S2.8, A2.7):

| Moment | Result |
|---|---|
| Disconnection during reception | Cancels and cleans |
| After the `202` | Continues under lease; closing the tab or the SSE does **not** cancel |
| `POST /audios/{id}/attempts/{attempt_id}/cancel` before the commit | Invalidates the fencing; the supervisor cancels the subprocess and the gRPC, and cleans up. `202` |
| Cancel after the commit | `409` with the current status; the transcript is not destroyed |

**Other rules.** If the cleanup evidence fails, the instance closes admission of new uploads and
triggers an alert (S1.B6); an expired lease that leaves `pending` only blocks the outbox. If the
container dies (`SIGKILL`), the tmpfs disappears with it and the attempt is left with an expired
lease and `cleanup_status = pending`. A TTL is not deletion. The final state of a **non-transient**
provider error is not specified.

## 6. P3 · Text Indexing and Hybrid Search

<!-- svg: proc-03-indexacion-busqueda -->
```mermaid
---
title: "P3 · Text indexing and hybrid search"
config:
  flowchart:
    wrappingWidth: 330
    nodeSpacing: 24
    rankSpacing: 30
    padding: 8
---
flowchart LR
    subgraph IDX["Indexing<br/>index(audio_id) job from the persisted text"]
        direction TB
        o1["The scheduler dispatcher<br/>takes the enabled events<br/>and enqueues only IDs and version<br/>into Redis"]:::parcial
        w1["The worker revalidates tenant,<br/>tombstone and transcript<br/>version"]:::plan
        d1{"Still current?"}:::plan
        x1(["Discards: publishes nothing"]):::err
        w2["Chunking of about 800 tokens<br/>with about 100 overlap,<br/>Unicode offsets and<br/>chunk_segments links"]:::plan
        w3["Embeddings in batches with<br/>registered model, dimension and<br/>version"]:::plan
        w4["Builds the complete index<br/>generation (building) and<br/>activates it atomically:<br/>the previous one goes to retired"]:::plan
        d2{"Did the stage fail?"}:::plan
        w5["Transient failure: up to 3<br/>retries with backoff and<br/>jitter"]:::plan
        f1(["index stage = failed.<br/>Retry from the text:<br/>POST /audios/{id}/retry"]):::err
        ok1(["index stage = succeeded"]):::plan
        o1 --> w1
        w1 --> d1
        d1 -->|"no"| x1
        d1 -->|"yes"| w2
        w2 --> w3
        w3 --> w4
        w4 --> d2
        d2 -->|"no"| ok1
        d2 -->|"yes"| w5
        w5 -->|"retry"| w2
        w5 -->|"exhausted"| f1
    end
    subgraph QRY["Search<br/>GET /search?q=&subject_id=&from=&to=&limit=&cursor="]
        direction TB
        q1["The API validates the session and<br/>the filters"]:::plan
        q2["Lexical branch: tsvector per<br/>language"]:::plan
        q3["Vector branch: pgvector,<br/>cosine distance, HNSW"]:::plan
        q4["Each branch filters user_id,<br/>active resource, version,<br/>tombstones and requested filters<br/>before the top-k: about 30<br/>candidates per branch"]:::plan
        q5["Deterministic RRF fusion,<br/>deduplication and diversity.<br/>Order: score_rrf descending<br/>and chunk_id"]:::plan
        q6(["Paginated results with the<br/>Subject · Class · mm:ss citation<br/>or 'segment N' when there are no<br/>reliable times"]):::plan
        q1 --> q2
        q1 --> q3
        q2 --> q4
        q3 --> q4
        q4 --> q5
        q5 --> q6
    end
    IDX -.->|"the active index<br/>feeds the search"| QRY

    classDef parcial fill:#fff4d6,stroke:#b26a00,color:#111
    classDef plan fill:#f3f3f3,stroke:#777,stroke-dasharray:5 4,color:#111
    classDef err fill:#fde8e8,stroke:#c0392b,color:#111
    style IDX fill:#fafafa,stroke:#9aa0a6,color:#111
    style QRY fill:#fafafa,stroke:#9aa0a6,color:#111
```

**Status: implemented API-side** (L2.1–L2.4, J2.1–J2.2, A2.11). Chunking, embeddings, the
index job, the dispatcher and the cleanup gate exist; `GET /search` returns hybrid results with
a stable cursor. `analyze` remains a no-op and there is no browser flow. Design rules:

- An `index_requested` event is only dispatched if the latest attempt has `cleanup_status` at
  `verified`.
- Embeddings have a fixed model and dimension per index generation; changing them requires a new
  generation and reindexing, never mixing vectors.
- Without D3-Text closed for the provider, only synthetic or authored text is indexed.
- The RRF `k`, the candidate window and the `GET /search` response are implemented in
  `apps/api/app/services/search.py` and `apps/api/app/routers/search.py`.

## 7. P4 · Chat with Verified Citations

<!-- svg: proc-04-chat -->
```mermaid
---
title: "P4 · Chat with verified citations (SSE)"
config:
  flowchart:
    wrappingWidth: 330
    nodeSpacing: 24
    rankSpacing: 30
    padding: 8
---
flowchart LR
    subgraph A["Entry"]
        direction TB
        u0(["The student writes a<br/>question. Scope: all classes,<br/>one subject or one<br/>class"]):::ext
        a0["If there is no conversation: POST<br/>/chat/conversations. 201 or<br/>422 chat_mode_invalid"]:::plan
        a1["POST<br/>/chat/conversations/{id}/messages<br/>with client_message_id. 200<br/>text/event-stream, consumed<br/>with fetch"]:::plan
        d0{"client_message_id already used?"}:::plan
        r0(["Returns the saved<br/>message without calling<br/>the LLM again"]):::plan
        a2["Stores the user<br/>message and emits the snapshot"]:::plan
        u0 --> a0
        a0 --> a1
        a1 --> d0
        d0 -->|"yes"| r0
        d0 -->|"no"| a2
    end
    subgraph B["Retrieval and context"]
        direction TB
        a3["Question embedding and<br/>hybrid search (P3) with<br/>tenant, version and<br/>tombstone filters"]:::plan
        d1{"Enough evidence?"}:::plan
        e1(["error insufficient_evidence:<br/>abstention, asks for more<br/>context and invents neither<br/>sources nor dates"]):::err
        a4["Assembles the context: up to about<br/>8 chunks under the model window<br/>minus output reserve and<br/>margin. Trims the<br/>history by complete<br/>turns"]:::plan
        d2{"Does the minimum fit?"}:::plan
        e2(["error context_exceeded"]):::err
        a3 --> d1
        d1 -->|"no"| e1
        d1 -->|"yes"| a4
        a4 --> d2
        d2 -->|"no"| e2
    end
    subgraph C["Response and citations"]
        direction TB
        a5["The LLM answers with a tutor<br/>prompt: transcript and<br/>tool results are untrusted<br/>data. The API<br/>emits delta"]:::plan
        a6["Citation markers wait in a<br/>buffer until the DB validates<br/>that ID, chunk and segment exist<br/>and belong to the tenant and the<br/>active version"]:::plan
        d3{"Valid citation?"}:::plan
        x3["Not rendered as a source"]:::plan
        a7["Emits citation: Subject ·<br/>Class · mm:ss, with a link to<br/>/biblioteca/{audio_id}?segment=…<br/>or 'segment N'"]:::plan
        a8["Persists messages and<br/>message_sources with ALL<br/>the sources given to the<br/>model"]:::plan
        ok(["done event, single terminal:<br/>the message is left completed"]):::plan
        can(["Cancellation: POST<br/>…/messages/{message_id}/cancel,<br/>202. The partial is left<br/>cancelled"]):::plan
        a5 --> a6
        a6 --> d3
        d3 -->|"yes"| a7
        d3 -->|"no"| x3
        a7 --> a8
        x3 --> a8
        a8 --> ok
        a5 -.->|"the student<br/>cancels"| can
    end
    A -->|"new message"| B
    B -->|"context ready"| C

    classDef plan fill:#f3f3f3,stroke:#777,stroke-dasharray:5 4,color:#111
    classDef err fill:#fde8e8,stroke:#c0392b,color:#111
    classDef ext fill:#e8eefc,stroke:#3b5bdb,color:#111
    style A fill:#fafafa,stroke:#9aa0a6,color:#111
    style B fill:#fafafa,stroke:#9aa0a6,color:#111
    style C fill:#fafafa,stroke:#9aa0a6,color:#111
```

**Status: implemented API-side** (L2.5–L2.6, A2.12; `D2.5`–`D2.7` are still open). The chat
SSE route and turn persistence exist; citations are revalidated against the database. There is
no browser flow yet.

| Aspect | Contract |
|---|---|
| SSE events | Initial `snapshot`, then `delta` and `citation`, and exactly one terminal `done` or `error`; all with monotonic ID |
| Reconnection | `: ping` heartbeats between deltas; `Last-Event-ID` allows skipping what was already sent |
| Message states | `streaming`, `completed`, `failed` and `cancelled` |
| History | Newest page returned oldest-first; same-timestamp turns keep the question before its answer; opaque keyset cursor |
| Conversation list | Bounded keyset pages ordered by last activity |
| Idempotency | Repeating `client_message_id` returns the saved message |
| Without reliable times | The citation shows "segment N", never an invented minute (D6) |
| Sources | All sources given to the model are recorded, including those inherited from the history |

## 8. P5 · Activity Extraction, Human Review and Google Calendar

<!-- svg: proc-05-extraccion-calendar -->
```mermaid
---
title: "P5 · Activity extraction, human review and Google Calendar"
config:
  flowchart:
    wrappingWidth: 330
    nodeSpacing: 24
    rankSpacing: 30
    padding: 8
---
flowchart LR
    subgraph ANA["Analysis<br/>analyze(audio_id) job"]
        direction TB
        o1["analyze_requested outbox,<br/>enabled after verified<br/>cleanup. The worker takes<br/>the job"]:::plan
        w1["Windows with overlap and<br/>deduplication by identical<br/>evidence. Context: class date and<br/>zone, not the upload's"]:::plan
        w2["LLM with versioned prompt and<br/>Pydantic output:<br/>activities, dates, spans,<br/>literal quote, summary and<br/>topics"]:::plan
        v1["Deterministic validation:<br/>enums and ranges, class<br/>segment, tenant and version,<br/>Unicode offsets and literal<br/>quote in the text<br/>reconstructed from the DB"]:::plan
        d1{"Valid proposal?"}:::plan
        x1(["Discarded: no partial<br/>results and edited or confirmed<br/>tasks are not overwritten"]):::err
        d2{"date_status"}:::plan
        n1["resolved: pending task"]:::plan
        n2["ambiguous or missing:<br/>needs_review. Midnight is never<br/>invented"]:::plan
        o1 --> w1
        w1 --> w2
        w2 --> v1
        v1 --> d1
        d1 -->|"no"| x1
        d1 -->|"yes"| d2
        d2 -->|"resolved"| n1
        d2 -->|"ambiguous or missing"| n2
    end
    subgraph REV["Human review<br/>inbox"]
        direction TB
        rv["The student reviews<br/>evidence, date, confidence<br/>and the ASR warning. Edits,<br/>discards (dismissed) or<br/>confirms"]:::plan
        d3{"Valid version, date, block and<br/>evidence?"}:::plan
        e3(["409 version_conflict or 422<br/>invalid date or evidence"]):::err
        cf["Atomic transaction:<br/>confirmed and version + 1,<br/>outbox per task_id and<br/>due_version. 202 with<br/>calendar_sync_status =<br/>pending"]:::plan
        rv --> d3
        d3 -->|"no"| e3
        d3 -->|"yes"| cf
    end
    subgraph CAL["Google Calendar synchronization"]
        direction TB
        d4{"Google connected?"}:::plan
        bl(["calendar_sync_status =<br/>blocked"]):::plan
        sy["sync_task_event job on the<br/>secondary 'Maulwurf'<br/>calendar: deterministic event<br/>ID and ETag to avoid<br/>overwriting external changes.<br/>Timeout: look up by ID.<br/>409: reconcile. Stale<br/>version: discard"]:::plan
        d5{"Result?"}:::plan
        s1(["synced"]):::plan
        f1(["failed: explicit retry of<br/>the current version, back to<br/>pending"]):::err
        ig["invalid_grant: the<br/>integration goes to<br/>disconnected and pending<br/>tasks to blocked"]:::plan
        d4 -->|"no"| bl
        d4 -->|"yes"| sy
        sy --> d5
        d5 -->|"ok"| s1
        d5 -->|"error"| f1
        d5 -->|"invalid_grant"| ig
        ig --> bl
        bl -.->|"reconnect: reactivates<br/>those of the current<br/>version"| sy
    end
    ANA -->|"pending or<br/>needs_review proposal"| REV
    REV -->|"confirmed"| CAL
    CAL -.->|"editing a confirmed<br/>task: does not<br/>write until<br/>reconfirming"| REV

    classDef plan fill:#f3f3f3,stroke:#777,stroke-dasharray:5 4,color:#111
    classDef err fill:#fde8e8,stroke:#c0392b,color:#111
    style ANA fill:#fafafa,stroke:#9aa0a6,color:#111
    style REV fill:#fafafa,stroke:#9aa0a6,color:#111
    style CAL fill:#fafafa,stroke:#9aa0a6,color:#111
```

**Status: planned** (S3). There are no analysis, task or Calendar routes, jobs or services;
Calendar sync appears in `services/outbox` with no consumer and a draft Pydantic analysis schema
exists.

| Aspect | Contract |
|---|---|
| Task states | `pending`, `needs_review`, `confirmed`, `dismissed` and `completed`; `scheduled` is not a state |
| `calendar_sync_status` | `not_requested`, `pending`, `synced`, `failed` and `blocked` |
| Calendar write | Only after human confirmation; the chat tools never write to Calendar |
| Later editing | Editing a confirmed task creates a revision and does not write to Google until reconfirmed |
| Retry | `failed` only returns to `pending` with an explicit retry of the current version |

## 9. P6 · Reminders and Digests via Gmail

<!-- svg: proc-06-recordatorios -->
```mermaid
---
title: "P6 · Reminders and digests via Gmail"
config:
  flowchart:
    wrappingWidth: 330
    nodeSpacing: 24
    rankSpacing: 30
    padding: 8
---
flowchart LR
    subgraph A["Scheduling"]
        direction TB
        c0(["The student confirms a<br/>task (P5) and enables Gmail<br/>with PATCH /me/notifications.<br/>Opt-in is disabled<br/>by default"]):::ext
        d0{"Does the task have a date?"}:::plan
        n0(["No escalated reminders"]):::plan
        s1["Schedules occurrences with<br/>dedupe_key: user_id, type,<br/>task_id, due_version and<br/>occurrence. With time: T-48<br/>h, T-24 h and T-2 h. Date<br/>only: T-3 d and T-1 d at the<br/>local time"]:::plan
        s2["Changing the due date or the<br/>preferences cancels stale<br/>future occurrences. It never<br/>sends already-passed thresholds<br/>in a burst"]:::plan
        c0 --> d0
        d0 -->|"no"| n0
        d0 -->|"yes"| s1
        s1 -.-> s2
    end
    subgraph B["Sending"]
        direction TB
        sc["ARQ scheduler with a single<br/>active role per lease in the<br/>DB. Every minute it computes<br/>local due times and claims<br/>rows atomically"]:::plan
        dg["Daily digest (07:00<br/>configurable) and weekly<br/>(Sunday night): one<br/>run per user and local date,<br/>opt-in only"]:::plan
        snd["sending status: Gmail API<br/>with gmail.send, only to the<br/>user's verified email"]:::plan
        d1{"Result?"}:::plan
        sent(["sent"]):::plan
        fail(["failed"]):::err
        unk(["delivery_unknown: lost<br/>response or expired sending<br/>lease. It does NOT retry<br/>on its own"]):::err
        sc --> snd
        sc -->|"also evaluates the<br/>digests"| dg
        dg --> snd
        snd --> d1
        d1 -->|"Gmail accepts"| sent
        d1 -->|"error"| fail
        d1 -->|"no response"| unk
    end
    subgraph C["Uncertain delivery and resend"]
        direction TB
        pan["The GET /notifications panel<br/>explains delivery_unknown"]:::plan
        rs["Manual resend: POST<br/>/notifications/{id}/resend<br/>with<br/>acknowledge_duplicate_risk =<br/>true"]:::plan
        nw["Creates a new audited<br/>occurrence. The original never<br/>goes back to pending"]:::plan
        pan --> rs
        rs --> nw
    end
    A -->|"the occurrence is due"| B
    B -->|"delivery_unknown"| C

    classDef plan fill:#f3f3f3,stroke:#777,stroke-dasharray:5 4,color:#111
    classDef err fill:#fde8e8,stroke:#c0392b,color:#111
    classDef ext fill:#e8eefc,stroke:#3b5bdb,color:#111
    style A fill:#fafafa,stroke:#9aa0a6,color:#111
    style B fill:#fafafa,stroke:#9aa0a6,color:#111
    style C fill:#fafafa,stroke:#9aa0a6,color:#111
```

**Status: planned** (S4: J4.1–J4.6, A4.1). `me.py` states that Gmail and digest preferences
arrive in S4; there are no notification jobs, only the `notify` and `send_digest` event types
cataloged in the outbox.

| Aspect | Contract |
|---|---|
| States | `pending`, `sending`, `sent`, `failed`, `delivery_unknown` and `cancelled` |
| Delivery | Gmail does not offer exactly-once with `gmail.send`; a stable `Message-ID` does not guarantee remote dedupe |
| Target | Delivery within 15 minutes, not second-level accuracy; several workers protected by uniqueness and leases |
| Dates without time | T-3 d and T-1 d at local time; a nonexistent DST hour moves to the next valid instant and a repeated one runs only once |
| Mail | HTML and plain-text templates with escaped content; "Snooze" opens an authenticated UI and no mail GET mutates state |
| Unspecified | How a `failed` delivery is recovered |

## 10. P7 · Class and Account Deletion

<!-- svg: proc-07-borrado -->
```mermaid
---
title: "P7 · Class and account deletion"
config:
  flowchart:
    wrappingWidth: 330
    nodeSpacing: 24
    rankSpacing: 30
    padding: 8
---
flowchart LR
    subgraph CLA["Delete a class<br/>DELETE /audios/{id}, 202 (S2)"]
        direction TB
        c1["Tombstone: cancels the<br/>attempts and jobs of that class"]:::plan
        c2["Deletes the transcript, chunks,<br/>embeddings, derived<br/>tasks, summary and citations.<br/>Messages citing them are<br/>purged or moved to<br/>contentless tombstone"]:::plan
        c3["Google events already<br/>created are kept by<br/>default, with a warning.<br/>Deleting them requires an<br/>explicit option via<br/>google_remote_operations"]:::plan
        c1 --> c2
        c2 --> c3
    end
    subgraph CT1["Delete the account · start<br/>DELETE /me, 202 (S4)"]
        direction TB
        u0(["The student asks to delete<br/>their account and decides whether<br/>the Maulwurf events are deleted:<br/>delete_maulwurf_events"]):::ext
        a1["Creates account_deletions and<br/>responds 202 with<br/>deletion_operation_id"]:::plan
        a2["Applies tombstone and blocks<br/>sessions and jobs. The initiating<br/>session is restricted<br/>to GET /me/deletion/{id} and<br/>POST /auth/logout. The rest<br/>are revoked"]:::plan
        st(["While it lasts: GET<br/>/me/deletion/{id} returns<br/>pending"]):::plan
        u0 --> a1
        a1 --> a2
        a2 -.-> st
    end
    subgraph CT2["Delete the account · execution"]
        direction TB
        d1{"Delete events on Google?"}:::plan
        g1["Executes or enqueues the<br/>remote deletions BEFORE<br/>revoking the tokens:<br/>delete_event and<br/>delete_calendar, with<br/>pending, running, succeeded<br/>or failed states"]:::plan
        p1["Purges the user's data and<br/>derivatives"]:::plan
        p2["Removes the tokens"]:::plan
        d2{"Did any remote deletion fail?"}:::plan
        r1(["GET /me/deletion/{id}<br/>returns completed"]):::plan
        r2(["completed_with_remote_failures"]):::err
        rs["A non-sensitive residual<br/>record remains to look up<br/>the result, without keeping<br/>profile or text"]:::plan
        bk["Backups: encrypted text and<br/>metadata only, 30 days<br/>proposed at most. On restore<br/>the tombstones are<br/>reapplied"]:::plan
        d1 -->|"yes: authorized and with<br/>credentials"| g1
        d1 -->|"no"| p1
        g1 --> p1
        p1 --> p2
        p2 --> d2
        d2 -->|"no"| r1
        d2 -->|"yes"| r2
        r1 --> rs
        r2 --> rs
        p1 -.->|"backups expire"| bk
    end
    CT1 -->|"operation created"| CT2

    classDef plan fill:#f3f3f3,stroke:#777,stroke-dasharray:5 4,color:#111
    classDef err fill:#fde8e8,stroke:#c0392b,color:#111
    classDef ext fill:#e8eefc,stroke:#3b5bdb,color:#111
    style CLA fill:#fafafa,stroke:#9aa0a6,color:#111
    style CT1 fill:#fafafa,stroke:#9aa0a6,color:#111
    style CT2 fill:#fafafa,stroke:#9aa0a6,color:#111
```

**Status: partial.** Class deletion (`DELETE /audios/{id}` tombstone) is implemented (A2.7);
account deletion is implemented at the API level in A4.4 (`DELETE /me` creates the observable
operation and `GET /me/deletion/{id}` reports it). The Web client flows are D4.x.

| Aspect | Contract |
|---|---|
| Google events | No third-party events are deleted, nor deletions without authorization; without permissions deletion in Google is not guaranteed |
| Residual record | `account_deletions` keeps only what is needed to report the result, without profile or text |
| Operation states | `pending`, `completed` and `completed_with_remote_failures` |
| Backups | Encrypted text and metadata only; deletions are reapplied on restore (validated in F3) |

## 11. P8 · Team Process: Sprint, Claim, PR and Definition of Done

<!-- svg: proc-08-equipo -->
```mermaid
---
title: "P8 · Team process: sprint, claim, PR and definition of done"
config:
  flowchart:
    wrappingWidth: 330
    nodeSpacing: 24
    rankSpacing: 30
    padding: 8
---
flowchart LR
    subgraph A["Monday: open the sprint and claim"]
        direction TB
        m0["Monday, 45 min, whole<br/>team: the lead verifies the<br/>previous gate, capacity and<br/>availability"]:::impl
        m1["Opens the sprint and freezes the<br/>interfaces. The provider delivers<br/>contract or mock on Monday<br/>and code by day 3<br/>at the latest"]:::impl
        m2["Each member claims tickets<br/>with a PR touching only their<br/>row of<br/>docs/sprints/CLAIMS.md:<br/>unique owner, claimed_at UTC,<br/>status and cloud eligibility"]:::impl
        d1{"P-* tests or real<br/>provider calls?"}:::impl
        n1["Requires verified cloud<br/>eligibility, with date and<br/>reviewer"]:::impl
        d2{"Did anyone claim the ticket?"}:::impl
        ld["The lead resolves it:<br/>reassigns, splits with child<br/>IDs or explicitly reduces<br/>the DoD"]:::impl
        m0 --> m1
        m1 --> m2
        m2 --> d1
        d1 -->|"yes"| n1
        d1 -->|"no"| d2
        n1 --> d2
        d2 -->|"no"| ld
    end
    subgraph B["Development and PR"]
        direction TB
        w["Development: one PR per<br/>ticket, based on develop"]:::impl
        d3{"Does a contract change?"}:::impl
        sp["Updates ESPECIFICACION.md<br/>and PLAN_IMPLEMENTACION.md in<br/>the same PR"]:::impl
        ci["CI quality: python-static,<br/>python-unit, web,<br/>integration and build-images.<br/>P-* and load tests do not<br/>run in PRs"]:::impl
        rv["At least 1 review. If it crosses<br/>areas, whoever holds the claim<br/>there or the lead reviews"]:::impl
        mg["Merge to develop"]:::impl
        rt["Daily 15 min. Day 3:<br/>first consumable<br/>integration. Wednesday:<br/>cross-integration and switch<br/>from mocks to real code"]:::impl
        w --> d3
        d3 -->|"yes"| sp
        d3 -->|"no"| ci
        sp --> ci
        ci --> rv
        rv --> mg
        w -.-> rt
    end
    subgraph C["Ticket and sprint closure"]
        direction TB
        ev["Evidence in the logbook and<br/>in the CLAIMS.md row:<br/>command, UTC date, environment<br/>and result"]:::impl
        dd(["Ticket done: code, tests,<br/>updated contract or document,<br/>demonstrable in the local<br/>compose and with<br/>evidence"]):::impl
        fr["Friday, 45 min: demo,<br/>sprint DoD verification,<br/>retro and logbook<br/>closure"]:::impl
        ev --> dd
        dd --> fr
    end
    A -->|"ticket claimed"| B
    B -->|"merge"| C

    classDef impl fill:#e6f4ea,stroke:#2e7d32,color:#111
    style A fill:#fafafa,stroke:#9aa0a6,color:#111
    style B fill:#fafafa,stroke:#9aa0a6,color:#111
    style C fill:#fafafa,stroke:#9aa0a6,color:#111
```

Sources: [sprints/README.md](sprints/README.md), [PLAN_SPRINTS §0 and §7](PLAN_SPRINTS.md),
[ADR-0004](adr/ADR-0004-commits-pr.md), the PR template and `.github/workflows/quality.yml`.
Transferring or abandoning a ticket requires an atomic PR with the motive and the evidence
handover.

## 12. Discrepancies Between Documents

While drawing the processes, inconsistencies worth resolving appeared. Each diagram follows the
indicated column.

| Topic | One document says | Another says | This document follows |
|---|---|---|---|
| `PUT` destination | ESPECIFICACION §5: the web uploads to ingestion | `infra/Caddyfile` and `routers/audios.py`: `api` receives it and the C2 sink is pending | The code |
| Lease on reservation | `plan/S2.md` (A2.3): reservation "with lease" | `services/audios.py`: `lease_expires_at` stays `NULL` until ingestion takes the attempt | The code |
| Calendar outbox event | DISENO §2.6: `sync_task_event` | `plan/S3.md` (A3.4): `calendar.event.sync` | Only names the `sync_task_event` job |
| Order of the SSE events | ESPECIFICACION M7: `delta*` and then `citation*` | DISENO §3.10: `delta` and `citation` in any order | Does not fix the order |
| Weekly digest content | ESPECIFICACION M8: includes new tasks | `plan/S4.md` (J4.6): only upcoming ones and conflicts | Does not detail the content |
| Remote operations after the purge | DISENO §2.8: `google_remote_operations` survives the purge | The same table declares `user_id ... ON DELETE CASCADE` | The code (A4.4): `user_id` is an opaque UUID without a FK, so the residual remote ids, authorization, status and redacted error survive the purge |

## 13. Maintenance

The diagrams are Mermaid blocks preceded by `<!-- svg: name -->`. After editing them, run
`python3 scripts/docs/export_diagrams.py` to regenerate `docs/diagrams/*.svg` (see
[ARCH.md §8](ARCH.md#8-maintenance)). When merging a ticket that changes what is implemented,
update the colors, the status tables and the header commit.

# System Architecture — Maulwurf

> **Status:** architecture view derived from [ESPECIFICACION.md](ESPECIFICACION.md), which is the
> normative source, and contrasted with the `develop` code at `2298e1b` (2026-10-11). It creates
> no contracts: on any difference, the specification and the
> [implementation plan](PLAN_IMPLEMENTACION.md) prevail. Step-by-step flows are in
> [PROCESOS.md](PROCESOS.md).

The target architecture and the built one **still do not match**. S1 left a solid foundation
(Compose stack, schema, sessions, hardened ingestion as a library) and S2 is underway, but no
end-to-end flow exists. Each diagram marks what is implemented, what is partial, and what
remains planned.

## 1. How to Read the Diagrams

| Style | Meaning |
|---|---|
| Green fill, solid border | Implemented and wired into a process that runs |
| Amber fill | Exists in the code (endpoint or library with tests) but does not work end to end |
| Gray fill, dashed border | Specified or planned only |
| Blue fill | Actor or system outside the repository |
| Dashed line | Planned flow or not yet wired |

Each figure is exported to SVG in [diagrams/](diagrams/) (see §8).

| Fig. | Diagram | SVG |
|---|---|---|
| 1 | System context | [arq-01-contexto.svg](diagrams/arq-01-contexto.svg) |
| 2 | Docker Compose containers | [arq-02-contenedores.svg](diagrams/arq-02-contenedores.svg) |
| 3 | API and ARQ process components | [arq-03-componentes-api.svg](diagrams/arq-03-componentes-api.svg) |
| 4 | Ingestion components | [arq-04-componentes-ingesta.svg](diagrams/arq-04-componentes-ingesta.svg) |
| 5 | Data and trust zones | [arq-05-zonas-de-confianza.svg](diagrams/arq-05-zonas-de-confianza.svg) |

## 2. System Context

<!-- svg: arq-01-contexto -->
```mermaid
---
title: "Figure 1 · System context"
config:
  flowchart:
    wrappingWidth: 330
    nodeSpacing: 24
    rankSpacing: 30
    padding: 8
---
flowchart LR
    U(["Student<br/>browser / PWA"]):::ext
    subgraph MW["Maulwurf: self-hosted deployment, multi-tenant since F0"]
        SYS["Web · API · ephemeral<br/>ingestion · workers<br/>Postgres with pgvector ·<br/>Redis"]:::parcial
        end
    RIVA["NVIDIA Riva on NVCF<br/>ASR whisper-large-v3<br/>gRPC over TLS"]:::ext
    LLM["LLM and embeddings<br/>behind adapters"]:::ext
    GOO["Google<br/>OAuth · Calendar · Gmail"]:::ext

    U -->|"HTTPS, same origin:<br/>upload, search,<br/>chat, review"| SYS
    SYS <-.->|"ephemeral audio in<br/>chunks<br/>text and offsets"| RIVA
    SYS -.->|"text chunks<br/>and questions"| LLM
    SYS <-.->|"OAuth, events after<br/>confirmation<br/>mail only with<br/>opt-in"| GOO

    classDef parcial fill:#fff4d6,stroke:#b26a00,color:#111
    classDef ext fill:#e8eefc,stroke:#3b5bdb,color:#111
    style MW fill:#fafafa,stroke:#9aa0a6,color:#111
```

Today **no third-party call is wired**: the Riva client exists in isolation and unused, and
Google appears only as configuration and status read from the database.

| System | What it exchanges | Guarantees and limits | Status |
|---|---|---|---|
| Student | Uploads audio with chosen language and consent; consults, reviews and confirms | Every Calendar write requires explicit confirmation | API partial; the web is a scaffold |
| NVIDIA Riva | Receives audio in chunks and returns text and offsets | `grpc.nvcf.nvidia.com:443`, `whisper-large-v3`, transcription only (never `task:translate`). Retention and region unverified: D3-Audio `blocked`; D2 contract `pending` | Isolated client (S2.4 in progress), not wired |
| LLM and embeddings | Receive text chunks and questions | Provisional: `gpt-4o-mini` via LiteLLM and `text-embedding-3-small` (1536 dimensions). D3-Text is closed per provider and model before the first real transcript | Planned (S2–S3) |
| Google | OAuth login, Calendar events, mail via Gmail | Tokens only in the backend, encrypted with AES-GCM. Gmail is opt-in and only writes to the verified email | Configuration and status only; no login (A1.3) |

## 3. Containers

<!-- svg: arq-02-contenedores -->
```mermaid
---
title: "Figure 2 · Docker Compose containers (infra/docker-compose.yml)"
config:
  flowchart:
    wrappingWidth: 330
    nodeSpacing: 24
    rankSpacing: 30
    padding: 8
---
flowchart TB
    B(["Browser"]):::ext
    subgraph HOST["Host running Docker Compose"]
        CD["caddy 2.8 · edge network<br/>:80 and :443, internal TLS"]:::impl
        WEB["web · edge network<br/>Next.js :3000, scaffold"]:::parcial
        API["api · edge and data networks<br/>FastAPI :8000<br/>Alembic + Uvicorn"]:::impl
        ING["ingest · edge and data networks<br/>FastAPI :8000, only /healthz<br/>and /readyz<br/>rootfs ro, tmpfs 256 MiB, 1<br/>GiB without swap"]:::parcial
        PG[("postgres · data network<br/>pgvector pg16")]:::impl
        RD[("redis 7.4 · data network<br/>no RDB/AOF, noeviction")]:::impl
        SC["ARQ scheduler · data network<br/>dispatch_outbox every minute"]:::parcial
        WK["ARQ worker · data network<br/>index and analyze: no-op"]:::parcial
    end
    EXT["Riva · LLM · Google<br/>outside the host"]:::ext

    B -->|"HTTPS"| CD
    CD -->|"remaining routes"| WEB
    CD -->|"API routes and<br/>PUT"| API
    CD -.->|"PUT (S2)"| ING
    API -.->|"C2 port"| ING
    API --> PG
    API -->|"PING in /readyz"| RD
    ING -->|"readiness"| PG
    SC -->|"reads outbox"| PG
    SC -->|"enqueues IDs"| RD
    RD --> WK
    WK --> PG
    ING -.->|"gRPC TLS"| EXT
    WK -.-> EXT

    classDef impl fill:#e6f4ea,stroke:#2e7d32,color:#111
    classDef parcial fill:#fff4d6,stroke:#b26a00,color:#111
    classDef ext fill:#e8eefc,stroke:#3b5bdb,color:#111
    style HOST fill:#fafafa,stroke:#9aa0a6,color:#111
```

| Service | Image or build | Networks | Exposed | Limits and hardening | Runs |
|---|---|---|---|---|---|
| `caddy` | `caddy:2.8-alpine` (pinned digest) | edge | 80 and 443 on the host (configurable) | Read-only Caddyfile, admin API disabled; no healthcheck | Reverse proxy with internal TLS (`https://localhost`) |
| `web` | `apps/web` → `maulwurf-web:local` | edge | 3000, internal only | `USER node` | `npm run start` (Next.js 15, scaffold) |
| `api` | `apps/api` → `maulwurf-api:local` | edge, data | 8000, internal only | UID 10001 in the Dockerfile; compose adds no `read_only` nor `cap_drop` | Alembic migration and Uvicorn |
| `ingest` | `apps/ingest`, target `runtime` | edge, data | 8000, internal only | UID 10001, `read_only` rootfs, 256 MiB tmpfs at `/work/tmp` (`noexec,nosuid,nodev`), `cap_drop: ALL`, `no-new-privileges`, 1 GiB of RAM without swap, 2 CPU, 128 PIDs | Uvicorn `maulwurf_ingest.main:app` |
| `worker` | `api` image | data | — | `cap_drop: ALL`, `no-new-privileges`, 128 PIDs | `arq app.workers.arq_app.WorkerSettings` |
| `scheduler` | `api` image | data | — | Same as `worker` | `arq app.workers.arq_app.SchedulerSettings` |
| `postgres` | `pgvector/pgvector:pg16` (pinned digest) | data | None on the host | `pgdata` volume | PostgreSQL 16 with pgvector |
| `redis` | `redis:7.4-alpine` (pinned digest) | data | `127.0.0.1:6379`, loopback only | No RDB nor AOF, `maxmemory` 256 MB by default, `noeviction` (ADR-0005) | `redis-server` |

All services have a healthcheck except `caddy`, which waits for `web`, `api`, and `ingest` to be
healthy. The eight-service compose is ticket [J1.1](plan/tickets/J1.1.md); the hardened ingestion
image is J1.2, Caddy J1.3, the ARQ processes J1.5, and the Redis policy J1.6.

**Caddy routing** (`infra/Caddyfile`, in this order):

| Route | Destination | Notes |
|---|---|---|
| `PUT /audios/*/content` | `api:8000` | Streaming without compression or buffering. The Caddyfile comment anticipates moving it to `ingest:8000` in S2 |
| `/audios/*/progress` | `api:8000` with `flush_interval -1` | Progress SSE: the route exists in Caddy, but not in the API routers |
| `/auth/*`, `/me`, `/me/*`, `/subjects`, `/subjects/*`, `/integrations/*`, `/audios`, `/audios/*`, `/ingestion/*`, `/readyz` | `api:8000` | `/subjects` has no router yet in `develop` (A1.6) |
| Anything else | `web:3000` | Fallback |

**What is not connected yet**

- `ingest` receives no traffic from Caddy nor from `api`: its ASGI exposes only `GET /healthz` and
  `GET /readyz`, and `api` has no client towards it. The `PUT` ends at `api`, whose reception sink
  responds `503` (point C2, see §4.1).
- `worker` registers `index` and `analyze`, which only write the ID to the log (`max_tries=1`).
- `scheduler` runs `dispatch_outbox` at startup and every minute: it takes up to 100 events and
  enqueues IDs and version into Redis, without ACK nor retries (J2.1 pending).
- No container calls Riva, an LLM, or Google.

## 4. Components

### 4.1 API and ARQ Processes

<!-- svg: arq-03-componentes-api -->
```mermaid
---
title: "Figure 3 · API and ARQ process components (apps/api)"
config:
  flowchart:
    wrappingWidth: 330
    nodeSpacing: 24
    rankSpacing: 30
    padding: 8
---
flowchart TB
    CD["caddy"]:::ext
    subgraph PA["api process · uvicorn app.main:app"]
        subgraph RT["routers"]
            direction LR
            r1["health<br/>/healthz · /readyz"]:::impl
            r2["auth<br/>POST /auth/logout"]:::impl
            r3["me<br/>GET · PATCH /me"]:::impl
            r4["ingestion<br/>GET /ingestion/capabilities"]:::impl
            r5["integrations<br/>GET /integrations/status"]:::impl
            r6["audios<br/>POST /audios<br/>PUT /audios/{id}/content"]:::parcial
        end
        subgraph SV["services"]
            direction LR
            s1["sessions · tenant<br/>opaque cookie, CSRF,<br/>get_owned"]:::impl
            s2["audios<br/>slot reservation, quota,<br/>consent"]:::impl
            s3["uploads<br/>streaming, SHA-256,<br/>compensation"]:::parcial
            s4["dedupe<br/>exact or variant identity,<br/>token"]:::parcial
            sk{{"ReceptionSink (C2 port)<br/>today<br/>UnavailableReceptionSink:<br/>503"}}:::parcial
            s3 --> s4
            s3 --> sk
        end
        RT --> SV
    end
    subgraph PW["arq processes · same image as api"]
        sc["scheduler<br/>dispatch_outbox every minute,<br/>up to 100 events"]:::parcial
        dp["dispatcher + services/outbox<br/>publication policy and<br/>cleanup gate"]:::parcial
        rd2[("Redis")]:::impl
        wk["worker<br/>index and analyze: no-op"]:::parcial
        sc --> dp
        dp -->|"enqueues IDs"| rd2
        rd2 --> wk
    end
    PG[("Postgres")]:::impl
    ING["ingest<br/>real reception (S2)"]:::plan

    CD --> RT
    SV -->|"models + SQLAlchemy"| PG
    SV -.->|"C2 sink"| ING
    dp -->|"reads outbox_events"| PG

    classDef impl fill:#e6f4ea,stroke:#2e7d32,color:#111
    classDef parcial fill:#fff4d6,stroke:#b26a00,color:#111
    classDef plan fill:#f3f3f3,stroke:#777,stroke-dasharray:5 4,color:#111
    classDef ext fill:#e8eefc,stroke:#3b5bdb,color:#111
    style PA fill:#fafafa,stroke:#9aa0a6,color:#111
    style RT fill:#fafafa,stroke:#9aa0a6,color:#111
    style SV fill:#fafafa,stroke:#9aa0a6,color:#111
    style PW fill:#fafafa,stroke:#9aa0a6,color:#111
```

| Layer | Module | Purpose | Status |
|---|---|---|---|
| routers | `health` | `/healthz` and `/readyz` (Postgres and Redis) | Implemented |
| | `auth` | `POST /auth/logout` | Implemented; Google login does not exist (A1.3) |
| | `me` | `GET /me` (profile and CSRF token) and `PATCH /me` | Implemented |
| | `ingestion` | `GET /ingestion/capabilities` (A2.2) | Implemented; publishes `null` limits until D4 is closed |
| | `integrations` | `GET /integrations/status`, reads only the DB | Implemented |
| | `audios` | `POST /audios` (A2.3) and `PUT /audios/{id}/content` (A2.4) | Partial: the `PUT` responds 503 with the default sink |
| services | `sessions` | Opaque session (`mw_session`) and CSRF | Implemented |
| | `tenant` | `get_owned`: access filtered by `user_id` (ADR-0006) | Implemented |
| | `audios` | Slot reservation, quota, and versioned consent | Implemented; quota and slot are conditional (D4) |
| | `uploads` | Streaming reception, SHA-256, compensation, and the `ReceptionSink` port | Partial: there is no real sink implementation |
| | `dedupe` | Exact or variant identity and one-time-use token | Implemented; tested with an injected sink |
| | `outbox` | Pure policy: which events are published and the cleanup gate | Implemented |
| workers | `arq_app`, `dispatcher` | `worker` and `scheduler` processes; the dispatcher moves IDs from `outbox_events` to Redis with `FOR UPDATE SKIP LOCKED` | Partial: no ACK nor retries; `index` and `analyze` do nothing |
| core | `config`, `deps`, `db`, `errors`, `validation` | `MAULWURF_*` settings with fail-fast validation, session, CSRF and Origin, async SQLAlchemy, error envelope, and validators | Implemented |
| | `crypto` | AES-GCM with key version | It exists, but no process uses it today |
| models | `core`, `ingestion`, `transcript`, `chunk`, `chat` | S1 and S2 tables (migrations `0001` and `0002`) | Schema implemented; no services use it beyond ingestion |
| schemas | `ingestion` | Capabilities contract | Implemented |
| | `analysis`, `chunk` | Pydantic contracts for extraction and chunks | Draft, unused at runtime |

### 4.2 Ingestion

<!-- svg: arq-04-componentes-ingesta -->
```mermaid
---
title: "Figure 4 · Ingestion components (apps/ingest/maulwurf_ingest)"
config:
  flowchart:
    wrappingWidth: 330
    nodeSpacing: 24
    rankSpacing: 30
    padding: 8
---
flowchart TB
    subgraph W["Wired into the uvicorn process · main:app"]
        direction LR
        m1["main<br/>GET /healthz · GET /readyz"]:::impl
        m2["config<br/>MAULWURF_DATABASE_URL +<br/>INGEST_*"]:::impl
        m3["hardening<br/>rootfs, tmpfs, swap, core<br/>dumps"]:::impl
    end
    subgraph L["Library with tests · main does not import it"]
        direction TB
        sup["supervisor<br/>one attempt: lease,<br/>reception, ASR in subprocess<br/>and cleanup in finally"]:::parcial
        adm["admission<br/>latch: cleanup failed<br/>closes new uploads"]:::parcial
        lea["lease<br/>heartbeat and fencing<br/>per attempt"]:::parcial
        rec["receiver<br/>private file in tmpfs<br/>+ SHA-256"]:::parcial
        val["audio/validate<br/>ffprobe: format,<br/>duration and streams"]:::parcial
        cln["cleanup<br/>kills the group, deletes<br/>and verifies in /proc"]:::parcial
        aus["audio_absence<br/>audio-absence sweep:<br/>tests only"]:::parcial
        job["asr_job<br/>subprocess in its own group<br/>stub: does not call Riva"]:::parcial
        pip["pipeline<br/>one active chunk at a time"]:::parcial
        ffm["audio/ffmpeg + fragmenter<br/>no shell nor network · 30 s ·<br/>overlap 0.25 s"]:::parcial
        rcl["audio/reconcile + timestamps<br/>boundaries and global offsets"]:::parcial
        riv["asr/riva_client<br/>gRPC TLS · up to 3 attempts<br/>asr_job does not use it yet"]:::parcial
        cap["capacity + capabilities<br/>RAM per slot · observed<br/>formats"]:::parcial
        sup --> adm
        sup --> lea
        sup --> rec
        sup --> val
        sup --> cln
        sup -->|"launches"| job
        job --> pip
        pip --> ffm
        pip --> rcl
        pip --> val
        riv --> rcl
        cap -.-> val
    end

    classDef impl fill:#e6f4ea,stroke:#2e7d32,color:#111
    classDef parcial fill:#fff4d6,stroke:#b26a00,color:#111
    style W fill:#fafafa,stroke:#9aa0a6,color:#111
    style L fill:#fafafa,stroke:#9aa0a6,color:#111
```

| Module | Purpose | Status |
|---|---|---|
| `main` | FastAPI app with `/healthz` and `/readyz` (hardening, tmpfs and Postgres); it does not serve if a `hardening` guarantee is missing | Implemented |
| `config`, `hardening` | `MAULWURF_DATABASE_URL` and `INGEST_*` settings; self-check of rootfs, tmpfs, swap and core dumps by reading `/proc` and the cgroup | Implemented |
| `supervisor` | Orchestrates one attempt: lease, reception, ASR in a subprocess and cleanup in `finally`; kills the process group, deletes the directory and verifies in `/proc` | Library |
| `admission`, `lease` | Instance admission latch; lease, heartbeat and fencing of `ingestion_attempts` | Library |
| `receiver` | Streaming reception into a private file for the attempt; SHA-256 per chunk; cuts and deletes when the cap is exceeded | Library |
| `audio/validate` | ffprobe: decides to accept, normalize or reject by container, duration and streams | Library; `m4a` is listed as unverified |
| `audio/ffmpeg`, `audio/fragmenter` | ffmpeg with no shell nor network and with CPU, memory and output limits; chunks of up to 30 s with 0.25 s overlap | Library |
| `pipeline`, `asr_job` | One active chunk at a time; `asr_job` runs as a subprocess and is today a stub without provider calls | Library (stub) |
| `audio/reconcile`, `audio/timestamps` | Boundary and overlap reconciliation; global offsets | Library |
| `asr/riva_client` | Isolated gRPC TLS client; up to 3 attempts with backoff for transient errors | Library; `asr_job` does not use it |
| `cleanup` | Ephemeral directory per attempt and cleanup verification in `/proc`: descriptors, group processes and mounts | Library; used by the `supervisor` |
| `audio_absence` | Audio-absence sweep across disk, Redis, logs, traces and caches; returns only booleans and counts | Tests only: imported by `test_no_audio_persistence.py` |
| `capacity`, `capabilities` | RAM budget per slot; capabilities observed in the spike | Library; D4 remains blocked |
| `audio/synthetic` | Synthetic audio generated in RAM for tests (needs `espeak-ng`) | Tests only |

## 5. Data and Trust Zones

<!-- svg: arq-05-zonas-de-confianza -->
```mermaid
---
title: "Figure 5 · Data and trust zones"
config:
  flowchart:
    wrappingWidth: 330
    nodeSpacing: 24
    rankSpacing: 30
    padding: 8
---
flowchart LR
    subgraph Z0["Client"]
        orig["Original file<br/>student's local copy"]:::ext
    end
    subgraph Z1["Ingestion: 256 MiB tmpfs, no swap"]
        aud["Received audio, normalized<br/>WAV<br/>and chunks: they are deleted and<br/>verified<br/>before enabling the outbox"]:::efimero
    end
    subgraph Z2["Owned stores"]
        direction TB
        pg[("Postgres with pgvector<br/>transcript, segments,<br/>chunks,<br/>embeddings, chat, tasks,<br/>sessions,<br/>Google tokens encrypted<br/>and outbox with IDs only")]:::propio
        bk["Encrypted backups<br/>text and metadata only<br/>30 days proposed"]:::plan
        rd[("Redis without persistence<br/>IDs and version only")]:::propio
    end
    subgraph Z4["Third parties outside our control"]
        direction TB
        riva["NVIDIA Riva<br/>receives the audio in<br/>chunks"]:::ext
        llm["LLM and embeddings<br/>receive text chunks"]:::ext
        goo["Google<br/>OAuth tokens, events and<br/>mail"]:::ext
    end

    orig -->|"streaming PUT,<br/>TLS"| aud
    aud <-.->|"gRPC TLS: chunks<br/>and text with offsets"| riva
    aud -.->|"atomic commit:<br/>transcript and<br/>segments"| pg
    pg -->|"IDs"| rd
    pg -.->|"text"| llm
    pg -.->|"events and mail<br/>after<br/>confirmation or<br/>opt-in"| goo
    pg -.-> bk

    classDef efimero fill:#fff4d6,stroke:#b26a00,color:#111
    classDef propio fill:#e6f4ea,stroke:#2e7d32,color:#111
    classDef plan fill:#f3f3f3,stroke:#777,stroke-dasharray:5 4,color:#111
    classDef ext fill:#e8eefc,stroke:#3b5bdb,color:#111
    style Z0 fill:#fafafa,stroke:#9aa0a6,color:#111
    style Z1 fill:#fafafa,stroke:#9aa0a6,color:#111
    style Z2 fill:#fafafa,stroke:#9aa0a6,color:#111
    style Z4 fill:#fafafa,stroke:#9aa0a6,color:#111
```

| Data | Where it may exist | Where it never does | Third party that receives it |
|---|---|---|---|
| Audio: original, normalized WAV and chunks | `ingest` tmpfs (RAM, no swap) while it is received, validated, converted and transcribed | Disk, S3 or MinIO, Postgres, Redis, logs, traces, caches, backups, CI artifacts | NVIDIA Riva receives the chunks; its retention is unverified (D3-Audio `blocked`) |
| Transcript and segments | Postgres and encrypted backups | Logs, traces, Redis, CI | LLM and embeddings, depending on the process (D3-Text) |
| Chunks, embeddings, chat and tasks | Postgres (pgvector, versioned) | Redis, logs | LLM and embeddings; Google only receives the event of a confirmed task |
| Google tokens | Postgres, encrypted with AES-GCM and `key_version` | Browser, logs, Redis | Google |
| Session | Opaque `mw_session` cookie in the browser; in Postgres only its hash | Tokens issued by the client or the LLM | — |
| Queue messages | Redis, with IDs and version only | Text, tokens, audio | — |

**How the guarantees are enforced**

| Guarantee | Mechanism | Status |
|---|---|---|
| No audio persistence | Swap-free tmpfs, read-only rootfs, `hardening` that prevents serving if a guarantee is missing, `cleanup` with verification in `/proc` and, in tests only, the `audio_absence` sweep | Partial: measures implemented in the container and the library; the full path is not wired |
| No verified cleanup, no index or analysis | Gate in `services/outbox` and dispatcher with `FOR UPDATE SKIP LOCKED` | Implemented (policy and dispatcher) |
| Redis holds IDs only | The dispatcher enqueues only IDs and version; Redis without RDB nor AOF and with `noeviction` (ADR-0005) | Implemented |
| Tenant isolation | `get_owned` on every read, foreign keys with `user_id`, and the A1.9 suite against real Postgres (ADR-0006) | Implemented for the existing routes |
| Identity only from the backend | `core/deps`: session, CSRF and Origin; never the request body or the LLM | Implemented |
| Calendar only with human confirmation | Confirmation transaction, outbox and worker | Planned (F2) |

## 6. Implementation Status and Gaps

| Capability | Status | Evidence |
|---|---|---|
| 8-service Compose stack with healthchecks and quality CI | Implemented | `infra/`, `.github/workflows/quality.yml`; CI green at `2298e1b` |
| Opaque session, CSRF, `GET` and `PATCH /me`, `POST /auth/logout` | Implemented | `test_session_csrf.py`, `test_a18_endpoints.py` |
| Google login | Not implemented | A1.3 without claim; PR #42 proposes a different web stack |
| Subject CRUD | Not in `develop` | A1.6 `in progress`; PR #50 open |
| `POST /audios` and `PUT /audios/{id}/content` | Partial | A2.3 (#51), A2.4 (#52); the default sink responds 503 (C2) |
| Ephemeral ingestion: admission, reception, supervisor, lease, cleanup, ffprobe and ffmpeg, fragmentation, reconciliation and Riva client | Partial: unwired library | S1.B*, S2.1–S2.2 (#54), S2.3–S2.4 (#55, `in progress`); `asr_job` is a stub |
| Transcript commit and post-commit reconciler | Not implemented | S2.5 and A2.10, S2.7 |
| Outbox dispatcher | Partial | J1.5; no ACK nor retries (J2.1) |
| Indexing, hybrid search and chat | Not implemented | L2.x, A2.11 and A2.12; models exist, but no routes or jobs |
| Analysis, review, Calendar, Gmail and account deletion | Not implemented | S3 and S4 remain closed |
| Web | Scaffold | `apps/web/app/page.tsx` is a placeholder; the `D-` area has not started |
| Upload limits (200 MiB and 3 h target) | Not approved | `capabilities` publishes `null`; the Caddyfile sets no body limits or timeouts |

## 7. Open Decisions That Constrain the Architecture

| Decision | Status | Effect on the architecture | Source |
|---|---|---|---|
| D2 Riva contract | `pending` | Allowlist of languages, formats and limits published by `capabilities` | [F0.1 minutes](spike/F0.1-acta-decisiones.md) |
| D3-Audio | `blocked` | Without it, no real classes are accepted (NVIDIA retention and region unverified) | F0.1 minutes |
| D3-Text | No recorded status | LLM and embedding provider; changing it reopens the decision, and embeddings have a fixed dimension in the schema | ESPECIFICACION §12 |
| D4 Capacity | `blocked` | Upload limits, RAM per slot and deadlines | F0.1 minutes |
| D6 Temporality | `pending` (proposal `none`) | Decides `timestamp_precision`, the citation format and SRT | F0.1 minutes |
| D8 Retention and backups | The spec lists it as pending; `plan/S4.md` treats it as approved | Text and chat retention, and backup policy | ESPECIFICACION §12, `plan/S4.md` |
| `PUT` route (C2) | Open | Today `api` receives and the sink responds 503; the spec draws the `PUT` towards `ingest`. The internal contract between them is missing | `infra/Caddyfile`, `services/uploads.py` |
| Web framework | Open | Next.js (specification, ADR-0001 and scaffold) versus Vite and Express (PR #42) | PR #42 |
| Upload limit at the edge | Not analyzed | The proposal uses Cloudflare Tunnel; Cloudflare documents 100 MB (Free and Pro) and 200 MB (Business) per request, against the 200 MiB target. It remains to confirm whether it applies to Tunnel | [Cloudflare, error 413](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/4xx-client-error/error-413/) |

## 8. Maintenance

1. The diagrams live in this file and in [PROCESOS.md](PROCESOS.md) as Mermaid blocks, which
   GitHub renders. Each exportable block is preceded by `<!-- svg: name -->`.
2. After editing a diagram, regenerate the SVGs:

   ```bash
   python3 scripts/docs/export_diagrams.py
   ```

   Requires Node 22 and Chrome or Chromium (variable `CHROME_PATH`). It pins
   `@mermaid-js/mermaid-cli` 12.0.0 (Mermaid 12.1.0) and writes text as `<text>`, without
   `foreignObject`, so the SVGs open in Word, PowerPoint or Inkscape.
3. When a ticket that changes what is implemented is merged, update the colors, the tables and
   the header commit. If a contract changes, update ESPECIFICACION.md and
   PLAN_IMPLEMENTACION.md first.

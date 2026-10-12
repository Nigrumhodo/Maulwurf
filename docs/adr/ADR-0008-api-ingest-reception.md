# ADR-0008 — API↔ingest reception contract: `api` terminates the `PUT` and streams to `ingest`

- Date (UTC): 2026-10-12
- Status: Accepted (decided by the lead, jpinillaz)
- Ticket: A2.13 (paired with S2.11; consumed by J2.8, S2.13, A2.14)
- Closes: open decision C2 of [ARCH.md](../ARCH.md) §7
- Changes: [ESPECIFICACION.md](../ESPECIFICACION.md) §3.1 (diagram) and §M1 ("shared
  dependency"); [PLAN_IMPLEMENTACION.md](../PLAN_IMPLEMENTACION.md) F0.2/F1.1
- Related: [ADR-0005](ADR-0005-redis.md) (no audio in Redis),
  [ADR-0006](ADR-0006-aislamiento-tenant.md) (tenant filtering), `docs/plan/S1.md` §1.4
  (lease and fencing), `docs/plan/tickets/A2.13.md` (implementation plan)

## Context

The binary upload cannot be admitted today:

- `apps/api/app/services/uploads.py` implements the whole public `PUT` (A2.4): session, CSRF
  and Origin checks, ownership, upload window, declared and streamed size cut, incremental
  SHA-256, exact/variant dedupe, compensation, and the `ReceptionSink` port
  (`begin/write/finish/discard/admit`). The only sink is `UnavailableReceptionSink`, which
  answers `503 capacity_unavailable` before reading a byte.
- `apps/ingest` serves only `/healthz` and `/readyz`. Its supervisor (`run_attempt`) acquires
  the lease, moves `awaiting_upload → receiving`, receives, probes, transcribes and cleans up
  in one call, and receives the upload as one `bytes` object (gap S2.11).
- `infra/Caddyfile` routes `PUT /audios/*/content` to `api:8000`; the specification diagram
  draws the upload going from the browser to `ingest`.

S2.md §A2.13 fixes the constraints any answer must meet: a single authority for session,
CSRF and Origin; `ingest` never receives the session cookie, only an internal credential
bound to `attempt_id` and the fencing token; streaming on every hop with no disk and no
whole-file buffer; end-to-end backpressure; one SHA-256 reused by dedupe; a lost connection
cancels the attempt and cleans up; every failure is mapped through the S2.13 catalog.

## Options

| | (a) `api` terminates and streams to `ingest` | (b) Caddy routes the `PUT` to `ingest` |
|---|---|---|
| Session/CSRF authority | Only `api` (already implemented and tested, A1.4) | `ingest` must validate the session (second authority) or a browser-held upload token |
| Dedupe and compensation | Stay in `api` (A2.4 code reused as is) | Move to `ingest` (business logic and API tables in the hardened service) or a callback to `api` |
| Exposure of `ingest` | Internal network only; can leave `edge` | Reachable from the browser through Caddy |
| Cost | Bytes cross one extra internal hop; `api` hashes 200 MiB per upload | One hop less |
| Code change | Implement the sink client (API) and the internal endpoint (ingest) | Rewrite A2.4 in ingest, new browser credential, Caddy route |

Rejected as well: gRPC between the services (new dependency and toolchain for one stream),
and any queue or Redis hop (ADR-0005 and the no-audio rule forbid audio there).

## Decision

**Option (a).** The browser keeps sending `PUT /audios/{audio_id}/content?attempt_id=…` to
`api` through Caddy. `api` authenticates, runs every pre-byte check, and streams the body to
an internal, non-public HTTP endpoint of `ingest` over the Compose `data` network. The
reception is split into two phases so that dedupe stays in `api` and is decided while the
bytes sit only in the attempt's tmpfs:

1. **Receive:** one streaming internal request carries the body. `ingest` takes the lease,
   writes the tmpfs file, hashes, cuts at its limit, probes the format, and answers with its
   hash. It then **holds** the file, heartbeating the lease, until phase 2.
2. **Decide:** `api` compares hashes, runs dedupe, and calls either **admit** (`ingest`
   continues to ASR) or **discard** (`ingest` deletes, verifies cleanup, and writes the
   terminal status).

## Contract

### 1. Topology and transport

- Caller: `api` only. Callee: `ingest` at `MAULWURF_INGEST_URL` (Compose:
  `http://ingest:8000`). Plain HTTP/1.1 on the internal `data` network, no TLS in local and
  CI; a deployed environment needs a private network or mTLS (J4.x).
- Caddy never routes `/internal/*`. The Caddyfile answers `404` for that prefix as defense
  in depth, and `ingest` leaves the `edge` network (J2.8), so Caddy cannot reach it at all.
- Transfer encoding: `Transfer-Encoding: chunked`. `api` does not forward the browser's
  `Content-Length` as a framing header; it sends it, when present, as
  `X-Maulwurf-Declared-Bytes` for logging and early rejection only. Neither side trusts it
  for the cut or the hash.
- Path prefix `/internal/v1/receptions/{attempt_id}`. JSON bodies use
  `application/json`; the stream uses `application/octet-stream`.

### 2. Internal credential

Every internal request carries `Authorization: Bearer <credential>`. The credential is
minted by `api` per operation and never leaves the backend.

- Format: `v1.<payload>.<mac>`, both parts base64url without padding. `mac` is
  HMAC-SHA256 with the shared secret over the ASCII bytes `maulwurf-ingest/v1.<payload>`.
- Payload (canonical JSON, sorted keys):

  | Field | Meaning |
  |---|---|
  | `op` | `receive`, `admit` or `discard` |
  | `att` | `attempt_id` (must equal the path parameter) |
  | `aud` | `audio_id` of the attempt |
  | `uid` | `user_id` taken from the authenticated session |
  | `ft` | fencing token the request is bound to (see below) |
  | `exp` | Unix seconds; `now + lease TTL` (30 s, the existing S1.B3 value) |

- `ft` binding: for `receive`, the attempt's current `fencing_token` as read by `api` under
  the PUT checks; `ingest` acquires only if the row still has exactly that token, then
  increments it (S1.B3). For `admit`/`discard`, the incremented token returned by `receive`.
  A replayed `receive` credential therefore fails after the first acquire, and an
  `admit`/`discard` is accepted only for the reception that the same lease holds.
- Verification in `ingest`: constant-time MAC check, `exp` not past, `att` equals the path,
  and the database row matches `uid`, `aud` and `ft`. Any mismatch answers
  `401 internal_credential_invalid` without touching the row. No `jti` store: the fencing
  token and the status checks make each credential effectively single-use.
- Secret: `MAULWURF_INGEST_CREDENTIAL_SECRET` in `api`, the same value as
  `INGEST_CREDENTIAL_SECRET` in `ingest` (Compose maps one `.env` variable to both). It is
  the only API-side secret `ingest` receives; it grants nothing outside these endpoints.
- The session cookie, CSRF token, Origin, OAuth tokens and encryption keys are never
  forwarded. `api` builds the internal request from scratch; it does not proxy headers.

### 3. Endpoints

#### `PUT /internal/v1/receptions/{attempt_id}` (op `receive`)

1. Verify the credential (§2).
2. Admission gate before the first byte: `admission.uploads_admitted()` is true, a slot is
   free (the published `active_slots`; while D4 is blocked, the single slot S1.B2 measured),
   and the tmpfs check of `/readyz` passes. Otherwise `503 capacity_unavailable`.
3. Acquire the lease with the bound `ft` and move `awaiting_upload → receiving` in the same
   guarded statement. If no row matches: `409 attempt_not_active`.
4. Stream `request.stream()` into the attempt workdir through `receiver.receive` (O_EXCL,
   `0600`, incremental SHA-256, cut at `INGEST_MAX_UPLOAD_BYTES`). Memory is bounded by the
   read block; nothing is buffered beyond it.
5. Probe the file (`probe_file`, with the S2.12 containment).
6. Answer `200`:

   ```json
   {"fencing_token": 8, "sha256": "<64 hex>", "received_bytes": 1234567,
    "duration_s": 3605.2}
   ```

   and keep holding the workdir under the lease (`status` stays `receiving`).

Failures after the acquire always clean up in `finally` and write the terminal status
under the lease before answering:

| Cause | Persisted status / `error_code` | Internal answer |
|---|---|---|
| Size above the ingest limit | `rejected` / `payload_too_large` | `413 payload_too_large` |
| Probe rejects the format | `rejected` / probe `reason` | `415 unsupported_format` + `{"reason"}` |
| ffprobe missing or crashing (`probe_unavailable`) | `requires_reupload` / `probe_unavailable` | `503 capacity_unavailable` |
| Body ends early or the connection drops | `cancelled` / `reception_interrupted` | none (connection gone) |
| tmpfs full, unexpected error | `requires_reupload` / `supervisor_error` | `500 internal_error` |

#### `POST /internal/v1/receptions/{attempt_id}/admit` (op `admit`)

Requires a held reception for that attempt and token. Moves `receiving → transcribing`,
hands the workdir to the supervisor's ASR stage (S2.10), and answers `202
{"status": "transcribing"}`. Repeated or late calls answer `409 reception_not_held`. From
here on the existing supervisor rules apply unchanged (fencing, cleanup in `finally`,
`transcript_committed_cleanup_pending → succeeded` only after verified cleanup).

#### `POST /internal/v1/receptions/{attempt_id}/discard` (op `discard`)

Body `{"reason": "<reason>"}`. Deletes the workdir, verifies cleanup with
`cleanup.destroy_and_verify`, records `cleanup_status`, writes the terminal status, releases
the slot, and answers `200 {"cleanup_status": "verified" | "failed"}`. `failed` also closes
admission (S1.B6). Idempotent: a second call for an already-discarded reception answers
`200` with the recorded `cleanup_status`.

| `reason` (sent by `api`) | Persisted status / `error_code` |
|---|---|
| `duplicate_exact`, `duplicate_variant` | `cancelled` / the reason (then `api` absorbs the reservation) |
| `variant_hash_mismatch` | `rejected` / `variant_hash_mismatch` |
| `client_disconnected` | `cancelled` / `client_disconnected` |
| `reception_integrity_failed` (hash mismatch) | `requires_reupload` / `reception_integrity_failed` |
| `api_error` (DB failure after receive, admit failure) | `requires_reupload` / `api_error` |

#### Hold timeout

If neither `admit` nor `discard` arrives within one lease TTL after the `200` of `receive`,
`ingest` discards on its own, writes `requires_reupload` / `reception_abandoned`, and the
later call answers `409 reception_not_held`. No new number: a hold longer than the lease
would lose the lease anyway.

### 4. Sequence

```mermaid
sequenceDiagram
    participant B as Browser
    participant C as Caddy
    participant A as api
    participant I as ingest
    participant P as PostgreSQL
    B->>C: PUT /audios/{id}/content (cookie, CSRF, Origin)
    C->>A: stream, no buffering
    A->>P: session, ownership, window, status awaiting_upload, fencing_token N
    A->>I: PUT /internal/v1/receptions/{att} (credential op=receive, ft=N), chunked
    I->>P: acquire lease if token = N; token N+1; awaiting_upload → receiving
    loop each chunk, bounded queue
        A->>I: chunk (A hashes as it forwards)
        I->>I: write tmpfs, hash
    end
    I->>I: ffprobe
    I-->>A: 200 {fencing_token N+1, sha256, received_bytes}
    A->>A: compare hashes; dedupe lookup
    alt new content or confirmed variant
        A->>P: write identity (admit_identity)
        A->>I: POST …/admit (op=admit, ft=N+1)
        I->>P: receiving → transcribing
        I-->>A: 202 {status: transcribing}
        A-->>B: 202 {attempt_id, status, received_bytes}
    else duplicate
        A->>I: POST …/discard {reason}
        I->>P: cleanup evidence + cancelled
        I-->>A: 200 {cleanup_status: verified}
        A->>P: absorb reservation
        A-->>B: 200 duplicate_exact / 409 duplicate_variant
    end
```

### 5. State ownership

- From a successful acquire on, **only `ingest` writes `ingestion_attempts.status`,
  `error_code` and `cleanup_*`**, always through the fenced statements of
  `maulwurf_ingest/lease.py`. `api` stops writing `cancelled` on disconnect and `rejected`
  on `variant_hash_mismatch`; it asks for the matching `discard` reason instead.
- `api` keeps writing what is not ingestion state: the audio identity (`sha256`,
  `original_bytes`, `received_bytes`, `expected_bytes`) in `admit_identity`, its
  compensation, variant tokens, and `absorb_reservation`, which deletes the provisional rows
  only **after** a verified `discard` (the row must exist while `ingest` records cleanup).
- The double-`PUT` race of A2.14 is closed by the fenced acquire: the second `receive`
  finds no row with status `awaiting_upload` and token `N` and gets `409
  attempt_not_active`. A2.14 keeps the quota/slot locks and the expiry of stale
  reservations.
- If `ingest` is unreachable after it acquired, the attempt stays with an expiring lease and
  `cleanup_status = pending`; reconciliation is S2.7, as for any lost supervisor.

### 6. Hash

`api` computes the SHA-256 while forwarding; that value is the one dedupe and
`admit_identity` use, so the content is hashed once for the decision and never re-read from
disk. `ingest` computes its own while writing and returns it. A mismatch means the internal
hop corrupted or truncated the stream: `api` calls `discard` with
`reception_integrity_failed` and answers `500 internal_error`. This is the "hash matches on
both sides" criterion of A2.13.

### 7. Backpressure, timeouts, and disconnects

- `api` side: `begin()` opens the internal request whose body is an async generator reading
  a bounded queue (one chunk); `write(chunk)` awaits the queue, so the browser stream is read
  only as fast as `ingest` consumes. Peak memory per upload is a few read blocks, not the
  file. `finish()` closes the generator and awaits the `receive` answer.
- `ingest` side: uvicorn stops reading the socket while `receiver.receive` is writing, so
  TCP flow control propagates back to `api`, Caddy and the browser.
- Timeouts (`api` → `ingest`, httpx): connect 2 s (the existing `CHECK_TIMEOUT_S` of
  ingest), and an idle read/write/pool timeout of one lease TTL. The `receive` answer waits
  for ffprobe, whose own bound comes from S2.12.
- Browser disconnect: `api` gets `ClientDisconnect`, cancels the internal request (closing
  the connection), and, if a `receive` answer already arrived, calls `discard` with
  `client_disconnected`. Without an answer, `ingest` sees the dropped body and writes
  `cancelled` / `reception_interrupted` itself. Either way the browser gets no response and
  the reservation is gone; uploading again needs a new `POST /audios`.
- `api` cuts at its own limit (`effective_upload_limit()`, which may be lower than
  `INGEST_MAX_UPLOAD_BYTES`): same path as a disconnect on the internal hop. `api` aborts the
  internal request and answers `413 payload_too_large`; `ingest` records `cancelled` /
  `reception_interrupted`. The reservation is gone either way (A2.14), so the precise code is
  only in the public answer.
- `api` crash mid-stream: same path as a dropped body. `api` crash during the hold: the hold
  timeout applies.

### 8. Public mapping (input for S2.13)

| Internal answer | Public `PUT` answer |
|---|---|
| `200` receive, hashes equal | continue to dedupe (A2.4 unchanged) |
| `409 attempt_not_active` | `409 attempt_not_active` |
| `413 payload_too_large` | `413 payload_too_large` with `max_bytes` |
| `415 unsupported_format` | `415 unsupported_format` (the internal `reason` is not exposed) |
| `503 capacity_unavailable`, connect error, timeout | `503 capacity_unavailable` + `Retry-After` |
| `401 internal_credential_invalid` | `500 internal_error` (configuration fault; logged, never exposed) |
| `409 reception_not_held` on admit | `503 capacity_unavailable` after identity compensation |
| `discard` → `failed` or error | `500 internal_error` (no `200`/`409` without verified cleanup) |
| anything else | `500 internal_error` |

New persisted codes for the S2.13 catalog: `reception_interrupted`, `reception_abandoned`,
`reception_integrity_failed`, `client_disconnected`, `api_error`, `duplicate_exact`,
`duplicate_variant`. `probe_unavailable` moves from `rejected` to `requires_reupload`.

### 9. Configuration

| Variable | Service | Default | Effect when unset |
|---|---|---|---|
| `MAULWURF_INGEST_URL` | `api` | none | `get_reception_sink()` keeps `UnavailableReceptionSink` (503) |
| `MAULWURF_INGEST_CREDENTIAL_SECRET` | `api` | none | same; outside `local` a `change-me` value fails fast |
| `INGEST_CREDENTIAL_SECRET` | `ingest` | none | internal endpoints answer `503 capacity_unavailable`; `/readyz` reports `credential` |

Both secrets are set together or neither (like the Google pair). J2.8 passes `ingest` an
explicit list of variables, never the whole `.env`.

### 10. Logs and privacy

Internal requests log only attempt id, operation, outcome code, byte count and duration.
Never the credential, hashes of private content in clear next to the user id, headers, or
any byte of the body. No access log of `ingest` records the `Authorization` header.

### 11. Multiple `ingest` instances

The held file lives in one container's tmpfs, so `admit`/`discard` must reach the instance
that answered `receive`. With one instance (Compose, S2) this is automatic. Before scaling,
`receive` must return the instance address and `api` must pin phase 2 to it (J4.x); until
then running more than one `ingest` replica is unsupported.

## Consequences

- The A2.4 code stays: A2.13 adds `IngestReceptionSink` behind the existing protocol and
  changes the two direct status writes in `uploads.py` into `discard` reasons.
- `ingest` gains three internal endpoints and a hold phase (S2.11); `run_attempt` is split
  into receive, hold and admit/ASR stages and stops taking `bytes`.
- Every upload costs one extra internal hop and one hash in `api`; accepted for a single-host
  MVP. F0.2 capacity is measured for `ingest`; `api` memory stays bounded by the queue.
- The specification no longer describes a session dependency "shared by API and ingestion":
  `api` is the only session authority, and `ingest` trusts only the internal credential.

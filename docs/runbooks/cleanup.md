# Cleanup and recovery runbook (S4.1)

> **Ticket:** S4.1 (`MAULW-149`) · **Depends on:** S2.6, S2.7 · **Test:** I-S4-SG-03.
> **Review:** an active Infra claim or the lead. The PR stays open until that review and
> the reference-environment drill are recorded.
>
> This is an executable procedure, not a proposal. Every step states the command and the
> result that proves it. Where a step cannot be executed in the development sandbox, the
> step and its verification table say so explicitly instead of implying it ran.
>
> References: [S4 detailed specification](../plan/S4.md), [development
> runbook](../RUNBOOK-dev.md), [specification](../ESPECIFICACION.md).

## 1. What this runbook protects

The ingest contract is **no persistent audio**:

- bytes live only in the per-attempt directory under the ingest tmpfs;
- the supervisor destroys the directory in `finally` and verifies the result in `/proc`
  (`workdir_absent`, `tmp_is_tmpfs`, `live_processes`, `open_descriptors`,
  `mounts_under_workdir`, `unreadable_processes`);
- only verified evidence writes `cleanup_status = 'verified'`, `cleanup_verified_at` and
  `audio_deleted_at`;
- a persisted `cleanup_status = 'failed'` closes the instance admission latch and emits
  `cleanup.failed` plus `ingest.alert code=cleanup_failed`;
- nothing in this procedure retranscribes, replays, copies or downloads audio.

The source of truth for the failure path is
`apps/ingest/maulwurf_ingest/cleanup.py`, `admission.py` and `supervisor.py`; the recovery
path is `apps/ingest/maulwurf_ingest/recovery.py`.

## 2. Implemented behavior and the known gap

| Behavior | Implemented now | Where |
|---|---|---|
| Failed cleanup closes admission | Yes. The supervisor records `failed`, calls `admission.halt`, logs `cleanup.failed` and `ingest.alert`, increments `maulwurf_ingest_cleanup_failed_total` | `supervisor.py`, `admission.py`, `cleanup.py` |
| Admission stays closed across restart | Yes. Startup reads a persisted `cleanup_status = 'failed'` and closes the latch again | `admission.restore_from_database` in `main.py` |
| One recovery pass at startup | Yes. `reconcile_committed` then `sweep_stranded` | `main.reconcile_on_startup` |
| Recurring 60 s sweeper inside the service | **No.** The startup pass does not loop; embedding the timer in the supervisor is S4.2 | `main.py` says so explicitly |
| 60 s sweeper as an operator process | Yes. `sweep --watch --interval 60` runs the same two functions | `recovery.py` |
| Host-loss attestation | Yes, as an operator attestation; the tool refuses to write without it | `recovery.resolve_attempts` |
| Host-bound attestation | Yes. `resolve` requires `INGEST_HOST_ID`/`--expected-host`; a missing identifier or a different host is refused before any database access | `recovery.resolve_attempts` |

The CLI is available from a checkout as `scripts/ops/cleanup_recover.py`; inside the
hardened ingest container, every command below uses
`python -m maulwurf_ingest.recovery` so nothing is copied into the read-only filesystem or
the audio tmpfs.

**The gap matters:** a cleanup failure discovered by the external sweeper writes the failed
row and alerts, but the running service's in-memory latch is not changed by another
process. The recovery procedure therefore restarts `ingest` immediately after a sweeper
pass reports `cleanup_failed > 0`. Until S4.2 moves the sweeper into the supervisor, this
is the documented containment; do not describe the service as having an automatic 60 s
sweeper.

### Exit codes

| Code | Meaning |
|---|---|
| `0` | The command completed and no attention is required |
| `2` | Usage, configuration, evidence or database error; inspect the output before retrying |
| `3` | `status --check` found a recovery candidate, `sweep` saw a cleanup failure, or `resolve` refused at least one attempt |

## 3. Roles, access and privacy rules

- **Executor:** an operator with access to the compose host and the incident record.
- **Reviewer:** an active Infra claim or the lead. The reviewer executes or witnesses the
  reference-environment drill, verifies every destruction-evidence fact against the
  provider record, and signs the record.
- **Security/privacy reviewer:** confirms any destruction fact the provider cannot prove
  (snapshot, swap, memory image, checkpoint) and owns the stop decision that keeps the text
  outbox blocked.
- Run commands from `infra/` unless a step says otherwise. Use UTC for every timestamp.
- **Host identity:** every ingest host has a stable `INGEST_HOST_ID`. `resolve` accepts an
  attestation only when its `host` exactly matches `--expected-host` (which defaults to
  `INGEST_HOST_ID`). The expected host is the host that owned the attempt, taken from the
  deployment configuration or the incident record, **never from the evidence file**. With
  no expected host the command fails closed (`expected_host_not_configured`); another host
  is `host_mismatch`.
- **Local-directory precondition:** steps 5.x run on the affected host, after 5.1 restarted
  the ingest service there. `--tmp-dir` and `local_attempt_dirs` inspect only the tmpfs of
  the process that executes the CLI; from another host or checkout the check passes without
  testing the affected tmpfs. In a real host loss the new host's empty tmpfs proves nothing
  (7.1).
- Never `cat`, `strings`, `cp`, `tar`, `docker cp`, screenshot or attach a file from
  `/work/tmp`. Only counts, booleans and attempt UUIDs leave the tmpfs.
- Never put a transcript, audio fragment, provider payload, token, DSN or password in a
  log, ticket, evidence file or command line.
- The evidence file is **outside the repository** and mode `0600`. It contains only the
  fixed fields from Appendix A.

## 4. Detection: failed cleanup, admission halted, alert

### 4.1 Confirm the stack is up

```bash
cd infra
docker compose ps --format 'table {{.Service}}\t{{.Status}}'
```

Expected: `postgres`, `redis`, `ingest`, `api`, `worker`, `scheduler`, `web` and `caddy`
are `running`/`healthy`; `ingest` is `healthy`. A restart loop is itself an incident; do
not continue to a write step.

### 4.2 Read the alert

```bash
docker compose logs --since 24h --no-color ingest \
  | grep -E 'cleanup\.failed|ingest\.alert code=cleanup_failed'
```

Expected: at least one `cleanup.failed attempt_id=<uuid>` line and at least one
`ingest.alert code=cleanup_failed attempt_id=<uuid>` line, with the same UUID. The line is
JSON; the evidence booleans are in its `fields` and the message contains no path or
content. Record the UUID for the rest of the procedure:

```bash
export ATTEMPT_ID='<the alert UUID>'
```

### 4.3 Read the instance metrics

```bash
docker compose exec -T ingest python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/internal/metrics', timeout=3).read().decode())" \
  | grep -E '^maulwurf_ingest_(admission_open|cleanup_failed_total|temporary_oldest_age_seconds|tmpfs_free_bytes|tmpfs_total_bytes) '
```

Expected:

```text
maulwurf_ingest_admission_open 0
maulwurf_ingest_cleanup_failed_total 1
```

The counter is per process, so a restart resets it; the persisted failed row is the durable
signal. `temporary_oldest_age_seconds` and the tmpfs bytes are informational at this point.

### 4.4 Read the persisted state

```bash
docker compose exec -T postgres sh -c \
  'PGPASSWORD="$POSTGRES_PASSWORD" psql -U maulwurf -d maulwurf -At' <<'SQL'
SELECT id, status, cleanup_status, cleanup_verified_at IS NULL, audio_deleted_at IS NULL,
       heartbeat_at, lease_expires_at
  FROM ingestion_attempts
 WHERE cleanup_status = 'failed'
 ORDER BY updated_at;
SQL
```

Expected: one row per alert UUID with `cleanup_status = failed` and both `IS NULL` checks
`t`. `lease_expires_at` may still be up to 30 seconds in the future; wait for it before the
attestation (5.3). No audio, transcript or path column is selected.

The cleanup evidence itself is booleans and counts, never a path:

```bash
docker compose exec -T postgres sh -c \
  'PGPASSWORD="$POSTGRES_PASSWORD" psql -U maulwurf -d maulwurf -At' <<'SQL'
SELECT cleanup_evidence
  FROM ingestion_attempts
 WHERE cleanup_status = 'failed'
 ORDER BY updated_at;
SQL
```

Expected: a JSONB object with `verified false` and a failing counter (for example
`open_descriptors 1`) or a redacted error type. If it contains a path, transcript or audio
bytes, stop and treat it as a privacy incident; do not copy it into the incident record.

### 4.5 Run the read-only recovery check

```bash
docker compose exec -T ingest python -m maulwurf_ingest.recovery status --check
echo "exit=$?"
```

Expected:

```text
admission_expected=closed
attention_required=true
failed_cleanups=1
failed_attempt_ids=<the alert UUID>
local_attempt_dirs=<count; may be non-zero before 5.1>
tmpfs_is_tmpfs=true
```

and `exit=3`. If `tmpfs_is_tmpfs=false`, the attempt directory is not on a tmpfs;
stop: the hardening contract is not met. This command only reads attempt metadata and the tmpfs counts.

`admission_expected` is what a restart will restore from the database. The running
process's actual latch is the `maulwurf_ingest_admission_open` metric; if it is `0` while
`failed_cleanups=0`, the startup database read failed (`admission.restore_failed`) and the
process must be restarted after the database is healthy. Do not write an attestation in
that branch.

### 4.6 Contain

- Do not restart yet: first record `ATTEMPT_ID`, the alert lines and the evidence booleans.
- Do not upload again; the instance answers `503 capacity_unavailable` while the latch is
  closed (the API maps the unavailable sink rather than storing bytes).
- Do not copy the attempt directory. The next step destroys it.

## 5. Recover a failed cleanup on a reachable host

The failed verification means the audio's destruction is **not proven**. The proof is a
destroyed memory/mount plus an operator attestation. In compose, force-recreating the
ingest container destroys its `/work/tmp` tmpfs and its process memory; the new container
gets a new empty tmpfs. The PostgreSQL volume is untouched.

**Precondition:** execute 5.3–5.6 on the affected host, after 5.1 restarted the ingest
service there, with `--expected-host` set to that host's `INGEST_HOST_ID`. `resolve` only
sees the tmpfs of the process that runs it; running it from a different checkout or host
does not test the affected tmpfs and is not this procedure.

**TOCTOU note:** `resolve` checks the local attempt directories before the `UPDATE`; the
`UPDATE` re-checks the lease, not the directory. This is accepted because the expired
lease is the invariant that rules out a live writer: a process whose lease expired cannot
heartbeat, transition or record cleanup, and a reclaim bumps the fencing token. Run
`resolve` only after 5.1's restart and after the lease has expired (5.3).

### 5.1 Destroy the tmpfs and memory

```bash
docker compose up -d --force-recreate --wait ingest
docker compose ps ingest
```

Expected: `ingest` is `healthy` and `maulwurf_ingest_admission_open` is still `0` (the
persisted failed row re-closes the restarted process). The old container and its
tmpfs/memory are gone.

Confirm the new container has no attempt directory:

```bash
docker compose exec -T ingest sh -c \
  'find /work/tmp -maxdepth 1 -name "attempt-*" -printf "." | wc -c'
```

Expected: `0`. The command prints one dot per matching directory, never the directory
name or content.

### 5.2 Prove the memory/mount facts

```bash
container=$(docker compose ps -q ingest)
docker inspect --format '{{json .HostConfig.Tmpfs}}' "$container"
docker inspect --format '{{json .Mounts}}' "$container"
docker inspect --format 'readonly={{.HostConfig.ReadonlyRootfs}} memory={{.HostConfig.Memory}} memory_swap={{.HostConfig.MemorySwap}}' "$container"
docker compose exec -T ingest sh -c 'cat /sys/fs/cgroup/memory.swap.max'
```

Expected:

- `.HostConfig.Tmpfs` contains `/work/tmp` with `noexec,nosuid,nodev` and a `size=`;
- `.Mounts` has no `/work/tmp` volume or bind (the tmpfs is not durable storage);
- `readonly=true` and `memory == memory_swap` (zero swap allowance);
- `/sys/fs/cgroup/memory.swap.max` prints `0`.

If the tmpfs is missing, a `/work/tmp` volume appears, the root filesystem is writable, or
the swap value is not `0`: **stop**. The destruction is not proven; escalate to the
security/privacy reviewer and keep admission closed.

### 5.3 Record the attestation instant

```bash
docker inspect --format '{{.State.StartedAt}}' "$(docker compose ps -q ingest)"
date -u +%Y-%m-%dT%H:%M:%SZ
```

Expected: an RFC 3339 UTC timestamp later than the failed row's `heartbeat_at` and
`updated_at`. Use it as `destroyed_at` in Appendix A. The attestation instant is the new
container's start (the old mount is already destroyed); it is not a claim that the new
container's clock is the forensic event time.

Wait until the failed row's lease has expired (the lease TTL is 30 s, `LEASE_TTL_S` in
`receptions.py`):

```bash
sleep 35
docker compose exec -T postgres sh -c \
  'PGPASSWORD="$POSTGRES_PASSWORD" psql -U maulwurf -d maulwurf -At' <<SQL
SELECT lease_expires_at <= now() OR lease_expires_at IS NULL
  FROM ingestion_attempts
 WHERE id = '$ATTEMPT_ID';
SQL
```

Expected: `t`. If `f`, wait and repeat; `resolve` refuses a live lease.

### 5.4 Write the destruction evidence

Create the file outside the repository, mode `0600`, with exactly these fields (Appendix A
has the schema):

```bash
export EVIDENCE_FILE='/secure/incidents/INC-2026-10-31-01/destruction.json'
install -m 600 /dev/null "$EVIDENCE_FILE"
cat > "$EVIDENCE_FILE" <<'JSON'
{
  "host": "maulwurf-ingest-1",
  "destroyed_at": "<timestamp from 5.3, e.g. 2026-10-31T12:34:56Z>",
  "memory_destroyed": true,
  "tmpfs_mount_destroyed": true,
  "no_snapshots": true,
  "no_swap": true,
  "reviewer": "<active Infra claim or lead handle>",
  "reference": "INC-<date>-<number>"
}
JSON
```

Replace every `<...>` placeholder first; `python3 -m json.tool` only checks JSON syntax,
while the resolver rejects a non-ISO `destroyed_at`. Expected: `python3 -m json.tool
"$EVIDENCE_FILE"` exits `0`. The tool refuses unknown fields, missing fields, non-true
booleans, a timezone-less or future `destroyed_at`, and files larger than 8 KiB. Do not add
a `notes` field or any free text.

`host` must be the stable identifier of the host that owned the attempt: `INGEST_HOST_ID`
on a live host, or the destroyed host's identifier from the incident record in section 7.
It must match the `--expected-host` passed in 5.5/5.6 exactly. Do not derive the expected
host from the evidence file.

### 5.5 Dry-run the attestation

The evidence is streamed to the container on stdin and is never written into the image or
the audio tmpfs:

```bash
docker compose exec -T ingest python -m maulwurf_ingest.recovery resolve \
  --attempt "$ATTEMPT_ID" \
  --expected-host maulwurf-ingest-1 \
  --evidence-file /dev/stdin \
  < "$EVIDENCE_FILE"
```

`--expected-host` is explicit here; inside the container it defaults to `INGEST_HOST_ID`
when that variable is configured. Without either, every attempt is refused with
`expected_host_not_configured` (fail closed).

Expected:

```text
evidence_sha256=<64 lowercase hex characters>
evidence_host=maulwurf-ingest-1
expected_host=maulwurf-ingest-1
evidence_destroyed_at=2026-10-31T12:34:56+00:00
applied=false
attempt=<ATTEMPT_ID> action=would_verify status=transcript_committed_cleanup_pending cleanup_status=failed
```

If the decision is `refused`, read its `reason`: `host_mismatch` (the evidence names a
different host than `--expected-host`; stop and fix the incident record, do not edit the
file to match), `expected_host_not_configured` (set `--expected-host` or `INGEST_HOST_ID`;
do not proceed without one), `live_lease` (wait and repeat 5.3),
`local_attempt_directory_present` (the tmpfs still has the attempt; repeat 5.1),
`not_covered_by_evidence` (the attempt was created after the attested destruction, or its
last heartbeat/update is later than the destruction plus the one-minute clock-skew margin),
`no_audio_received` (nothing to attest), or `already_resolved` (nothing to do).

### 5.6 Apply, then let the sweeper release the text

```bash
docker compose exec -T ingest python -m maulwurf_ingest.recovery resolve \
  --attempt "$ATTEMPT_ID" \
  --expected-host maulwurf-ingest-1 \
  --evidence-file /dev/stdin \
  --apply \
  < "$EVIDENCE_FILE"
```

Expected: `attempt=<ATTEMPT_ID> action=verified ... cleanup_status=verified` and exit `0`.
The stored `cleanup_evidence` also records `expected_host`, so the accepted host pair is
auditable. A host refusal leaves the row untouched.

```bash
docker compose exec -T ingest python -m maulwurf_ingest.recovery sweep
echo "sweep_exit=$?"
```

Expected: `pass=1` and `sweep_exit=0`. If the failed attempt was committed
(`status=transcript_committed_cleanup_pending`), `enabled` is at least `1`; a stranded
failed attempt has no text, so `enabled=0` and there is nothing to release. `cleanup_failed`
must be `0`. `enabled` is the number of committed attempts whose index/analyze
outbox rows were released after verified cleanup. A `cleanup_failed` value other than `0`
means the sweep found another failure: restart `ingest` immediately and return to 4.1.

### 5.7 Restart to reload the latch, then verify

```bash
docker compose up -d --force-recreate --wait ingest
docker compose exec -T ingest python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/internal/metrics', timeout=3).read().decode())" \
  | grep -E '^maulwurf_ingest_admission_open '
docker compose exec -T ingest python -m maulwurf_ingest.recovery status --check
echo "exit=$?"
```

Expected: `maulwurf_ingest_admission_open 1`, `admission_expected=open`,
`attention_required=false` (when this incident was the only one), `exit=0`.

Verify the durable row and the released text without selecting content:

```bash
docker compose exec -T postgres sh -c \
  'PGPASSWORD="$POSTGRES_PASSWORD" psql -U maulwurf -d maulwurf -At' <<SQL
SELECT status, cleanup_status, cleanup_verified_at IS NOT NULL,
       audio_deleted_at IS NOT NULL, cleanup_evidence->>'source'
  FROM ingestion_attempts
 WHERE id = '$ATTEMPT_ID';
SELECT count(*)
  FROM transcripts t
  JOIN ingestion_attempts ia
    ON ia.user_id = t.user_id AND ia.audio_id = t.audio_id
 WHERE ia.id = '$ATTEMPT_ID';
SELECT o.type, o.enabled, o.blocked_reason
  FROM outbox_events o
  JOIN ingestion_attempts ia
    ON ia.user_id = o.user_id AND ia.audio_id = o.resource_id
 WHERE ia.id = '$ATTEMPT_ID'
   AND o.type IN ('index_requested', 'analyze_requested')
 ORDER BY o.type;
SQL
```

Expected: for a committed failed attempt, `succeeded | verified | t | t |
operator_attestation`, exactly one transcript (the commit is not repeated), and the outbox
rows `analyze_requested | t |` and `index_requested | t |` with a `NULL` reason. For a
stranded failed attempt the status stays `requires_reupload`, the transcript count is `0`
and there are no outbox rows to release. A `transcript_committed_cleanup_pending` status
means the sweeper has not enabled the text yet; run 5.6's sweep again. No transcript text is
selected.

## 6. The 60 s sweeper

```bash
docker compose exec -T ingest python -m maulwurf_ingest.recovery sweep \
  --watch --interval 60
```

Expected: one `pass=1 ...` line immediately, then one line every 60 seconds. `Ctrl+C`
stops it. The interval is enforced at a minimum of 60 s by `run_sweeper`; there is no
hidden retry loop or jitter. `--interval` is validated and used only with `--watch`: a
single pass runs once and ignores it, so `sweep` without `--watch` is valid even if no
interval is supplied. Each pass uses the real `reconcile_committed` and `sweep_stranded`
functions: it never runs ASR, never retranscribes, never steals a live lease, and never
retries a failed cleanup.

If a pass reports `cleanup_failed` other than `0`, the process stops with exit `3`. The
failed row is persisted and the alert is in the sweeper's output, but the **running
service's** latch is not changed by this process: immediately run
`docker compose up -d --force-recreate --wait ingest` and return to step 4. This is the
known S4.2 gap from section 2.

Run a bounded drill with two passes when no full 60 s wait is needed:

```bash
docker compose exec -T ingest python -m maulwurf_ingest.recovery sweep \
  --watch --interval 60 --max-passes 2
```

Expected: `pass=1 ...` then `pass=2 ...`, roughly 60 seconds apart, and exit `0` when both
passes report `cleanup_failed=0`.

## 7. Host loss: restoration

A lost host takes its memory and its tmpfs with it. The database survives. Restoration has
two independent requirements:

1. the committed text is durable in PostgreSQL, so it can be indexed without the audio;
2. the old host's memory/mount is **proven destroyed**, so the audio is gone.

Requirement 2 is an attestation about infrastructure, not something this repository can
inspect after the host is gone. For the reference deployment, J4.7 must provide the
provider evidence: the instance was terminated (not paused or hibernated), the memory was
destroyed, the tmpfs vanished, there is **no snapshot or image of the instance created
after the upload**, there is **no swap** (and no page file, memory dump or core dump), and
there is no container checkpoint/export. If any of those is not proven, **stop**: keep the
text outbox blocked, treat the audio as possibly retained, escalate to security/privacy,
and require a reupload for that class. Never mark cleanup verified "because the host is
gone" alone.

For a local compose drill, the force-recreate in 5.1 is the equivalent destruction event;
for a real host loss, the provider's termination record supplies `destroyed_at`.

### 7.1 Start the new host

Bring the stack up on the new host with the durable PostgreSQL volume (or the approved A4.5
restore) and wait for health:

```bash
cd infra
docker compose up -d --wait
docker compose ps --format 'table {{.Service}}\t{{.Status}}'
```

Expected: all services healthy; the new ingest tmpfs is empty
(`find /work/tmp -maxdepth 1 -name "attempt-*" -printf "." | wc -c` prints `0`).

The ingest startup already runs one recovery pass. It cannot prove the old tmpfs is gone,
so a committed attempt with `cleanup_status = pending` and no local directory is left
`held`; a stranded `receiving`/`transcribing` attempt is ended as
`requires_reupload` with `technical_loss`. Neither is retranscribed.

**Residual risk: the empty local tmpfs is not evidence.** After a real host loss,
`local_attempt_dirs=0` is expected because the new host starts with an empty tmpfs; it does
**not** prove that the old host's audio is gone, and the
`local_attempt_directory_present` refusal cannot fire in this path. The safety of `resolve`
therefore rests on two other controls: the destruction attestation (memory and tmpfs
destroyed, no snapshot/image created after the upload, no swap, dump or checkpoint) and the
expired lease. The **active Infra claim or lead verifies the evidence** against the provider
record and signs the incident; the **security/privacy reviewer** must confirm any fact the
provider cannot prove. If a fact remains unproven, keep the text outbox blocked and require
a reupload. Do not treat an empty directory listing as proof.

**Expected host in this path.** `resolve` runs on the replacement host, but `--expected-host`
must name the **destroyed host that owned the attempts**, not the new host's identifier and
never a value copied from the evidence file. If the replacement reuses the old logical
identifier as its `INGEST_HOST_ID`, record that decision in the incident before attesting.
The local-directory check is vacuous here; the attestation and the expired lease are the
only controls.

### 7.2 List the attempts that need the attestation

```bash
docker compose exec -T ingest python -m maulwurf_ingest.recovery status
```

Expected: the failed and committed-pending UUIDs. `committed_pending_ids` includes rows
whose cleanup is already verified; those need no attestation and the sweeper releases them
(`resolve` reports `already_resolved`). Attempts already swept to
`requires_reupload` no longer appear in `stranded_ids`; find them with the incident window
(`$LOSS_START` is the last healthy check, `$DESTROYED_AT` comes from the provider record):

```bash
export LOSS_START='<last healthy check, e.g. 2026-10-31T10:00:00Z>'
export DESTROYED_AT='<provider termination time, e.g. 2026-10-31T12:34:56Z>'
docker compose exec -T postgres sh -c \
  'PGPASSWORD="$POSTGRES_PASSWORD" psql -U maulwurf -d maulwurf -At' <<SQL
SELECT id, status
  FROM ingestion_attempts
 WHERE cleanup_status = 'pending'
   AND status <> 'awaiting_upload'
   AND COALESCE(heartbeat_at, updated_at) BETWEEN '$LOSS_START' AND '$DESTROYED_AT'
 ORDER BY updated_at;
SQL
```

Expected: only the UUIDs of attempts that had a live lease during the incident window. If
the window is uncertain, widen it and inspect the statuses; do not guess a UUID.

### 7.3 Attest the old host's destruction

Write the Appendix A file with the provider's `destroyed_at`, `host`, reviewer and
incident reference, and the four destruction facts `true`. The evidence `host` and
`--expected-host` must both name the destroyed host that owned the attempts:

```bash
export LOST_HOST_ID='<the destroyed host identifier from the incident record>'
```

Then copy the file into the container and dry-run/apply the same way as 5.5–5.6, passing
every UUID from 7.2:

```bash
docker compose exec -T ingest python -m maulwurf_ingest.recovery resolve \
  --attempt "$ATTEMPT_ID" \
  --expected-host "$LOST_HOST_ID" \
  --evidence-file /dev/stdin \
  < "$EVIDENCE_FILE"
```

Repeat `--attempt` once for every UUID from 7.2; the dry run prints one decision per
attempt. Expected: every decision is `would_verify`; no `refused`. Then repeat with `--apply`, run
one `sweep`, and verify as in 5.7. A committed attempt becomes `succeeded` with one
transcript and enabled outbox rows; a stranded attempt stays `requires_reupload` (no text)
with `cleanup_status = verified` and `audio_deleted_at` set.

### 7.4 What is not verified here

The provider-termination, snapshot and swap evidence belongs to the reference deployment
(J4.7) and cannot be produced from this repository or its development sandbox. Until J4.7
and the reviewer record it, host-loss restoration in the reference environment is **NOT
VERIFIED**. The local compose drill is the only executable equivalent in this revision.

## 8. Verification record (I-S4-SG-03)

| Check | Command | Result in this revision |
|---|---|---|
| Unit behavior of the runbook tool | `cd apps/ingest && uv run pytest tests/test_cleanup_recovery.py -m "not integration" -q` | 25 passed |
| Unit suite of the area | `cd apps/ingest && uv run pytest -m "not integration and not provider and not load" -q --ignore=tests/test_synthetic_audio.py` | 236 passed. Without `--ignore`, the 8 `test_synthetic_audio.py` cases fail only because `espeak-ng` is absent in this sandbox; CI installs it |
| Ruff | `cd apps/ingest && uv run ruff check .` | pass |
| Mypy | `cd apps/ingest && uv run mypy .` | pass |
| Documentation paths | `python3 scripts/docs/check_refs.py` | pass |
| I-S4-SG-03 integration | `cd apps/ingest && uv run pytest -m integration -q tests/test_cleanup_recovery.py` | Local dispatch smoke with PostgreSQL 16.15: 8 passed (the host-mismatch and missing-host cases included). Quality CI run `41255292952`, job `integration (ingest)`: **64 passed**, 11 skipped, 1 xfailed; the 6 cases in that run are included |
| Quality CI | all jobs in `.github/workflows/quality.yml` | Run `41255292952` (head `8f9676c`): all 17 checks green, including `python-unit (ingest)` 233 passed and `integration (ingest)` 64 passed. See section 8.1; the fix2 run is recorded after the push |
| Compose detection/recovery drill | steps 4–6 | **NOT RUN HERE**: this sandbox has no `docker`, `podman`, `docker-compose` or `/var/run/docker.sock`; the reviewer must execute it on the reference stack |
| Host-loss restoration | section 7 | **NOT RUN HERE**: needs J4.7 provider evidence and a real host; the reviewer records it |

The integration cases use the real migrated PostgreSQL schema and the real
`run_attempt`/`reconcile_committed`/`sweep_stranded` functions; the Quality CI job above ran
them as root against PostgreSQL. The host-loss test uses a synthetic attestation file; it
proves the tool's refusal/acceptance contract, not that a real host's memory was destroyed.
The reviewer's reference-environment drill is the remaining evidence for the ticket's
"executed as-is" requirement; until then the claim stays `in progress`.

### 8.1 Quality CI on the fix head

Run `41255292952` (workflow `quality`, head `8f9676c`, after the ADR-0004 reword and the
develop sync) is green in all 17 checks, including `python-unit (ingest)` **233 passed**
and `integration (ingest)` **64 passed** with the 6 I-S4-SG-03 cases. The local commands and the environment check are captured in
`docs/sprints/artifacts/S4.1/local-gates.txt` and
`docs/sprints/artifacts/S4.1/drill-environment.txt`. The only later commit is this evidence update; PR #135 links
its own `quality` check and no code changes follow.

## 9. Stop conditions

Stop and escalate to the security/privacy reviewer, keeping admission closed, when:

- the evidence file cannot show memory **and** tmpfs destruction, no snapshots and no swap;
- the failed row's `cleanup_evidence` contains a path, transcript, audio or secret;
- `resolve` reports `host_mismatch` (the attestation names another host) or
  `expected_host_not_configured` (no host identity is configured); do not bypass either by
  editing the evidence file, the environment or the database;
- `resolve` refuses a row and the reason is not one of the transient conditions in 5.5;
- a second cleanup failure appears during the same incident;
- the new host's PostgreSQL volume is not the approved durable copy.

Do not "fix" a refusal by editing the database by hand. Do not delete the failed row. Do
not retry the upload as a workaround: the contract requires reupload when ephemeral audio
is lost.

## 10. Review checklist (active Infra claim or lead)

- [ ] Section 2 is accurate against the current `apps/ingest` code (no claimed 60 s
      in-service sweeper).
- [ ] Steps 4.1–4.5 were executed on the reference stack; the alert, metrics and
      `status --check` outputs are attached to the incident record.
- [ ] Step 5 was executed (or witnessed) and the evidence file has the Appendix A fields.
- [ ] `--expected-host` named the host that owned the attempts and matched the incident
      record; no attestation was accepted without it.
- [ ] A failure path was rehearsed at least once; the failed row was recovered and the
      text outbox released without a second transcript.
- [ ] Section 7 evidence includes the provider termination/no-snapshot/no-swap facts.
- [ ] No audio, transcript, secret or path appears in the record, logs or PR.
- [ ] Reviewer handle, date, environment and command results are recorded.

## Appendix A — Destruction evidence schema

```json
{
  "host": "maulwurf-ingest-1",
  "destroyed_at": "2026-10-31T12:34:56Z",
  "memory_destroyed": true,
  "tmpfs_mount_destroyed": true,
  "no_snapshots": true,
  "no_swap": true,
  "reviewer": "active-infra-or-lead-handle",
  "reference": "INC-2026-10-31-01"
}
```

Rules enforced by `load_evidence`:

- exactly the eight fields above; no `notes` and no extra keys;
- `host`, `reviewer` and `reference` are 1–200 printable characters, no control characters;
- `host` must exactly match the `--expected-host` passed to `resolve` (or
  `INGEST_HOST_ID`); a different value is `host_mismatch` and no expected host is
  `expected_host_not_configured`, both without a database write;
- `destroyed_at` is an ISO 8601 instant with a timezone, not more than 5 minutes in the
  future;
- the four booleans are exactly `true`;
- the file is at most 8 KiB; its SHA-256 is stored in `cleanup_evidence` so the incident
  record can link the exact attestation.

The tool stores only those fields plus `source = operator_attestation`, `verified = true`,
`evidence_sha256`, `expected_host` and `recorded_at`. It never stores the file path or free
text. The persisted `cleanup_verified_at` and `audio_deleted_at` are the attestation record
time; the destruction instant is `cleanup_evidence.destroyed_at`, because the evidence is
reviewed after the event.

## Appendix B — No-audio checklist for every step

- [ ] No `cat`, `strings`, `xxd`, `ffprobe`, `ffmpeg`, `docker cp`, `tar` or screenshot of
      `/work/tmp`.
- [ ] Only `find ... -printf "." | wc -c` counts, `/proc` booleans and UUIDs leave the
      tmpfs.
- [ ] The incident record links the command output, not an audio or transcript file.
- [ ] The evidence file lives outside the repository and is mode `0600`; it is streamed on
      stdin and never written into the container or the audio tmpfs.
- [ ] The final database queries select counts, statuses and booleans, never `content`,
      `text`, `transcript` or `segments`.

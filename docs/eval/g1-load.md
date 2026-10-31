# G1 re-verification under load (S4.3)

Load evidence for **L-S4-SG-01**: concurrent uploads with injected failures must leave
**zero audio persistence**, **zero orphan temporaries** and **RAM within the per-slot
reservation**. The harness is [`scripts/load/g1_load.py`](../../scripts/load/g1_load.py) and
the raw capture is
[`docs/sprints/artifacts/S4.3/g1-load.txt`](../sprints/artifacts/S4.3/g1-load.txt).

> **Status: measured locally on 2026-10-31; G1 load checks pass.** The run used a synthetic
> tone generated in RAM, no Riva channel, no provider key, no PostgreSQL/Redis and no real
> class audio. The strict verdict is green because the sandbox could mount a bounded private
> tmpfs (`CAP_SYS_ADMIN`); the injected-ENOSPC fallback is also demonstrated and labelled.
> This does not approve 200 MiB or 3 h, does not close G1 by itself, and does not replace the
> container/reference-environment sweep.

## Scope

In scope:

- concurrent uploads through the production reception path (`receiver.receive` streams to
  the attempt tmpfs without assembling a bytes object);
- the production ffprobe decision and the real `maulwurf_ingest.asr_job` child in its own
  process group, with the transcript stub (no Riva client is constructed);
- cleanup through `cleanup.destroy_and_verify`, including the `/proc` inspection of
  descriptors, process group and mounts;
- three injected failures: SIGKILL to the ASR group, the production ASR deadline, and a real
  `ENOSPC` on a bounded private tmpfs;
- zero persistence in the attempt/harness trees, logs and scanned caches; zero orphan
  directories, descriptors or process-group members; RAM per slot against the provisional
  reservation.

Out of scope (owned by other tests or still open):

- the API upload route, the PostgreSQL lease/outbox state machine and the transcript
  commit; `I-S1-SG-06/07/08` and `I-S2-SG-06/07/08` cover those layers;
- any Riva call, provider latency, provider RAM, retention or quota;
- the 1 h / 200 MiB class and the Caddy/browser leg (D2.x, J2.10);
- approving the provisional slot, tmpfs or memory numbers (D4 remains `blocked`);
- a global host sweep on a clean machine: this development host already contains
  unrelated audio artifacts, so the host row is context and the scoped sweep is the load
  evidence.

## Contract under measurement

`receiver.receive` writes the upload to `<tmpfs>/attempt-<id>-XXXX/original` in 64 KiB
chunks with `O_EXCL` and `0600`, hashing the bytes as they arrive. `probe_file` decides the
format from the written file. `asr_job --read-stub-transcript` runs
`pipeline.process`: ffprobe/silencedetect, one active `frag-NNNN.wav`, the stub
transcriber, delete the fragment, next. The harness runs that child with
`start_new_session=True` and then:

1. `kill`: SIGKILL to the whole group after a fragment exists;
2. `timeout`: `_run_asr` with a 1 s deadline in realtime mode, which kills the group;
3. `full`: the upload hits a real `ENOSPC` on a private tmpfs sized below the upload;
4. `success`: the same path without a failure, as the baseline.

Cleanup is always `cleanup.destroy_and_verify`: kill the group, wait for its live members
to die, delete the directory, and prove in `/proc` that the path is absent, the parent is a
tmpfs, no live member remains, no descriptor under the directory is open and no mount was
created below it. A worker is a subreaper, like `init: true` in compose, and reaps orphaned
grandchildren before the scan; each worker runs in its own PID/mount namespace when
`unshare` is permitted, so a sibling's processes never appear in the evidence.

Definitions used in the tables:

```
per_slot_reservation = PROVISIONAL_MEM_LIMIT_BYTES / PROVISIONAL_SLOTS = 512 MiB
slot_ram_peak         = worker RSS + every live process-group RSS + tmpfs bytes
zero_audio            = scoped sweep clear + root empty + no attempt left bytes
zero_orphans          = workdir absent + 0 descriptors + 0 group members + 0 slot entries
ram_ok                = every slot_ram_peak <= per_slot_reservation
```

The reservation is **derived** from the provisional 1 GiB instance limit and two slots. It
is not an approved capacity decision.

## Environment

| Field | Value |
|---|---|
| Date (UTC) | 2026-10-31 |
| Host | Linux 7.0.0-39-generic x86_64 |
| Python | 3.12.3 |
| ffmpeg | 8.1.2 |
| Output isolation | one PID/mount namespace per worker (`unshare`) |
| Full-tmpfs injection | real, bounded private tmpfs of 96 KiB |
| Synthetic audio | 6 s tone, 16 kHz mono PCM s16, generated in RAM |
| Slots / attempts | 2 concurrent / 4 (success, kill, timeout, full) |
| Riva / PostgreSQL / Redis | none / none / not set |

## Results

| Attempt | Scenario | Outcome | tmpfs peak | Slot RAM | fs clean | verified |
|---|---|---|---|---|---|---|
| 0 | success | success | 375.2 KiB | 118.39 MiB | yes | yes |
| 1 | kill | killed (`asr_failed`) | 187.7 KiB | 118.16 MiB | yes | yes |
| 2 | timeout | timeout | 187.7 KiB | 118.16 MiB | yes | yes |
| 3 | full | ENOSPC | 64.0 KiB partial | 40.91 MiB | yes | yes |

- **Zero audio persistence: yes.** The scoped sweep reports `audio_files=0`,
  `magic_matches=0`, `log_audio_markers=0`, `scan_complete=true`; the harness root is empty
  and every attempt workdir is absent. The host sweep found pre-existing unrelated files
  (`audio_files=16`, `magic_matches=10`, `scan_complete=false`) and is reported only as
  context.
- **Zero orphan temporaries: yes.** Every attempt reports `workdir_absent=true`, zero
  descriptors, zero live process-group members, zero slot-directory entries. The full-tmpfs
  mount was empty and unmounted; the root was empty before removal.
- **RAM within the per-slot reservation: yes.** The largest slot peak is 118.39 MiB against
  the derived 512 MiB reservation. The sum of tmpfs peaks is 814.6 KiB against the
  provisional 256 MiB. The ENOSPC attempt peaks at 40.91 MiB because it never starts ffmpeg.
- **Injections covered: yes.** kill, timeout and real full tmpfs all executed and were
  confirmed; the strict `g1_verified` and the labelled `g1_verified_with_injected_fallback`
  are both true.

A first observation of the same run must be recorded honestly: in the initial captures the
kill attempt reported `fs_clean=yes` but `verified=no`. `open_descriptors_under` counted a
killed `ffmpeg` in state `Z` as an unreadable process, because `/proc/<pid>/fd` of a zombie
is denied even for its own UID. A zombie has already released every descriptor, so the
check was failing closed on a harmless race. The fix skips `Z`/`X` in
`cleanup.open_descriptors_under`; the regression test is
`test_a_zombie_is_not_an_unreadable_process` and the pre-fix capture is preserved in the
artifact. A non-zombie process that cannot be inspected (another UID) still fails closed.

## Limits and what is not verified

- **The global host sweep is not usable on this shared development host.** It found 16
  unrelated audio artifacts and reached the file budget. The reference environment is the
  hardened ingest container (single UID, tmpfs only, tini as PID 1); the load claim is the
  scoped sweep plus the `/proc` evidence. A clean-host run must reproduce `clear=true`.
- **The injected-ENOSPC fallback is not a real full disk.** It is available for
  environments without `CAP_SYS_ADMIN`, raises the real `ENOSPC` from the write boundary
  and is labelled `tmpfs_enospc_injected`; `g1_verified` stays false and only
  `g1_verified_with_injected_fallback` is true.
- **No real class audio, no Riva and no 1 h upload.** The ASR provider path, its memory and
  its retention are not measured; the 200 MiB/3 h limits stay unapproved.
- **No API/DB layer.** The harness does not own a lease, does not commit a transcript and
  does not publish the outbox; those are covered by the integration suites above.
- **The per-slot reservation is provisional.** 1 GiB / 2 slots is context, not an approved
  limit; S4.5 and the F0.2 follow-ups own the final budget.

## Reproduction

```bash
# Default: real bounded tmpfs when the sandbox permits it
cd apps/ingest && uv run python ../../scripts/load/g1_load.py

# Structured evidence (same run, no host sweep)
cd apps/ingest && uv run python ../../scripts/load/g1_load.py --no-host-sweep --format json

# Forced injected-ENOSPC fallback, explicitly labelled
cd apps/ingest && uv run python ../../scripts/load/g1_load.py \
    --full-injection write_error --attempts 4 --slots 2

# Unit support (no ffmpeg) and the load-marked case
cd apps/ingest && uv run pytest tests/test_g1_load.py -q
cd apps/ingest && uv run pytest tests/test_g1_load.py -q -m load
```

The harness exits 0 when the checks pass and the three injections are covered; it exits 1
when a check fails and 2 when the configuration cannot exercise an injection (for example,
a bounded tmpfs larger than the upload).

# Effective TTL benchmark (S4.4)

Evidence for **L-S4-SG-02**: the proposed ephemeral budgets — **10 min to start the
upload, 30 min of reception and 60 min of ASR** — measured with the production reception
and audio paths. The harness is
[`scripts/load/ttl_benchmark.py`](../../scripts/load/ttl_benchmark.py); the raw captures are
[`ttl-benchmark.json`](../sprints/artifacts/S4.4/ttl-benchmark.json) and
[`ttl-benchmark.txt`](../sprints/artifacts/S4.4/ttl-benchmark.txt).

> **Status: measured locally on the development machine on 2026-11-01.** The run used a
> synthetic tone generated in RAM, no Riva channel, no provider key, no PostgreSQL/Redis and
> no real class audio. It publishes the three TTLs in `GET /ingestion/capabilities` with
> `ttl_source_report`/`ttl_source_version`. It does **not** approve 200 MiB or 3 h: D4
> remains `blocked`. The provider leg is a projection from the F0.1 observation, not a new
> provider measurement.

## Scope

In scope:

- the production streaming reception (`receiver.receive`) into a private tmpfs attempt
  directory, hashed while it is written, with two concurrent slots;
- the production ffprobe → silencedetect → ffmpeg fragment extraction path
  (`pipeline.process`), with an empty-word stub transcriber and no Riva client;
- the chained deadline arithmetic from the published capabilities
  (`upload_expires_at → receive_deadline → asr_deadline`);
- the projection of the measured rates to the proposal (200 MiB and a 3 h class) and to
  the configured budgets;
- publication of the effective TTLs with their benchmark source, independently of the
  still-blocked size/duration limits.

Out of scope (owned by other tickets):

- the client uplink, the browser and the Caddy edge; the harness measures the server path
  only;
- the API↔ingest HTTP hop (covered by `I-S2-AN-12`) and PostgreSQL/Redis state (covered by
  the integration suites);
- provider latency, queueing, quality or retention (a real Riva call is not made);
- approving 200 MiB or 3 h (D4) and the final cost table (S4.5).

## Contract under measurement

| Phase | Production path | What the benchmark measures |
|---|---|---|
| Upload start, 10 min | `POST /audios` saves `upload_expires_at = now + 10 min`; the API rejects a late `PUT` with `410 upload_expired` before reading bytes | worst case: a client uses the whole start window and then sends 200 MiB; the chained budget is 10 + 30 min |
| Reception, 30 min | `receiver.receive` writes 64 KiB chunks with `O_EXCL`/`0600`, hashing incrementally; the API chains `receive_deadline = upload_expires_at + 30 min` | time to write 64 MiB per slot, with two slots; projection of 200 MiB at the slowest slot |
| ASR, 60 min | `pipeline.process` plans fragments with the 30 s cap and 0.25 s overlap, extracts one fragment at a time and deletes it before the next; `ingest` caps the child at the published ASR TTL | measured local seconds per audio second plus the F0.1 provider seconds per audio second; projection to 3 h |

Definitions:

```text
reception_seconds(200 MiB) = 200 / slowest_slot_MiB_per_s
reception_headroom        = 30 * 60 / reception_seconds(200 MiB)
asr_seconds(3 h)         = 3 * 3600 * (local_s_per_s + billed_s_per_s * provider_s_per_s)
asr_headroom             = 60 * 60 / asr_seconds(3 h)
upload_start_chain       = 10 min + reception_seconds(200 MiB) <= 10 min + 30 min
```

The provider factor is `1.692 s / 30.0 s = 0.0564 s/s`, from the F0.1 S1.A7 `es` row. It
includes call establishment and is applied linearly; the 3 h PCM was never uploaded in the
spike, so this is a projection, not a provider-contract result.

## Environment

| Field | Value |
|---|---|
| Date (UTC) | 2026-11-01 |
| Host | Linux 7.0.0-39-generic x86_64 |
| Python | 3.12.3 |
| ffmpeg / ffprobe | 8.1.2 |
| Concurrent slots | 2 (reception and ASR) |
| Reception payload | 64 MiB per slot, deterministic synthetic bytes, deleted and verified |
| ASR clip | 120 s mono PCM s16 16 kHz tone, 30 s fragment cap, 0.25 s overlap |
| Riva / PostgreSQL / Redis | none / none / none |

## Results

| Phase | Measurement | Proposal | Headroom | Verdict |
|---|---|---|---|---|
| Reception | slowest slot **36.03 MiB/s**; aggregate **83.31 MiB/s**; 200 MiB in **5.55 s** | 30 min (1,800 s) | **×324** | fits |
| Upload start | full **600 s** window + 5.55 s upload = **605.55 s** of the 2,400 s chain | 10 + 30 min | 1,794 s left | fits |
| ASR | local **0.0161 s/s**, billed **1.0083 s/s**, provider **0.0564 s/s**; 3 h projected in **788.08 s** (173.89 local + 614.20 provider) | 60 min (3,600 s) | **×4.57** | fits |

Per-slot measurements from the same capture:

| Slot | Reception bytes | Wall | Throughput | ASR fragments | ASR billed | ASR wall |
|---|---|---|---|---|---|---|
| 0 | 64 MiB | 1.777 s | 36.03 MiB/s | 5 | 121.0 s | 1.877 s |
| 1 | 64 MiB | 1.353 s | 47.29 MiB/s | 5 | 121.0 s | 1.932 s |

- **Reception: the 30-min budget is not the server constraint.** The slowest of two
  concurrent slots writes 200 MiB in 5.55 s, leaving a ×324 margin. The budget covers the
  client uplink, not the ingest tmpfs; the harness deliberately does not claim otherwise.
- **Upload start: the full window still fits the chain.** Even a client that starts 600 s
  after the reservation and then sends 200 MiB finishes inside the 10 + 30 min chain
  (605.55 s of 2,400 s). The API enforces the start window before the first byte; the
  benchmark verifies the arithmetic and that the reception work does not consume the start
  budget.
- **ASR: 60 min is justified.** The 3 h projection is 788.08 s. The old 600 s supervisor
  cap was shorter than that projection, so the live child cap now follows the published
  TTL: `INGEST_ASR_TIMEOUT_S` defaults to **3,600 s** and refuses a value below 3,600 s.
  This is an enforcement alignment, not a TTL change: the TTL stays at the proposed 60 min.
- **Cleanup:** every attempt directory was deleted and verified absent, and the harness
  root was empty. No audio is kept.

## Publication

`GET /ingestion/capabilities` now publishes the effective values without requiring a full
D4 publication:

```json
{
  "limits_validated": false,
  "max_upload_bytes": null,
  "max_duration_seconds": null,
  "ttl": {"upload_start_minutes": 10, "receive_minutes": 30, "asr_minutes": 60},
  "ttl_source_report": "docs/eval/ttl-benchmark.md",
  "ttl_source_version": "2026-11-01"
}
```

The API resolves `upload_expires_at`, `receive_deadline` and `asr_deadline` from those
values (`services/audios.attempt_deadlines`). A publication with only one or two TTLs is
rejected at startup; an explicit all-null TTL publication keeps the S1
`MAULWURF_UPLOAD_TTL_MINUTES` fallback and invents no receive/ASR deadline.

## Findings and limits

- **The Caddy edge is shorter than the reception budget.** J2.10 sets
  `servers.timeouts.read_body 20m`, documented as covering the provisional 15-minute upload
  window. The published chain can use up to 10 + 30 min. The application budget is still
  the contract, but through Caddy a transfer cannot consume the full 30 min until the
  J-area owner raises `read_body` (for example to 40m). This ticket does not edit
  `infra/`, so the edge change is a documented proposal for J2.10, not applied here.
- **The client uplink is not measured.** The reception row is the API→ingest→tmpfs path;
  a slow user link is outside this harness and is exactly what the 30-min budget is for.
- **The provider leg is a projection.** No Riva channel, key or new call is used; the F0.1
  30 s observation is linear and includes call establishment.
- **The ASR clip is a tone, not speech.** It exercises ffmpeg/fragmentation time, not
  transcription quality or provider queueing.
- **No API HTTP hop, PostgreSQL or Redis.** `receiver.receive` is the production receiver,
  but the API→ingest streaming contract is covered by `I-S2-AN-12`.
- **Reception is a published budget, not a hard 30-min kill.** The API chains
  `receive_deadline`; the ingest receiver has no independent wall-clock kill for a live
  stream, so the client disconnect and the edge bound it. The ASR child cap is 60 min from
  the transcribing phase (not recomputed from the row's `asr_deadline`); per-row deadline
  enforcement remains a follow-up outside this publication ticket.
- **200 MiB and 3 h stay unapproved.** They are the proposal used to size the projections;
  D4 remains `blocked`, and `limits_validated` stays `false`.

## Reproduction

```bash
# Default run: 64 MiB per slot, 2 slots, 120 s ASR clip, private tmpfs
cd apps/ingest && uv run python ../../scripts/load/ttl_benchmark.py

# Structured evidence (same run, no audio kept)
cd apps/ingest && uv run python ../../scripts/load/ttl_benchmark.py --format json

# Unit support and the real load case
cd apps/ingest && uv run pytest tests/test_ttl_benchmark.py -q -m "not load"
cd apps/ingest && uv run pytest tests/test_ttl_benchmark.py -q -m load
```

The harness exits 0 when every check passes, 1 when a TTL check fails and 2 when the
configuration cannot run (no tmpfs, not enough free space, bad input).

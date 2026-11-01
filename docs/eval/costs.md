# Real cost per class hour (S4.5)

Offline measurement for **C-S4-SG-04**: what one hour of class audio costs in ASR
minutes, ffmpeg CPU and RAM, with a reproducible harness and an explicit price input for
each term. These are the rows that feed the final **G8** budget.

> **Status: measured locally, prices `NO VERIFICADO`.** The measurements were produced on
> 2026-11-01 without a Riva channel, a key or a provider call. The F0.1 spike never
> measured an ASR price ([report §5](../spike/F0.1-informe-riva.md)), and no CPU or RAM
> price has been approved, so every USD column stays `NO VERIFICADO` until an operator
> supplies a rate. A green run here is not a G8 pass and does not approve 200 MiB or 3 h.

## Scope

In scope:

- effective ASR minutes per class hour from the production fragment plan (overlap
  included), inherited from the S3.5 measurement;
- user+system CPU seconds of the `ffprobe`/`ffmpeg` children on the production
  `pipeline.process` audio path, normalized to one source hour;
- the slot working set (worker RSS + tmpfs holding the original and the active fragment)
  and the GiB-hours it occupies over the measured wall time;
- a three-term cost composition where each rate is an explicit operator input and a
  missing rate keeps the term and the total `NO VERIFICADO`.

Out of scope:

- any Riva call, provider latency, provider quota, provider retention or SDK memory;
- LLM/embedding analysis tokens (S3.2 publishes those separately in
  [`window-cost.md`](window-cost.md)) and Google integrations;
- a full 1 h/200 MiB compose run under injected failures (S4.3 owns the G1 load);
- approving the provisional limits: D4 remains `blocked` and 200 MiB/3 h stay unapproved;
- closing G8.

## Contract under measurement

`scripts/load/cost_table.py` runs the S3.5 harness (`scripts/load/asr_cost.py`) and
composes its measurement into the cost table. That harness runs `--slots` concurrent
workers over the production `pipeline.process` path used by `asr_job`: `ffprobe` and
`silencedetect` plan the fragments, `ffmpeg` extracts one 30 s WAV at a time to a private
tmpfs, and an empty-words stub transcriber replaces Riva. The stub is also the measurement
point: the active fragment exists when it is called, so the tmpfs peak is exact instead of
a sleep-sampling guess.

S4.5 adds the child CPU measurement: each worker reads the `RUSAGE_CHILDREN` counters
before and after the audio path, so the delta is the CPU actually consumed by the
`ffprobe`/`ffmpeg` processes it waited for. The Python worker CPU, the provider and the
network are excluded by design.

Definitions used in the tables:

```
asr_minutes_per_class_hour = billed_minutes / (source_seconds / 3600)
cpu_seconds_per_class_hour = children_cpu_seconds delta / (source_seconds / 3600)
cpu_vcpu_hours             = cpu_seconds_per_class_hour / 3600
wall_seconds_per_class_hour = wall_seconds / (source_seconds / 3600)
ram_gib_hours              = ((rss_peak + tmpfs_peak) / 2^30)
                             x (wall_seconds_per_class_hour / 3600)

asr_cost   = asr_minutes_per_class_hour x asr_rate_usd_per_minute
cpu_cost   = cpu_vcpu_hours             x cpu_rate_usd_per_vcpu_hour
ram_cost   = ram_gib_hours              x ram_rate_usd_per_gib_hour
total      = asr_cost + cpu_cost + ram_cost
```

For the defaults (30 s fragment cap, 0.25 s overlap), a 300 s clip produces 11 fragments
and **302.5 billed seconds**: 60.5 effective minutes per class hour (+0.833% overlap).
The CPU and wall columns are a linear projection from the same clip; the 60 s run shows
that fixed start-up overhead inflates the projection, so the 300 s capture is the
published basis.

## Fixtures and environment

The clip is synthetic speech generated at execution time with `espeak-ng` in RAM, repeated
and trimmed to exactly 300 s (PCM s16 mono, 22050 Hz, about 12.6 MiB). It exists only on
the private tmpfs for the duration of the run; every slot directory is deleted and
verified. No real voice, class, transcript or PII is involved.

| Field | Value |
|---|---|
| Date (UTC) | 2026-11-01 |
| Host | Linux 7.0.0-39-generic x86_64 |
| Python | 3.12.3 |
| ffmpeg | 8.1.2 |
| espeak-ng | 1.51 |
| Slots | 2 concurrent (provisional `INGEST_SLOTS`) |
| Fragment cap / overlap | 30 s / 0.25 s |
| Riva call | none |

Command (from the repository root):

```bash
cd apps/ingest && uv run python ../../scripts/load/cost_table.py \
    --slots 2 --clip-seconds 300
```

The raw capture, including the JSON form and the 60 s and example-rate runs, is in
[`docs/sprints/artifacts/S4.5/cost-table.txt`](../sprints/artifacts/S4.5/cost-table.txt).

## Results

### Measured quantities per class hour

Three 300 s runs on the same host (the published table capture, its JSON repeat and the
example-rate run) plus a 60 s run for the fixed-overhead check and a repeat through the
S3.5 harness:

| Run | Clip | ASR minutes | ffmpeg CPU s | Cores busy | Wall s | Worker RSS | tmpfs peak | Working set | RAM GiB-hours |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A (table) | 300 s | 60.500 | 16.499 | 0.612 | 27.580 | — | — | 48.84 MiB | 0.000365418 |
| B (JSON) | 300 s | 60.500 | 15.257 | 0.685 | 23.188 | 35.24 MiB | 13.47 MiB | 48.71 MiB | 0.000306394 |
| C (table) | 60 s | 60.500 | 26.705 | 0.683 | 43.832 | — | — | 38.61 MiB | 0.000459122 |
| D (rates) | 300 s | 60.500 | 17.317 | 0.600 | 28.997 | — | — | 48.64 MiB | 0.000382632 |
| E (S3.5) | 300 s | 60.500 | 14.790 | 0.642 | 23.267 | — | — | 48.61 MiB | 0.000306797 |

The ASR minutes are stable because the overlap ratio is constant. CPU and wall vary
run-to-run on this shared host: the four 300 s captures span 14.79–17.32 CPU seconds and
23.19–29.00 wall seconds per class hour, so the budget uses the highest captured value.
The 60 s clip pays Python start-up, `ffprobe` and scheduler overhead once for half as much
audio, so its per-class-hour projection is clearly higher. The published budget range is:

| Quantity | Range (four 300 s runs) | Budget row (max) |
|---|---:|---:|
| ASR effective minutes per class hour | 60.500 | 60.5 |
| ffmpeg CPU seconds per class hour | 14.79–17.32 | 17.32 |
| CPU busy per slot | 0.600–0.685 cores | 0.685 cores |
| vCPU-hours per class hour | 0.004108–0.004810 | 0.004810 |
| Local wall per class hour | 23.19–29.00 s | 29.00 s |
| Worker RSS peak | 35.24 MiB | 35.24 MiB |
| tmpfs peak per slot | 13.47 MiB | 13.47 MiB |
| Peak working set (RSS + tmpfs) | 48.61–48.84 MiB | 48.84 MiB |
| RAM GiB-hours per class hour | 0.000306–0.000383 | 0.000383 |

The 13.47 MiB tmpfs peak matches S3.5: it is dominated by the original WAV (2.52 MiB per
minute at 22050 Hz mono s16); the active PCM fragment adds under 0.9 MiB. A compressed
real upload has a different original size, and 200 MiB is not approved, so this is not a
slot budget for the provisional ceiling.

### Cost table

No price is measured, so the default table has no USD number:

| Term | Measured quantity per class hour | Unit | Rate | Cost per class hour |
|---|---:|---|---:|---:|
| ASR | 60.5 | ASR minute | `NO VERIFICADO` | `NO VERIFICADO` |
| ffmpeg CPU | 0.004810 | vCPU-hour | `NO VERIFICADO` | `NO VERIFICADO` |
| RAM (RSS + tmpfs) | 0.000383 | GiB-hour | `NO VERIFICADO` | `NO VERIFICADO` |
| **Total** | | | | **`NO VERIFICADO`** |

When an authorized rate exists, the same command fills the columns:

```bash
cd apps/ingest && uv run python ../../scripts/load/cost_table.py \
    --slots 2 --clip-seconds 300 \
    --asr-rate-usd-per-minute 0.01 \
    --cpu-rate-usd-per-vcpu-hour 0.05 \
    --ram-rate-usd-per-gib-hour 0.005
```

With those **arbitrary example** rates (not a provider quote, not a cloud price) the
captured run prints `0.604999750 + 0.000240511 + 0.000001913 = 0.605242174 USD` per class
hour; ASR dominates by three orders of magnitude, which is why the missing provider rate
blocks the G8 number. The rates are labelled `operator-provided`, never spike-measured.

### G8 budget rows

| Row | Value | Scope |
|---|---|---|
| ASR effective minutes per class hour | 60.5 | Defaults 30 s / 0.25 s; linear projection |
| Local overlap overhead | +0.833% | 0.25 s per 30 s fragment |
| ASR rate per minute | `NO VERIFICADO` | F0.1 §5 / S1.A7 never measured a price |
| ASR cost per class hour | `NO VERIFICADO` | `60.5 x rate` |
| ffmpeg CPU seconds per class hour | 14.79–17.32 | `RUSAGE_CHILDREN` of ffprobe/ffmpeg, 2 slots |
| ffmpeg CPU vCPU-hours per class hour | 0.004108–0.004810 | CPU seconds / 3600 |
| CPU cores busy per slot | 0.600–0.685 | CPU seconds / wall seconds |
| ffmpeg CPU rate | operator input | No approved price |
| ffmpeg CPU cost per class hour | `NO VERIFICADO` | `vCPU-hours x rate` |
| Peak working set per slot | 48.61–48.84 MiB | Worker RSS + tmpfs peak |
| RAM GiB-hours per class hour | 0.000306–0.000383 | Peak working set x local wall time |
| RAM rate | operator input | No approved price |
| RAM cost per class hour | `NO VERIFICADO` | `GiB-hours x rate` |
| Total cost per class hour | `NO VERIFICADO` | Any missing rate keeps the total unverified |
| 200 MiB / 3 h approved | **no: `false`** | D4 stays `blocked` |
| G8 | **not closed** | Needs the provider price and the remaining G8 evidence |

## Reproduce

```bash
# Default table: 2 slots, 60 s synthetic clip, every rate NO VERIFICADO
cd apps/ingest && uv run python ../../scripts/load/cost_table.py

# Published basis and machine-readable output
cd apps/ingest && uv run python ../../scripts/load/cost_table.py \
    --slots 2 --clip-seconds 300
cd apps/ingest && uv run python ../../scripts/load/cost_table.py \
    --slots 2 --clip-seconds 300 --format json

# Explicit operator rates (arbitrary example, not a quote)
cd apps/ingest && uv run python ../../scripts/load/cost_table.py \
    --asr-rate-usd-per-minute 0.01 --cpu-rate-usd-per-vcpu-hour 0.05 \
    --ram-rate-usd-per-gib-hour 0.005

# Pure model and report tests; the load case exercises ffmpeg + espeak
cd apps/ingest && uv run pytest tests/test_cost_table.py -q -m "not load"
cd apps/ingest && uv run pytest tests/test_cost_table.py -q -m load
cd apps/ingest && uv run pytest tests/test_asr_cost.py -q
```

The harness requires `ffmpeg`, `ffprobe` and `espeak-ng` on `PATH`, and a writable tmpfs
(`TMPDIR`, `/dev/shm`, or `--tmpfs`). It refuses to fall back to persistent disk. It
writes no repository file, prints no audio and keeps no WAV after the run. `--slots` is
bounded to 4 and the clip to 300 s; those are harness bounds, not approved ingestion
limits.

## Limitations

- The ASR rate is unmeasured; the cost is a formula until the authorized spike closes it.
  Provider billing granularity and minimums are `NO VERIFICADO`; effective minutes are
  the audio sent, not an invoice.
- ffmpeg CPU covers only the `ffprobe`/`ffmpeg` children that the worker waits for. The
  Python worker, Riva SDK, network and provider CPU are excluded.
- RAM GiB-hours multiply the peak working set by the local audio-path wall time. They are
  a lower bound: provider waiting, idle reservation, page cache and post-processing are
  excluded.
- The 300 s class-hour projection is linear and ignores silence, questions and non-speech
  audio; the 60 s run shows how fixed overhead distorts short clips.
- Two slots of 300 s are not a 1 h/200 MiB class under compose. S4.3 owns the G1 load with
  injected failures.
- The synthetic source is generated by `espeak-ng`; real uploads are compressed and may
  have a different tmpfs original size.
- No real class audio, transcript or provider output is involved. `apps/web` is untouched.

# ASR effective minutes and RAM/tmpfs under load (S3.5)

Offline measurement for **C-S4-SG-04**: the ASR audio minutes a class hour really sends,
the local overlap overhead, and the RAM/tmpfs working set of concurrent ingestion slots.
These are budget inputs for **G8** and the basis for the final S4.5 cost table.

> **Status: measured locally, ASR rate `NO VERIFICADO`.** The harness and the numbers below
> were produced on 2026-10-31 without a Riva channel, a key or a provider call. The F0.1
> spike did not measure a cost per minute or per hour
> ([report §5](../spike/F0.1-informe-riva.md)), so the provider term of the cost stays
> `NO VERIFICADO`. A green run here is not a G8 pass and does not approve 200 MiB or 3 h.

## Scope

In scope:

- billed audio seconds per fragment plan (the exact bytes sent to ASR, overlap included);
- effective ASR minutes per class hour, projected linearly from the measured clip;
- concurrent `asr_job` workers using the real ffprobe/silencedetect/ffmpeg audio path;
- per-slot tmpfs peak, concurrent tmpfs peak and worker/ffmpeg RSS peaks;
- cleanup evidence: every slot directory is removed and the tmpfs root is verified empty.

Out of scope:

- any Riva call, provider latency, provider quota or provider retention;
- the Riva SDK memory buffer, which the stub transcriber never constructs;
- ffmpeg CPU cost, transcription quality, prompts or the analysis windows (S3.2/S4.5);
- real class audio, real transcripts, student data or a 1 h/200 MiB upload;
- approving the provisional limits: D4 remains `blocked`.

## Contract under measurement

`apps/ingest/maulwurf_ingest/pipeline.py` plans fragments with the observed 30 s ceiling and
the configured `INGEST_FRAGMENT_OVERLAP_S` (default 0.25 s). For each fragment it runs
`ffmpeg` with input seek, writes one WAV on the attempt tmpfs, hands it to the transcriber,
deletes it, and starts the next one. The harness replaces only the transcriber with the stub
that counts fragment bytes, so no provider client is built.

Definitions used in the tables:

```
billed_seconds       = sum(end - start) over the fragment spans
effective_minutes_h  = billed_minutes / (source_seconds / 3600)
amplification        = billed_seconds / source_seconds
cost_per_class_hour  = effective_minutes_h x rate_usd_per_minute
```

For the defaults, a 30 s fragment followed by a 0.25 s overlap repeats 0.25 s of audio per
boundary: 11 fragments for a 5 min clip bill 302.5 s (+0.833%).

## Fixtures and environment

The clip is synthetic speech generated at execution time with `espeak-ng` in RAM, repeated
and trimmed to exactly 300 s (PCM s16 mono, 22050 Hz, about 12.6 MiB). Nothing is versioned
or kept: the source WAV lives only inside the private tmpfs directories for the duration of
the run. The 300 s and 60 s runs use the same phrase; no real voice, class or PII is
involved.

| Field | Value |
|---|---|
| Date (UTC) | 2026-10-31 |
| Host | Linux 7.0.0-39-generic x86_64 |
| Python | 3.12.3 |
| ffmpeg | 8.1.2 |
| espeak-ng | 1.51 |
| Slots | 2 concurrent (provisional `INGEST_SLOTS`) |
| Fragment cap / overlap | 30 s / 0.25 s |
| Riva call | none |

Command (from the repository root):

```bash
cd apps/ingest && uv run python ../../scripts/load/asr_cost.py \
    --slots 2 --clip-seconds 300
```

The raw capture is in
[`docs/sprints/artifacts/S3.5/asr-cost.txt`](../sprints/artifacts/S3.5/asr-cost.txt).

## Results

### ASR effective minutes

| Clip | Fragments | Billed seconds | Overlap seconds | Amplification | Effective minutes/class-hour |
|---|---:|---:|---:|---:|---:|
| 60 s | 3 | 60.50 | 0.50 | 1.008333 | 60.50 |
| 300 s | 11 | 302.50 | 2.50 | 1.008333 | 60.50 |

The normalized value is stable because the overlap ratio is constant: one class hour sends
**60.5 billed minutes**, not 60. The +0.833% is the price of the local 0.25 s overlap and is
independent of the clip length at this fragment size.

### RAM and tmpfs under load (300 s clip, 2 concurrent slots)

| Measure | Slot 0 | Slot 1 | Two-slot upper bound |
|---|---:|---:|---:|
| tmpfs peak (original + one active fragment + stub) | 13.47 MiB | 13.47 MiB | 26.94 MiB |
| tmpfs after the run, before deletion | 12.62 MiB | 12.62 MiB | 25.24 MiB |
| Worker RSS before | 35.16 MiB | 35.11 MiB | — |
| Worker RSS peak | 35.58 MiB | 35.41 MiB | 70.99 MiB |
| Worker RSS delta | 0.30–0.42 MiB (per slot) | — | under 0.6 MiB |
| ffmpeg child max RSS | 47.80 MiB | 47.80 MiB | 47.80 MiB (max, not additive) |

The 60 s run shows the same shape: 3.40 MiB tmpfs peak per slot, 6.81 MiB for both slots,
and a worker RSS peak of 35.23–35.30 MiB. The tmpfs term is dominated by the original WAV
(2.52 MiB per minute at 22050 Hz mono s16); the active 30 s PCM fragment adds about
0.88 MiB and the stub file is negligible. A compressed real upload has a different original
size, and the provisional 200 MiB ceiling is not approved, so the table is not a slot budget
for 200 MiB or 3 h.

The worker RSS baseline (about 35 MiB) is the Python process after imports; the measured
delta during the audio path is under 0.6 MiB because the worker streams the WAV through
ffmpeg and never holds the whole class in its heap. The 47.80 MiB figure is the maximum RSS
of any waited ffmpeg child for a 30 s fragment; it is a single-child high-water mark, not a
sum and not a full slot. The Riva SDK buffer is absent by construction in this harness.

`fits_provisional_tmpfs_upper_bound` and `fits_provisional_mem_limit_upper_bound` are
reported only as context:

| Fit | Result |
|---|---|
| 26.94 MiB vs provisional 256 MiB tmpfs | yes |
| 70.99 MiB vs provisional 1 GiB container memory | yes |
| 200 MiB / 3 h approved | **no: `false`** |
| D4 capacity decision | `blocked` |

The yes rows do not approve a limit. F0.2 already shows that two 200 MiB slots do not fit
the 256 MiB tmpfs arithmetically; this harness does not test that regime.

## ASR cost

The F0.1 report records `Cost per minute = NO VERIFICADO` and
`Cost per hour = NO VERIFICADO` (§5). There is no authorized rate to multiply, so the
harness refuses to invent one:

```
effective minutes per class hour = 60.5
rate                              = NO VERIFICADO  (F0.1 spike, S1.A7)
cost per class hour               = NO VERIFICADO
```

When an authorized rate exists, rerun with `--rate-usd-per-minute RATE`. The report then
labels the rate `operator-provided` (not spike-measured) and fills the cost columns:

```
cost per class hour = 60.5 x RATE
```

This separation is deliberate: the measured factor is the effective-minutes row, and the
provider price remains an explicit input until the protected-account spike measures it.

## G8 budget rows

| Row | Value | Scope |
|---|---|---|
| ASR effective minutes per class hour | 60.5 | Defaults 30 s fragment / 0.25 s overlap; linear projection from the synthetic clip |
| Local overlap overhead | +0.833% | 0.25 s per 30 s fragment; independent of clip length at this profile |
| ASR rate per minute | `NO VERIFICADO` | No authorized provider price (F0.1 §5, S1.A7) |
| ASR cost per class hour | `NO VERIFICADO` | `60.5 x rate`; operator rate is labelled separately |
| tmpfs peak per concurrent slot | 13.47 MiB | 300 s WAV source + one 30 s PCM fragment + stub; compressed uploads differ |
| tmpfs peak, 2 concurrent slots | 26.94 MiB | Sum of per-slot maxima, conservative upper bound |
| Worker RSS peak per slot | 35.16–35.58 MiB | Absolute RSS; delta under 0.6 MiB; excludes ffmpeg child and Riva SDK |
| ffmpeg child max RSS | 47.80 MiB | Max of any waited child, not additive |
| Provider latency and SDK RSS | `NO VERIFICADO` | Requires an authorized `P-*` run |

## Reproduce

```bash
# Default table: 2 slots, 60 s synthetic clip, cost rate unverified
cd apps/ingest && uv run python ../../scripts/load/asr_cost.py

# Longer clip and machine-readable output
cd apps/ingest && uv run python ../../scripts/load/asr_cost.py \
    --slots 2 --clip-seconds 300 --format json

# Explicit operator rate (not a spike measurement)
cd apps/ingest && uv run python ../../scripts/load/asr_cost.py \
    --clip-seconds 300 --rate-usd-per-minute 0.0065

# Pure model and report tests; the load test exercises ffmpeg + espeak
cd apps/ingest && uv run pytest tests/test_asr_cost.py -m "not load" -q
cd apps/ingest && uv run pytest tests/test_asr_cost.py -m load -q
```

The harness requires `ffmpeg`, `ffprobe` and `espeak-ng` on `PATH`, and a writable tmpfs
(`TMPDIR` or `/dev/shm`, or `--tmpfs`). It refuses to fall back to persistent disk. It
writes no repository file and prints no audio. `--slots` is bounded to 4 and the clip to
300 s; those are harness bounds, not approved ingestion limits.

## Limitations

- The ASR rate is unmeasured; the cost is a formula until the authorized spike closes it.
- The stub transcriber does not include the Riva SDK, network latency, retries or provider
  memory. It measures the local audio path only.
- The source is a WAV synthesized by espeak-ng. Real uploads are compressed and may have a
  different tmpfs original size; the provisional 200 MiB and 3 h are not tested.
- The class-hour projection is linear and ignores silence, questions and non-speech audio.
- Two slots of 300 s are not the same as a 1 h class under compose. S4.3 owns the G1 load
  with injected failures; S4.5 owns the final table with ffmpeg CPU and the real rate.
- The tmpfs upper bound adds per-slot maxima even when the peaks do not coincide; the
  sampled concurrent peak was 26.34 MiB against the 26.94 MiB upper bound in this run.

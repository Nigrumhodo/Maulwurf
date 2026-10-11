# Santiago Montealegre — sprint logbook

> Suggested profile: Ingestion · Audio · ASR (plan §0.2) ·
> Master plan: [../PLAN_SPRINTS.md](../PLAN_SPRINTS.md) ·
> Tests: [../PLAN_TESTS.md](../PLAN_TESTS.md) ·
> Contracts: [../ESPECIFICACION.md](../ESPECIFICACION.md) (§M2, §M3) ·
> Technical document: [../NVIDIA_RIVA.md](../NVIDIA_RIVA.md) ·
> Gates: [../PLAN_IMPLEMENTACION.md](../PLAN_IMPLEMENTACION.md) (F0, G1)

## Claimed tickets

### Week 1 (S1) — Foundation + spike (F0, blocking)

> Historical section: rows written before the 2026-09-19 review are candidates, not
> claims. Current ownership: [CLAIMS.md](CLAIMS.md). This table is evidence.

| Ticket | Area | Status | Evidence (command, date, environment, result) |
|---|---|---|---|
| L1.1 | L | done | 2026-09-23 UTC. `uv sync --extra dev` in `apps/api` and `apps/ingest`; `npm install` in `apps/web`. Pins `.python-version` 3.12, `.nvmrc` 22, `docs/VERSIONES.md` and lockfiles. Merge #7. |
| L1.2 | L | done | 2026-09-23 UTC. ADR-0001–0004 in `docs/adr/` (monorepo, versions, lockfiles, commit/PR). Merge #7. |
| L1.3 | L | done | 2026-09-23 UTC. Protocol in `docs/spike/F0.1-protocolo.md`. It is not the S1.A8 report nor Riva calls. Merge #7. |
| S1.A1 | S | done | 2026-09-23 UTC, WSL. `cd apps/ingest && uv run pytest tests/test_synthetic_audio.py` (U-S1-SG-01): 9 passed. Synthetic WAV in RAM. Merge #6. |
| S1.A2 | S | done | 2026-09-24 UTC, WSL. `cd apps/ingest && .venv/bin/pytest tests/test_riva_spike_report.py`: 8 passed. `cd apps/ingest && .venv/bin/python ../../scripts/provider/riva_spike.py`: gRPC `OK`, client `nvidia-riva-client==2.27.0`, `grpcio==1.84.0`, `protobuf==6.33.5`. Initial/final metadata without a version (`date`, `nvcf-reqid`, `set-cookie`; no values). Server `NO VERIFICADO`. Hypothesis of 46 characters; the expected phrase does not match as is. |
| S1.A3 | S | done | 2026-09-24 UTC, WSL. `cd apps/ingest && .venv/bin/python ../../scripts/provider/riva_languages.py`. gRPC `OK` in `es`, `en` and `fr` (non-empty hypothesis; the expected phrase does not match). `zz` and missing code: `INVALID_ARGUMENT`. `es` audio declared as `en`: `OK`. Proposed allowlist: `es`, `en`, `fr`. No `multi` nor `task:translate`. Local pytest of the rule: 2 passed. |
| S1.A4 | S | done | 2026-09-24 UTC, WSL. `pytest tests/test_format_validate.py`: 3 passed (U-S1-SG-02). Container table re-run with ffmpeg/ffprobe 8.0.1, no new Riva call. WAV PCM s16 mono `accept`. `normalize` → WAV PCM s16 mono: mp3 (`mp3`), ogg (`vorbis`), opus (`opus`), flac (`flac`), webm (`opus`), all mono. m4a via pipe: `NO VERIFICADO`. 16 kHz gRPC `OK` (earlier run), expected phrase does not match; not an approved limit. 22050 Hz is quoted from S1.A2 (`OK`). |
| S1.A5 | S | done | 2026-09-24 UTC, WSL. `cd apps/ingest && .venv/bin/pytest tests/test_riva_limits_report.py tests/test_riva_spike_report.py`: 13 passed. `cd apps/ingest && .venv/bin/python ../../scripts/provider/riva_limits.py` (P-S1-SG-07), three calls in series. Local wait of 0.05 s: `DEADLINE_EXCEEDED` (`client_wait_timeout`); server deadline `NO VERIFICADO`. Immediate cancellation: `CANCELLED`. 16 kHz floor, 30.0 s, 960044 bytes: gRPC `OK`, hypothesis of 46 characters; it is an observed minimum, not a maximum. `approved_200_mib` and `approved_3h` are false. Quota and concurrency `NO VERIFICADO`. `INVALID_ARGUMENT` from S1.A3 quoted, not repeated. An earlier attempt aborted with `FutureCancelledError` before the floor; the recorded series is the complete one. `P-S1-SG-08` (latency and memory) is left for S1.A7. |
| S1.A6 | S | done | 2026-09-27T04:46:03Z, WSL. `cd apps/ingest && .venv/bin/pytest tests/test_timestamps.py tests/test_riva_offsets_report.py tests/test_riva_spike_report.py tests/test_riva_limits_report.py tests/test_riva_perf_report.py`: 26 passed (U-S1-SG-03 included). `cd apps/ingest && .venv/bin/python ../../scripts/provider/riva_offsets.py` (P-S1-SG-07), two calls in series with `enable_word_time_offsets`. Client: milliseconds documented, scale 0.001. Both gRPC `OK`, 0 words and 0 segments. Phrase 3.948 s, 174140 bytes, 22050 Hz, hypothesis of 46 characters. Silence in RAM 5.948 s (spoken 3.948 s), 262340 bytes. `d6_proposal=none`, `d6_status=pending`. It does not close G2-T nor G2-X. |
| S1.A7 | S | done | 2026-09-27T04:46:03Z, WSL. `cd apps/ingest && .venv/bin/python ../../scripts/provider/riva_perf.py` (P-S1-SG-08), three calls in series. `es` 3.948 s: latency 1.037 s, gRPC `OK`, 174140 bytes, 22050 Hz, hypothesis 46. `es` floor 30.0 s, 16 kHz, 960044 bytes: latency 1.692 s, gRPC `OK`. `en` 3.231 s, 142510 bytes, 22050 Hz: latency 1.054 s, gRPC `OK`, hypothesis 39. `en` 30 s and `fr` `NO VERIFICADO`. SDK `VmRSS`: 20742144 before importing `riva.client`, 36171776 after. PCM 3 h 16 kHz mono s16 = 345600000 bytes (329.59 MiB), not uploaded. RSS before 36175872, peak 381779968, delta 345604096. Cost/min and /h `NO VERIFICADO`. `approved_200_mib` and `approved_3h` are false. Observed audio 37.178 s. |
| S1.A8 | S | done | 2026-09-27 UTC. Report in `docs/spike/F0.1-informe-riva.md`, L1.3 format, no new call to NVIDIA. Gaps marked `NO VERIFICADO`. Audio retention for this `function-id` is not stated on the public NVCF terms page. |
| S1.A9 | S | done | 2026-09-27 UTC. Decision record in `docs/spike/F0.1-acta-decisiones.md`. D2 `pending`, D3-Audio `blocked`, D4 `blocked`, D6 `pending` with proposal `none`. None `approved`. Spec, API and UI unchanged. |
| S1.B2 | S | done | 2026-09-27 UTC, WSL. `cd apps/ingest && .venv/bin/pytest tests/test_capacity.py -q --noconftest`: 3 passed. `cd apps/ingest && .venv/bin/python ../../scripts/provider/capacity_bench.py`: one provisional slot of 209747200 bytes fits in the 268435456-byte tmpfs; two slots (419494400) do not. RSS of a 2 s conversion: before 16633856, peak 16769024; peak directory 128122; audio deleted. 200 MiB and 3 h not approved. The 3 h PCM does not fit in the slot. |
| S1.B4 | S | done | 2026-09-27 UTC. Postgres at `127.0.0.1:55432`. On the WSL host, 7 failed: `unreadable_processes` 35, uid 0, descriptors to the audio 0, tmpfs yes. In an Ubuntu container with its own `/proc`: `cd apps/ingest && .venv/bin/python -m pytest -m integration tests/test_failure_matrix.py -q` → 10 passed in 9.23 s. `unreadable` 0. |
| S1.B5 | S | done | 2026-09-27 UTC, WSL. Postgres and Redis at `127.0.0.1`. `cd apps/ingest && uv run pytest tests/test_no_audio_persistence.py tests/test_cleanup_admission.py -q` → 9 passed in 7.48 s. I-S1-SG-06: mounts, Redis, logs, traces and caches at zero, no dumps. On this host the supervisor's success does not end up `verified` because of `unreadable_processes` (same as S1.B4); the attempt directory is already gone. |
| S1.B6 | S | done | 2026-09-27 UTC, WSL. The 9 passed in ingest cover I-S1-SG-05: an expired lease leaves it `pending` and admission stays open; cleanup `failed` closes it and emits `cleanup_failed` without paths. `cd apps/api && uv run pytest tests/integration/test_cleanup_gate.py -q` → 1 passed in 1.97 s. `index_requested` stays `pending`. |

### Week 2 (S2) — Knowledge

> Ownership in [CLAIMS.md](CLAIMS.md). S2.3 and S2.4 remain `in progress`: the supervisor's
> integration matrix did not run (it needs Postgres) and `P-S2-SG-12` was not executed.

| Ticket | Area | Status | Evidence (command, date, environment, result) |
|---|---|---|---|
| S2.1 | S | done | 2026-10-09 UTC. Merge #54. Reception to tmpfs, SHA-256 and ffprobe. Covered by the suite below. |
| S2.2 | S | done | 2026-10-09 UTC. Merge #54. Bounded ffmpeg and 30 s fragmentation. A `-fs` cut does not end as a success. |
| S2.3 | S | in progress | 2026-10-09 UTC, WSL, `apps/ingest/.venv`. One active fragment, `-ss` before `-i`. `ruff check .` and `mypy .` clean. `pytest -m "not integration and not provider and not load"` → 126 passed, 33 deselected. |
| S2.4 | S | in progress | 2026-10-09 UTC, same environment. Client isolated from the event loop; retries classified with a test double, no NVIDIA channel. `P-S2-SG-12` is not executed. Cloud `pending`. |

## Blockers and dependencies

- (every Monday: note whom you are waiting on for a frozen contract §6 or real code, and which day)

## Week notes

- 2026-09-23: retroactive record; work in #6 and #7; canonical claim in #8.

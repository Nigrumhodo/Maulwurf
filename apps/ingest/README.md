# apps/ingest — Ephemeral ingestion (area `S-`)

- Measured contract first: the F0.1 spike (S1) fixes languages, limits, and timestamps
  (`docs/NVIDIA_RIVA.md`). No hardcoded constants until the report exists.
- Bounded tmpfs per attempt, cleanup in `finally`, lease/fencing, post-commit reconciler.
- `GET /ingestion/capabilities` (API) publishes only measured limits; until D4 closes it
  returns `null`/empty limits and the web disables upload.
- Per-ticket ownership in [docs/sprints/CLAIMS.md](../../docs/sprints/CLAIMS.md).
- S1.A1 (RAM synthetic generator) requires the `espeak-ng` (or `espeak`) system binary
  on `PATH` for the tests. On Windows, WSL can be used (`sudo apt
  install espeak-ng`). The WAV is generated via stdout and is not versioned or kept
  as an artifact; only the expected text and non-sensitive metrics are persisted.
- The ingest Dockerfile exists (S1.B1: `runtime` target, hardened, ffmpeg pinned by
  J1.8). `espeak-ng` is not in the image: only the tests use it, and CI installs it
  (`.github/workflows/quality.yml`); pinning its version stays pending if the waveform
  must be reproducible outside CI.

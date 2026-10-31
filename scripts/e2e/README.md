# E2E smoke — E-S2-JF-06 (J2.6)

`smoke_pipeline.py` is the automated smoke that runs on every MR and, locally, against
`infra/docker-compose.yml` plus `infra/docker-compose.e2e.yml`:

1. `POST /audios` reserves a class (A2.3/A2.14) and the driver streams a synthetic WAV
   through `PUT /audios/{id}/content` (A2.4).
2. `api` forwards the bytes to `ingest` (A2.13/S2.11); `ingest` runs the real ffmpeg
   fragmentation and ASR child against a local gRPC double, commits the transcript
   atomically (S2.5) and records **verified cleanup** before enabling the outbox (S2.6).
3. The scheduler dispatcher (J2.1) publishes `index_requested`; the worker runs
   `index(audio_id)` (J2.2) and activates one generation.
4. `GET /search` and `POST /chat/conversations/{id}/messages` run through the real hybrid
   search and chat SSE pipeline (L2.3–L2.6); the driver asserts a `citation` event whose
   `audio_id` is the smoke class and whose `index` is 1.
5. The driver checks the durable row holds only the class metadata (`sha256` and
   `original_bytes`), and the CI job asserts the ingest tmpfs is empty.

## Real components vs doubles

Real: PostgreSQL/pgvector, Redis, `api`, `ingest`, `worker`, `scheduler`, the ffmpeg
fragmentation, the Riva client code path (`GrpcRivaTransport`), the atomic commit, the
cleanup gate, the outbox, the dispatcher, the `index` job, hybrid search and chat SSE.

Doubles, local/CI only:

- `e2e_stubs/asr_double.py`: Riva `SpeechRecognition/Recognize` over TLS. It returns a
  fixed synthetic transcript with word times; it reads no audio and logs no content.
- `e2e_stubs/provider_double.py`: OpenAI-compatible `/v1/embeddings` and streaming
  `/v1/chat/completions`. Embeddings are a deterministic bag-of-words projection; the chat
  answer is a fixed synthetic sentence citing `[S1]`.

A double is **not** provider-contract evidence (`P-*` remains protected) and this smoke does
not close a `P-*` test. It also does not replace the 1 h Caddy/browser demo of S2.9.

## D3-Texto

D3-Texto remains **open**. The compose override sets
`MAULWURF_D3_TEXT_CLOSED_MODELS` only to the synthetic model name
`maulwurf-e2e-synthetic-v1` so the synthetic fixture can be indexed. That is not a decision
about any real provider or model; it lives only in `infra/docker-compose.e2e.yml` and is not
part of `.env.example`, the base compose file or any deployment.

## Run it locally

```bash
# From the repository root. The certificates are ephemeral and git-ignored; the compose
# override's default E2E_TLS_DIR=../.e2e-tls resolves to this same directory.
# The override hardcodes non-production credentials, so it requires an explicit opt-in.
export E2E_ALLOW_EPHEMERAL_CREDENTIALS=1
# The server key is mode 640; its group must match the e2e-asr container's extra group.
export E2E_TLS_GID="$(id -g)"
bash scripts/e2e/gen_riva_tls.sh .e2e-tls

cd infra
docker compose -f docker-compose.yml -f docker-compose.e2e.yml \
  up -d --build --wait postgres redis e2e-provider e2e-asr ingest worker scheduler api

docker compose -f docker-compose.yml -f docker-compose.e2e.yml \
  exec -T api python - < ../scripts/e2e/smoke_pipeline.py

docker compose -f docker-compose.yml -f docker-compose.e2e.yml \
  exec -T ingest python -c 'import os,sys; sys.exit(1 if os.listdir("/work/tmp") else 0)'

docker compose -f docker-compose.yml -f docker-compose.e2e.yml down -v
```

`E2E_TLS_DIR` overrides the certificate directory (CI uses `$RUNNER_TEMP/e2e-tls`).
`E2E_TLS_GID` is the host group that owns the 640 `server.key`; the `e2e-asr` container joins
it so the key is never world-readable. `E2E_ALLOW_EPHEMERAL_CREDENTIALS=1` is a required
acknowledgement: the override uses fixed, non-production credentials, runs under the
isolated `maulwurf-e2e` compose project and is never valid for a real deployment.
`SMOKE_TRANSCRIPT_TIMEOUT_S`, `SMOKE_INDEX_TIMEOUT_S` and `SMOKE_CHAT_TIMEOUT_S` bound the
waits; the index stage may wait up to one dispatcher cron (~60 s by design).

Helper checks run without Docker and without services:

```bash
python3 -m unittest discover -s scripts/e2e/tests -v
```

## Fixture rules (PLAN_TESTS §0.4)

- The WAV is built in RAM by the driver at execution time; it is never written to the repo,
  uploaded as an artifact or reused. The only durable trace is the class row with its SHA-256
  and byte count.
- The transcript, the answer and the question are synthetic; no real voice, class or PII.
- No API keys: the compose override uses fixed, non-secret CI placeholders. The TLS material
  is generated per run and `*.pem`/`*.key` are git-ignored.
- `SMOKE_EXPECTED_TEXT` is the single fixture shared by the ASR double and the driver.

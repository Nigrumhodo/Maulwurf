# Provider outage runbook (J4.8)

> **Ticket:** J4.8 (`MAULW-146`) · **Depends on:** J4.7, S4.1, A4.5 · **Test:**
> C-S4-JF-10. **Review:** an active Infra claim or the lead.
>
> This runbook covers the four paid/external providers: NVIDIA Riva (ASR), the chat LLM,
> the embeddings provider and Google Calendar/Gmail. It keeps the failure bounded and
> observable; it never retries a real provider forever, never fabricates an answer and never
> moves audio out of the ingest tmpfs.
>
> References: [S4 specification](../plan/S4.md), [specification §7 and §8](../ESPECIFICACION.md),
> [budget and alerts](budget-alerts.md), [tokens](tokens.md), [cleanup](cleanup.md),
> [restore](restore.md).

## 1. Principles

1. **Fail closed.** A provider error becomes a typed error, a terminal state or an explicit
   `error` event. There is no fake transcript, fake embedding, fake completion or fake
   Calendar write.
2. **Bounded retries only.** The adapters retry only transient codes (408/429/5xx/network)
   with backoff+jitter and a hard attempt cap. A 401/403 is an auth problem, not a transient
   fault; a 400 is a request defect. Do not increase retries during an incident.
3. **Audio stays ephemeral.** Diagnostics never copy, stream, `cat`, `docker cp` or
   screenshot `/work/tmp`. If the audio is lost, the user reuploads it.
4. **No secrets or content in evidence.** Record provider names, status codes, error codes,
   counts and timestamps. Never a key, token, prompt, transcript, email, Calendar title or
   response body.
5. **Budget is part of the incident.** A quota exhaustion is a budget/alert event; follow
   [budget-alerts.md](budget-alerts.md) instead of retrying until the cap is raised.

## 2. Detection

### 2.1 Stack and dependency health

```bash
cd infra
docker compose ps --format 'table {{.Service}}\t{{.Status}}'
```

Expected: `postgres`, `redis`, `api`, `worker`, `scheduler`, `ingest`, `web` and `caddy` are
running/healthy. A dependency down (`postgres`/`redis`) is not a provider outage; fix it
first.

```bash
curl -fsS http://localhost:8000/healthz
curl -fsS http://localhost:8000/readyz
```

`/readyz` checks PostgreSQL and Redis only; it never calls a paid provider.

### 2.2 Signals per provider

| Provider | User-visible symptom | Durable signal | Safe diagnostic |
|---|---|---|---|
| Riva / ASR | Upload ends `requires_reupload` (or `rejected` for unusable text); no transcript | `ingestion_attempts.status`, `error_code` (`asr_failed`, `asr_rejected`, `asr_timeout`, `requires_reupload`) | SQL below; `docker compose logs ingest` (no paths/content) |
| LLM | Chat SSE ends with `error` code `provider_unavailable`, `rate_limited` or `context_exceeded`; no invented answer | `messages.status` (`failed`), SSE terminal event | `docker compose logs api \| grep 'llm\.'` (only retry lines) |
| Embeddings | Index generation stays `building`/failed; search falls back to its lexical branch or fails closed | `processing_runs`/`index_generations` state, run `error_code` (`embedding_provider_error`, `embedding_rate_limited`) | `docker compose logs worker`; `/internal/metrics` outbox lag |
| Google Calendar | Task `calendar_sync_status=failed`/`blocked`; `POST /calendar/sync` 503/409; mirror stale | `tasks.calendar_sync_status`, `calendar_events`, `GET /integrations/status` | `GET /calendar/events` (`stale`, `coverage_notice`); logs without tokens |
| Gmail | `delivery_unknown` or `failed` in the delivery panel; no blind retry | `notifications.status`, `last_error_code` | `GET /notifications`; never the raw provider body |

### 2.3 Read the ingest outcome without exposing audio

```bash
cd infra
docker compose exec -T postgres psql -U maulwurf -d maulwurf -c \
"SELECT error_code, status, count(*) FROM ingestion_attempts
 WHERE created_at >= now() - interval '24 hours'
 GROUP BY error_code, status ORDER BY count(*) DESC;"
```

Expected during a Riva outage: a cluster of `requires_reupload` with `asr_failed`,
`asr_timeout` or `asr_rejected`. `probe_unavailable` is an infrastructure/probe fault, not
a bad file. No row contains audio; do not select `cleanup_evidence` or any path.

### 2.4 Confirm the provider, not the application

Run the adapter probe with a **closed local port** (no key, no real request):

```bash
cd apps/api
uv run python - <<'PY'
import asyncio

from app.services.embeddings import EmbeddingError, HttpEmbeddingProvider
from app.services.llm import ChatMessage, HttpChatProvider, LLMError


async def main() -> None:
    llm = HttpChatProvider(
        base_url="http://127.0.0.1:9/v1",
        api_key="probe-not-a-secret",
        model="probe",
        context_window=1024,
        max_retries=0,
        timeout_seconds=0.5,
    )
    try:
        async for _ in llm.stream([ChatMessage(role="user", content="ping")], max_tokens=8):
            pass
    except LLMError as exc:
        print("llm", type(exc).__name__, exc.code)

    embeddings = HttpEmbeddingProvider(
        base_url="http://127.0.0.1:9/v1",
        api_key="probe-not-a-secret",
        model="probe",
        dimension=1536,
        batch_size=1,
        max_retries=0,
        timeout_seconds=0.5,
    )
    try:
        await embeddings.embed(["ping"])
    except EmbeddingError as exc:
        print("embeddings", type(exc).__name__, exc.code)


asyncio.run(main())
PY
```

Expected: `llm LLMProviderError llm_provider_error` and
`embeddings EmbeddingProviderError embedding_provider_error`, proving that an unreachable
provider becomes a typed, non-retrying error. A real endpoint or key is never used here.

For Riva and Google the deterministic adapters are exercised by their suites:

```bash
cd apps/ingest && uv run pytest tests/test_riva_client.py -q
cd ../api && uv run pytest tests/unit/test_google_calendar.py tests/unit/test_google_tokens.py -q
```

These verify retry classification, `invalid_grant` disconnection and the no-retry paths
without a provider call.

## 3. Containment

### 3.1 Riva / ASR down

1. Let the current attempts finish or fail; do not kill a live ASR process unless the
   cleanup runbook requires it.
2. Stop new real uploads at the product layer until the provider is healthy; the API may
   keep accepting them, but every failed attempt ends `requires_reupload` and cannot be
   retried from persisted audio.
3. Verify that each failed attempt still reached verified cleanup:

   ```bash
   cd infra
   docker compose exec -T postgres psql -U maulwurf -d maulwurf -c \
   "SELECT status, cleanup_status, count(*) FROM ingestion_attempts
    WHERE created_at >= now() - interval '24 hours' GROUP BY 1,2;"
   ```

   Expected: no attempt is left `transcribing` with `cleanup_status=pending` after its
   lease expires. If cleanup is `failed`, switch to [cleanup.md](cleanup.md); admission must
   stay closed.
4. When the provider is back, use a synthetic short clip through the authorized test setup
   (never a real class) and confirm the transcript commits and cleanup is `verified`.

### 3.2 LLM or embeddings down

1. Chat: the SSE pipeline persists no invented answer and emits `provider_unavailable`.
   Users can still read the library and export transcripts.
2. Indexing: the outbox keeps `index_requested`/`analyze_requested` durable; the worker
   retries with bounded backoff. Do not bump `max_attempts` or delete the outbox row.
3. Search: the embeddings query leg fails; the response must be honest. If the deployment
   serves lexical-only search during the incident, state it in the incident; do not inject
   zero vectors.
4. When the provider is back, verify one index generation activates (`GET /audios/{id}` or
   the selected class's `index` stage) before re-enabling load.

### 3.3 Google Calendar or Gmail down

1. Calendar writes stay in the outbox; a transient provider failure leaves the mirror
   untouched. A task stays `failed`/`blocked` with a redacted code; do not mark it `synced`.
2. A 401 is not revocation. An `invalid_grant` is: the integration is disconnected through
   `disconnect_google_credential` and the user must reconnect. Follow [tokens.md](tokens.md).
3. Gmail: an ambiguous send is terminal `delivery_unknown`; there is **no automatic retry**.
   The manual resend endpoint requires an explicit duplicate-risk acknowledgement and
   creates a new audited occurrence.
4. Verify `GET /integrations/status` reports the expected `connected`/`disconnected` state
   and `GET /calendar/events` reports `stale`/`coverage_ok` honestly.

## 4. Recovery verification

```bash
# No new retry storm; the outbox still advances or holds a documented gate.
cd infra
docker compose exec -T api python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/internal/metrics', timeout=3).read().decode())" \
  | grep -E 'maulwurf_outbox_(lag_seconds|retrying_events)'
```

Then exercise one synthetic request per affected provider with the authorized test setup:

| Provider | Acceptance |
|---|---|
| Riva | One synthetic clip produces a transcript, cleanup `verified`, no audio left |
| LLM | One SSE answer ends with `done`, with validated citations and no fabricated text |
| Embeddings | One index generation reaches `active` and search returns its own tenant's chunks |
| Google Calendar | One test event is created once, visible via the mirror, no duplicate |
| Gmail | One opt-in test send is `sent`; a simulated lost response is `delivery_unknown`, not a retry |

If a provider remains down, record the incident and keep the affected path disabled. A
degraded product is preferable to a false transcript, a fake embedding or a blind Calendar
write.

## 5. Evidence and limits

Local C-S4-JF-10 evidence lives in
[`docs/sprints/artifacts/J4.8/`](../sprints/artifacts/J4.8/). It proves the deterministic
failure paths with local doubles and a real PostgreSQL; it does **not** prove a real
provider outage, quota or retention behaviour. Those remain `P-*` provider-contract work and
the J4.7 reference drill stays **NOT RUN** in this ticket.

Stop conditions:

- a transcript, embedding, completion or Calendar event is invented to make a demo pass;
- retries are increased beyond the bounded policy or a 401/403 is retried;
- audio or a transcript is copied out of its ephemeral location;
- an alert is silenced without a budget decision.

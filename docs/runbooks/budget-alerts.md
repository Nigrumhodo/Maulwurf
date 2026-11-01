# Budget and alert runbook (J4.8)

> **Ticket:** J4.8 (`MAULW-146`) · **Depends on:** J4.7, S4.1, A4.5 · **Test:**
> C-S4-JF-10. **Review:** an active Infra claim or the lead.
>
> This is the executable procedure for provider budgets and the alert rules in
> [`infra/alerting/`](../../infra/alerting/). It measures tokens, ASR minutes and Google
> operations; it does not invent a price, a quota or a capacity limit. Where a cap is not
> configured the usage is still visible, but no alert can fire.
>
> References: [S4 detailed specification](../plan/S4.md), [cost table](../eval/costs.md),
> [specification](../ESPECIFICACION.md), [ingest cleanup](cleanup.md).

## 1. What is measured

| Provider | Series unit | Source in the database | Known lower bound |
|---|---|---|---|
| `llm` | `tokens` | `messages.input_tokens + messages.output_tokens` for the UTC month | Only persisted assistant turns; a provider retry after a visible delta is not billed here |
| `asr` | `minutes` | `audios.duration_seconds` for classes whose transcript was committed in the month (`transcripts.created_at`, never the upload date) | Source duration, not provider billing. S3.5 measured 60.5 effective minutes per class hour, a ≈0.833% local overlap, so billed minutes are slightly higher |
| `google` | `operations` | Sent `notifications`, `sync_task_event` outbox rows and `google_remote_operations` created in the month | Manual `POST /calendar/sync` reads and token refreshes are not durably recorded |

The window is the **UTC calendar month**. At 00:00 UTC on day 1 the usage resets; a
continuous month is never approximated by a rolling 30-day window. For ASR, the month is
the transcript's commit time: an audio uploaded on 31 October whose transcript is committed
on 1 November counts in November, never nowhere, and an audio uploaded in November does not
count until its transcript exists.

## 2. Configuration

Set the caps in the deployment secret/config store, never in the repository:

```bash
# Operator-provided values; the examples are placeholders, not approved budgets.
export MAULWURF_BUDGET_LLM_TOKENS='<tokens per UTC month>'
export MAULWURF_BUDGET_ASR_MINUTES='<source minutes per UTC month>'
export MAULWURF_BUDGET_GOOGLE_OPERATIONS='<operations per UTC month>'
export MAULWURF_BUDGET_WARNING_RATIO='0.8'   # 0 < ratio <= 1
```

`MAULWURF_BUDGET_*` unset means **measure only**: `maulwurf_budget_configured{provider=...}`
is `0`, `maulwurf_budget_limit`/`ratio` are absent, and no budget alert fires. It is not a
zero budget. Set each cap from the approved budget and the real provider contract. The
F0/F1 evidence did not publish a provider price or quota, so a number copied from this
document would be invented.

## 3. Read the current state

### 3.1 Directly from the API

The endpoint is internal-only (Caddy answers `404` to `/internal/*`):

```bash
cd infra
docker compose exec -T api python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/internal/metrics', timeout=3).read().decode())" \
  | grep -E '^maulwurf_budget_'
```

Expected: one `maulwurf_budget_usage{provider,unit}` line per provider. A configured
provider also has `limit`, `ratio`, `warning` and `exhausted` lines. No line contains a
token, prompt, transcript or user identifier.

### 3.2 With the dependency-free checker

From a checkout on the private network:

```bash
python scripts/ops/budget_check.py --url http://api:8000/internal/metrics
```

Expected output (values are examples):

```text
provider=llm state=ok usage=1200000 unit=tokens limit=2000000 ratio=0.6000
provider=asr state=warning usage=2000 unit=minutes limit=2400 ratio=0.8333
provider=google state=unconfigured usage=42 unit=operations limit=- ratio=-
```

Exit codes: `0` all configured budgets below the warning ratio, `1` warning (or a missing
cap with `--require-configured`), `2` exhausted, `3` endpoint/format error. The checker
prints only metric values and closed labels.

### 3.3 With Prometheus

```promql
maulwurf_budget_ratio
maulwurf_budget_warning
maulwurf_budget_exhausted
maulwurf_budget_configured
```

The rule file is [`infra/alerting/maulwurf.rules.yml`](../../infra/alerting/maulwurf.rules.yml).
The local compose stack does not run Prometheus; the reference deployment (J4.7) owns that
wiring. See [`infra/alerting/README.md`](../../infra/alerting/README.md).

## 4. Response

### 4.1 Warning (`state=warning`)

1. Identify the provider and unit from the output.
2. Confirm the traffic is expected: a class backlog, a reindex, a digest window or a
   marketing/demo burst. Do not assume it is fraudulent.
3. Project the rest of the month from the current usage and the elapsed days.
4. Choose one: reduce input (pause a backlog, defer a reindex), raise the cap with an
   explicit approval, or accept the risk with the projection recorded in the incident.
5. Re-run §3 after the decision.

### 4.2 Exhausted (`state=exhausted`)

1. Treat it as a stop signal for the paid path. Do not let retries or a backlog hide it.
2. For `llm`: chat may keep serving non-provider paths (library, search with its own
   embedding availability) but must not invent answers; the SSE pipeline fails closed with
   `provider_unavailable`/`rate_limited`.
3. For `asr`: new uploads can still be received only while capacity is available; an ASR
   failure ends `requires_reupload`, never a silent retry. Do not reupload a real class to
   "see if it works".
4. For `google`: calendar reads/writes and Gmail sends must be paused at the deployment
   level until the cap is raised; the outbox keeps the durable work, it does not drop it.
5. Raise the cap only with a documented decision (owner, date, new value, reason) and
   update the approved budget. Never edit a metric or delete a row to clear an alert.

### 4.3 Unconfigured (`configured=0`)

Set the cap from the approved budget. Until then, the deployment can serve synthetic and
test traffic only; real class load is not authorized.

## 5. Verification after a change

```bash
# The new value is visible and the process did not restart in a loop:
docker compose ps api
docker compose exec -T api python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/internal/metrics', timeout=3).read().decode())" \
  | grep -E 'maulwurf_budget_(limit|ratio|configured)'
```

Expected: the `limit` changes to the approved value, `configured` is `1`, and `ratio`
matches the measured usage. An unset variable is not a valid way to silence an alert; it
only moves the provider to `unconfigured`.

## 6. Evidence and limits

The local C-S4-JF-10 execution is recorded in
[`docs/sprints/artifacts/J4.8/`](../sprints/artifacts/J4.8/). It uses the local PostgreSQL
fixture and deterministic test values, not a real provider or an approved budget. The
reference-environment drill (real Prometheus/Alertmanager, real billing account and real
caps) stays **NOT RUN** until J4.7 exists.

Stop conditions:

- the usage cannot be read at all (`/internal/metrics` fails) — treat it as observability
  down, not as zero usage;
- a provider cap is exhausted and the paid path is still being called;
- someone proposes dropping the raw usage series to "fix" an alert.

## 7. Review checklist

- [ ] Each cap comes from an approved budget and a real provider contract.
- [ ] `MAULWURF_BUDGET_WARNING_RATIO` is between 0 and 1.
- [ ] The alert rules are mounted in the reference Prometheus and `/internal/metrics` is
      scraped from both `api` and `ingest`.
- [ ] The on-call rotation knows that unset ≠ zero and that an exhausted cap is a stop
      signal.
- [ ] No secret, token, transcript or provider payload appears in the alert annotations or
      the evidence.

# Alerting and provider budgets (J4.8)

This directory owns the deployment-side alert contract introduced by J4.8. It does not add
services to the local eight-service stack: the reference deployment (J4.7) mounts the
rules, scrapes the internal endpoints and routes the alerts.

| File | Purpose |
|---|---|
| [`maulwurf.rules.yml`](maulwurf.rules.yml) | Prometheus rules for provider budgets, cleanup/admission and outbox lag |
| [`prometheus.example.yml`](prometheus.example.yml) | Scrape/rule-file example for the reference host |

Operator procedures:

- [`docs/runbooks/budget-alerts.md`](../../docs/runbooks/budget-alerts.md) — budget sources,
  thresholds, escalation and the dependency-free `scripts/ops/budget_check.py`;
- [`docs/runbooks/cleanup.md`](../../docs/runbooks/cleanup.md) — failed cleanup and host loss;
- [`docs/runbooks/provider-down.md`](../../docs/runbooks/provider-down.md) — Riva, LLM,
  embeddings and Google outage;
- [`docs/runbooks/tokens.md`](../../docs/runbooks/tokens.md) — revoked or rotated credentials;
- [`docs/runbooks/account-deletion.md`](../../docs/runbooks/account-deletion.md) — account
  deletion, residual work and verification;
- [`docs/runbooks/restore.md`](../../docs/runbooks/restore.md) — the D8 backup/restore.

## Metrics contract

Both internal endpoints answer the Prometheus text format without authentication and are
reachable only from the private data network; Caddy answers `404` to `/internal/*`.

| Endpoint | Series used by the rules |
|---|---|
| `api:8000/internal/metrics` | `maulwurf_budget_usage`, `maulwurf_budget_configured`, `maulwurf_budget_limit`, `maulwurf_budget_ratio`, `maulwurf_budget_warning`, `maulwurf_budget_exhausted`, `maulwurf_outbox_lag_seconds`, `maulwurf_outbox_retrying_events` |
| `ingest:8000/internal/metrics` | `maulwurf_ingest_cleanup_failed_total`, `maulwurf_ingest_failures_total`, `maulwurf_ingest_admission_open`, `maulwurf_ingest_slots_in_use`, `maulwurf_ingest_slots_total`, `maulwurf_ingest_tmpfs_*`, `maulwurf_ingest_temporary_oldest_age_seconds` |

The budget gauges describe the current UTC calendar month. `maulwurf_budget_usage` is always
exported; the limit, ratio, warning and exhausted series exist only when the corresponding
cap is configured. `maulwurf_budget_warning` and `maulwurf_budget_exhausted` encode the
application's own threshold, so a rule never duplicates the ratio constant.

## Budget configuration

All caps are optional and unset by default. Unset means "measure, do not alarm"; it is not a
zero budget.

| Variable | Unit | Source |
|---|---|---|
| `MAULWURF_BUDGET_LLM_TOKENS` | input + output tokens per UTC month | Token plan from the selected LLM provider and the S4.5 cost table |
| `MAULWURF_BUDGET_ASR_MINUTES` | source audio minutes per UTC month | Effective minutes measured by S3.5/S4.5 and the real Riva contract |
| `MAULWURF_BUDGET_GOOGLE_OPERATIONS` | recorded Google operations per UTC month | Google project quota and the D7-CAL/D7-MAIL budget |
| `MAULWURF_BUDGET_WARNING_RATIO` | ratio | Operator policy; default `0.8` |

Do not copy a number from this file into a deployment without the provider contract and the
approved budget. The F0/F1 evidence published no price, quota or capacity limit, so inventing
one would violate guardrail 10. The ASR usage is a lower bound: S3.5 measured 60.5 effective
minutes per class hour including the local overlap, while the metric sums source duration.
Google usage counts sent notifications, Calendar task-event writes and authorized remote
deletions; manual Calendar reads and token refreshes are not durably recorded.

## Alert response

| Alert | First action |
|---|---|
| `MaulwurfBudgetWarning` | Confirm expected traffic, review the unit and projection, decide whether to reduce or raise the cap. |
| `MaulwurfBudgetExhausted` | Stop the paid path or record an explicit cap increase. Do not hide a retry loop. |
| `MaulwurfBudgetNotConfigured` | Set the cap from the approved budget before accepting real load. |
| `MaulwurfCleanupFailed` / `MaulwurfIngestAdmissionClosed` | Follow the cleanup/recovery runbook; admission stays closed until cleanup is verified. |
| `MaulwurfCleanupUnrecorded` | Follow cleanup runbook §6; the attempt is pending and the text outbox is still gated. |
| `MaulwurfOutboxLag` | Check worker/scheduler, Redis and PostgreSQL; if a provider is down, follow the provider runbook. |

## Without Prometheus

`scripts/ops/budget_check.py` reads the same endpoint and exits non-zero on warning or
exhaustion. It is suitable for a cron, a systemd timer or a Uptime Kuma push check:

```bash
python scripts/ops/budget_check.py --url http://api:8000/internal/metrics
python scripts/ops/budget_check.py --require-configured --format json
```

## Verification

The local execution record is `docs/sprints/artifacts/J4.8/`. C-S4-JF-10 requires a drill in
the reference environment; while J4.7 is absent, the local compose/PostgreSQL drill is
recorded and the reference drill stays explicitly **NOT RUN**.

# Runbooks

Executable operator procedures. Every step is meant to be run as written; the
reference-environment status is stated per document instead of being implied.

| Runbook | Scope | Owner ticket |
|---|---|---|
| [`cleanup.md`](cleanup.md) | Failed cleanup, admission halt, the 60 s sweeper and host-loss restoration; no persistent audio | S4.1 |
| [`restore.md`](restore.md) | D8 encrypted text/metadata backup, clean restore and tombstone replay | A4.5 |
| [`budget-alerts.md`](budget-alerts.md) | Monthly LLM/ASR/Google budgets, alert rules and escalation | J4.8 |
| [`provider-down.md`](provider-down.md) | Riva, LLM, embeddings, Calendar and Gmail outages | J4.8 |
| [`tokens.md`](tokens.md) | Revoked, leaked or rotated credentials; dual-key ingest rotation | J4.8 |
| [`account-deletion.md`](account-deletion.md) | `DELETE /me`, status, resume, residual work and retention | J4.8 |
| [`deploy.md`](deploy.md) | Reference production Compose (`infra/docker-compose.prod.yml`), single-job Alembic migration, healthchecks, and rollback procedure | J4.7 |

> **Note on live-host drills:** [`deploy.md`](deploy.md) and `infra/docker-compose.prod.yml`
> are merged on `develop`, while live reference-host rehearsals (`D4.7` / `A4.8` / `S4.6`)
> and target-host runbook drills (`C-S4-JF-10` on the provisioned ARM VPS) remain recorded
> as **NOT RUN** until external credentials and host access are executed.

The executable evidence for C-S4-JF-10 is in
[`docs/sprints/artifacts/J4.8/`](../sprints/artifacts/J4.8/). The monitoring wiring lives in
[`infra/alerting/`](../../infra/alerting/).

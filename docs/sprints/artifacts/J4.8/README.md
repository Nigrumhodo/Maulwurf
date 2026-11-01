# J4.8 — C-S4-JF-10 execution record

Date (UTC): 2026-11-01. Ticket: J4.8 (`MAULW-146`). Branch: `task/MAULW-146`.

The reference environment required by C-S4-JF-10 is J4.7's deployment (ARM host, domain,
Caddy, versioned images, single Alembic job, rollback). **J4.7 is not present in this
repository and this sandbox has no Docker/podman.** The drills below therefore run against
the local reference-equivalent pieces: local PostgreSQL 16.15, Redis 7.0.15, the real API
and ingest packages, the real backup/restore script and deterministic provider doubles.
Every reference-only step is marked **NOT RUN**, not reported as passed.

| Runbook | Command / artifact | Local result | Reference step |
|---|---|---|---|
| Budget and alerts | [`budget-alerts.txt`](budget-alerts.txt) | Usage, warning, exhausted and unconfigured states on `/internal/metrics`; `budget_check.py` exits 1/2/0/1 as expected | **NOT RUN**: real Prometheus/Alertmanager, real billing account and approved caps |
| Cleanup/recovery (S4.1) | [`cleanup-status.txt`](cleanup-status.txt) | `status --check` detected a persisted `cleanup_status=failed` attempt and exited 3; no audio/path printed | **NOT RUN**: compose restart and host-loss attestation |
| Restore (A4.5) | [`restore-drill.txt`](restore-drill.txt) | Encrypted backup + deletion log, `export-deletions --refresh-backup`, `verify`, clean restore and tombstone reapply; deleted account did not reappear | **NOT RUN**: real backup volume, secret store and retention policy |
| Provider down | [`provider-down.txt`](provider-down.txt) | Closed-port LLM/embeddings probes return typed errors; Riva/Google deterministic suites 27 + 59 passed | **NOT RUN**: real Riva/LLM/embeddings/Google outage or quota |
| Tokens | [`tokens.txt`](tokens.txt) | Dual-key ingest configuration and revocation suites 27 + 59 passed; no secret printed | **NOT RUN**: real Google/NVIDIA revocation and secret-store rotation |
| Account deletion | [`account-deletion.txt`](account-deletion.txt) | I-S4-AN-04 13 passed against real PostgreSQL + Redis | **NOT RUN**: real Google account and reference-host `DELETE /me` |

Local gates for the code, tests and documentation are in
[`local-gates.txt`](local-gates.txt).

## What is new in this ticket

- `apps/api/app/services/provider_budget.py` — pure budget evaluation plus cross-tenant
  usage queries for LLM tokens, ASR source minutes and recorded Google operations.
- `/internal/metrics` — budget samples alongside the existing outbox series.
- `MAULWURF_BUDGET_*` settings — optional monthly caps; unset is measure-only.
- `scripts/ops/budget_check.py` — dependency-free alert check with documented exit codes.
- `infra/alerting/` — Prometheus rules, scrape example and deployment notes.
- `docs/runbooks/budget-alerts.md`, `provider-down.md`, `tokens.md`,
  `account-deletion.md` and the runbook index; `cleanup.md` and `restore.md` keep their S4.1
  and A4.5 procedures and are exercised here.
- `infra/docker-compose.yml` — passes the ingest previous-key pair for rotation; `api`
  never receives it.

## Explicit non-claims

- No real provider call, real credential, real Google account or approved budget was used.
- The budget usage is an approximation: ASR is source minutes (lower bound vs billed
  minutes) and Google is recorded operations (manual reads/token refreshes excluded).
- There is no Prometheus/Alertmanager service in the local eight-service stack and no
  reference deployment in this repository.
- Encryption-key rotation across versions is not implemented; the runbook records it as a
  stop condition.

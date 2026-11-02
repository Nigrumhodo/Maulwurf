# ADR-0005 — Redis policy

- Date (UTC): 2026-09-25
- Status: Accepted (merged in #17 with review; J1.6)
- Ticket: J1.6
- Related: [../ESPECIFICACION.md](../ESPECIFICACION.md) §3.1 and §8,
  [../PLAN_IMPLEMENTACION.md](../PLAN_IMPLEMENTACION.md) (G1, G5-F1),
  [../plan/S1.md](../plan/S1.md) §1.3 and §5 (J1.6)

## Context

ARQ uses Redis as job queue and job state store. The durable outbox lives
in PostgreSQL: jobs can be regenerated from the DB if Redis is lost
(J2.3). The risk is turning Redis into an unaudited store of audio,
transcripts, prompts, tokens, or PII, and into a second source of truth
with unreviewed durable persistence.

## Decision

| Topic | Decision | Reason |
|---|---|---|
| Source of truth | PostgreSQL (`outbox_events`) | Redis is dispensable; jobs are rebuilt |
| Redis persistence | Disabled: `--save "" --appendonly no`, no volume | Avoids unaudited durable copies |
| Eviction | `maxmemory-policy noeviction` | Visible failure instead of silently evicting jobs |
| `maxmemory` | `${REDIS_MAXMEMORY:-256mb}` (provisional) | Caps the container; revisited in S2/S4 |
| Value threshold | **4 KiB per value** | IDs, states, counters, and short ARQ results; nothing larger belongs in Redis |
| Key catalog | `arq:*` (queues `arq:queue` and `arq:scheduler`, health keys `arq:queue:health-check` and `arq:scheduler:health-check`), `mw:*` IDs/status only. `mw:limits:<scope>:<opaque identity>:<window>` is A4.6's fixed-window counter (hashed peer/user identity, TTL = window + 1 s; never a raw IP) | Every new key is documented here |
| TTL | ARQ results `keep_result=60` s; health keys 30–60 s | No long-lived state |
| Forbidden | Audio, transcripts, texts, prompts, tokens, credentials, PII | G1 and non-negotiable rules |
| Exposure | No port to the host; locally only `127.0.0.1:6379` for the sweep test | Shrinks surface without blocking I-S1-JF-03 |

Changing any of these rows requires updating this ADR and the compose file in
the same PR.

## Consequences

- Losing Redis (restart or `FLUSHALL`) loses queued jobs, not data:
  the PostgreSQL outbox regenerates them (J2.3). Test I-S2-JF-03 will verify it.
- With memory full, ARQ fails to enqueue instead of evicting; J2.5 must alert.
- Test I-S1-JF-03 sweeps keys and sizes against the 4 KiB threshold, and
  checks `aof_enabled=0`, empty `save`, and `noeviction`.
- This ADR does not decide provider, embedding, or index dimension policies.

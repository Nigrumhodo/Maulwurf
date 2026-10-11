# ADR-0006 — Tenant isolation in S1

- Date (UTC): 2026-09-27
- Status: Accepted (merged in #24 with review; A1.5)
- Ticket: A1.5 (verified by A1.9)
- Related: [../ESPECIFICACION.md](../ESPECIFICACION.md) §6,
  [../plan/S1.md](../plan/S1.md) §3 (A1.5, A1.9),
  [../PLAN_IMPLEMENTACION.md](../PLAN_IMPLEMENTACION.md) (G3)

## Context

A1.5 asks for "base RLS or mandatory filtering by `user_id` in all queries (decision
documented in an ADR)". Both routes prevent user B from reading or mutating A's resources:

- **Application-level filtering:** every query on an owned table carries
  `WHERE user_id = :session_user_id`.
- **PostgreSQL RLS:** per-table policies that hide rows belonging to others even if the
  query does not filter, with the user set per connection (`SET LOCAL app.user_id`).

RLS requires a DB role that owns none of the tables, setting the user per transaction so
the pool does not drag it across requests, and separate roles for migrations and workers.
In S1 only `users`, `sessions`, `google_credentials`, `subjects`, and the S1
ingestion/outbox shape exist.

## Decision

| Topic | S1 | S2 |
|---|---|---|
| Mechanism | Mandatory application-level filtering | Add RLS on top of filtering |
| Single point | Repository layer that receives the `user_id` from the authenticated session; routers do not build queries | Same, plus `SET LOCAL app.user_id` per transaction |
| Integrity | Composite FKs `(user_id, id)` on every owned row | Same |
| `user_id` origin | Only the backend session; never payload, path, or LLM output | Same |
| Foreign resource | `404` (do not reveal existence) | Same |
| Workers | Receive IDs and re-read the row's `user_id` from the DB | Own DB role with explicit policy |

## Consequences

- A query written outside the repository can bypass the filter: review rejects it and
  A1.9 (I-S1-AN-05) detects it by guessing A's IDs from B.
- Composite FKs prevent linking resources of two users even if a filter fails.
- Moving to RLS in S2 does not change the API or A1.9's tests; it adds a second barrier.
- Changing this decision requires updating this ADR and `docs/plan/S1.md` in the same PR.

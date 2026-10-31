# ADR-0006 — Tenant isolation in S1

- Date (UTC): 2026-09-27
- Updated (UTC): 2026-10-31 (A2.17 implementation and explicit accepted risk)
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

## S2 implementation (A2.17)

Revision `0005_s2_row_level_security` implements the S2 column of the table above:

- **Policies:** every tenant table that exists today gets `ENABLE` and
  `FORCE ROW LEVEL SECURITY` plus a `FOR ALL ... USING ... WITH CHECK` policy. `users`
  is keyed by `id`; the rest by `user_id`. Tenant tables use `current_setting('app.user_id')`
  without `missing_ok`, so a query without the setting raises instead of returning rows.
- **Roles:** `maulwurf_app` (API), `maulwurf_jobs` (worker/scheduler) and `maulwurf_ingest`
  (ingest) are `NOLOGIN`, `NOSUPERUSER` and `NOBYPASSRLS`. The DSN user keeps ownership and
  migration privileges; each runtime transaction downgrades itself with
  `SET LOCAL ROLE` (API in `app/core/db.py`, ingest in `maulwurf_ingest/rls.py`), so the
  constrained role is the effective user for RLS and grants.
- **Tenant binding:** the API sets `SET LOCAL app.user_id` as soon as the opaque session
  resolves and keeps it in a `ContextVar`, so every later transaction in the request (and
  its SSE generators) re-applies it from the engine `begin` listener. `SET LOCAL` is
  transactional and does not leak through the pool.
- **Bootstrap exceptions:** login and session resolution run before the tenant is known.
  `users` has a narrow `SELECT`/`INSERT` policy keyed by the verified Google `sub`
  (`app.google_sub`) and `sessions` a `SELECT` policy keyed by the presented token hash
  (`app.session_token_hash`). These are the only reads that do not need `app.user_id`;
  every other table fails closed.
- **Cross-tenant jobs:** the dispatcher, reconciler and index claim are cross-tenant by
  design. They run as `maulwurf_jobs` with a `jobs_cross_tenant` policy gated by
  `app.cross_tenant='on'`, set only by those processes. The tenant policies are scoped
  `TO maulwurf_app, maulwurf_ingest`, so a jobs transaction never evaluates the strict
  `app.user_id` expression. The internal `/internal/metrics` aggregate is the one API-side
  read that switches to this role (read-only, internal-only, aggregate counts). The ingest
  attempt queue has an explicit `ingest_attempt_queue` policy for `maulwurf_ingest`;
  transcript commits and the cleanup gate still bind `app.user_id`.
- **Migrations:** `alembic upgrade head` runs as the DSN owner. A data migration that reads
  or rewrites tenant rows needs a superuser or `BYPASSRLS` migration account (documented in
  [`../RUNBOOK-dev.md`](../RUNBOOK-dev.md) §5.1); the runtime roles never get it. S3/S4
  tables add their own policies and grants in their own migrations.

### Accepted risk: cross-tenant ingest attempt queue

`ingestion_attempts` carries a permissive policy for `maulwurf_ingest`
(`ingest_attempt_queue`, `USING (true)` / `WITH CHECK (true)`), so that role can read and
write every tenant's attempt row. This is an explicit accepted risk, not an oversight:

- **Why it stays in A2.17:** the admission latch counts the in-flight attempts of the whole
  instance (global capacity), and the post-commit reconciler, the stranded sweep and lease
  recovery run without a request, before or after any `app.user_id` exists. A
  `SECURITY DEFINER` function would move the same SQL behind a fixed interface, but it
  would still execute with the owner's cross-tenant rights: it reduces the call surface,
  not the capability. Doing it now would also move the lease/heartbeat/cancel/sweep state
  machine out of `maulwurf_ingest` and require re-testing the J2.4/S2.x failure matrix,
  outside this ticket's scope.
- **Blast radius if `maulwurf_ingest` is compromised:** read/write of operational metadata
  in `ingestion_attempts` for every tenant (ids, statuses, timings, byte counts, error
  codes, `user_id`) and disruption of the ingest state machine. The role cannot read audio
  (it is never persisted), transcripts, segments, chunks, embeddings, conversations,
  sessions or Google credentials: those tables stay tenant-bound for this role, and
  `audios`, `subjects` and `outbox_events` require `app.user_id`.
- **Existing limits:** the role is `NOLOGIN`, `NOBYPASSRLS`, owns no table and is used only
  by the ingest service DSN user; `ingest_attempt_queue` is scoped to that role alone, so
  the API and jobs roles remain tenant-bound on `ingestion_attempts`.
- **Review trigger:** revisit when S3 adds analysis, Calendar or Gmail work to the queue, if
  another service needs more than queue metadata, or if the ingest network boundary stops
  containing a compromise. The preferred direction is a `SECURITY DEFINER` queue API:
  per-tenant operations for request-bound work and one narrow cross-tenant function for
  the sweeps.

### Runtime identity debt (not changed by A2.17)

RLS here is a second barrier against application bugs, not a boundary against a compromised
database credential. Today `infra/docker-compose.yml` authenticates the API, the workers and
ingest with the migration owner (a superuser in the local image), and `0005` grants that owner
membership in all three runtime roles so one DSN can `SET LOCAL ROLE`. Outside the `begin`
listener, or through an injected statement inside it, that DSN runs as the owner: `FORCE` does
not restrict a superuser, and the owner can `SET ROLE maulwurf_jobs` plus
`app.cross_tenant='on'`.

That is acceptable for local development only. Staging and production must authenticate each
runtime with a dedicated `LOGIN` role that is a member of only its own runtime role, owns no
table and is not a superuser; `/internal/metrics` then needs a separate jobs-capable identity
or a dedicated read path. Splitting the DSNs (Compose profiles plus deployment manifests) and
adding a startup check that rejects an owner or superuser runtime DSN is follow-up work, not
part of MAULW-177 (it changes credentials and deployment configuration, not the policies).



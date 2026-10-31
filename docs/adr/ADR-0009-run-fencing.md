# ADR-0009 — Run fencing for the API `processing_runs` ledger

- Date (UTC): 2026-10-30
- Status: Accepted
- Ticket: A2.20 (Hoplite C4), tests I-S2-JF-15
- Changes: Alembic revision `0004_s2_run_fencing`; `apps/api/app/models/transcript.py`;
  `apps/api/app/services/{index_job,reconciliation}.py`;
  `apps/api/app/workers/{index_job,reconciler}.py`
- Related: [ADR-0006](ADR-0006-aislamiento-tenant.md) (tenant filtering),
  `docs/plan/S2.md` §A2.20 (ticket), `docs/PLAN_TESTS.md` §2.4 (I-S2-JF-15)

## Context

`processing_runs` is the lease and idempotency ledger of the API index job (J2.2). A delivery
claims the row of `(audio_id, stage, transcript_version, config_version)` by moving it to
`running`; the status plus `started_at` is the lease. The reconciler (J2.4) closes a `running`
row after `LEASE` as `failed/lease_expired` and enqueues a recovery delivery.

Two races were possible because every update addressed the row by `id` only:

1. A worker that stayed silent past `LEASE` could call `finish_run` after the reconciler had
   closed the row, overwriting the terminal status and hiding the recovery.
2. The same worker could still activate the generation it was building after the reconciler
   had reclaimed the run and orphan cleanup had discarded (or another claim had rebuilt) that
   generation, so a stale build could retire the active index.

The ingestion side already fences its attempts with a monotonic `fencing_token`; the API run
ledger had no equivalent (`docs/plan/S2.md` §A2.20).

## Decision

- **Token.** `processing_runs` gets `fencing_token BIGINT NOT NULL DEFAULT 0`. It is internal
  to the ledger and never exposed by the API. Existing rows start at `0`.
- **Claim.** Every successful claim increments the token inside the existing `FOR UPDATE`
  transaction and returns `RunClaim(run_id, fencing_token)`. Retry/reset (`services/runs.py`)
  keeps its behavior: it only moves a `failed|cancelled` row to `pending`, never to `running`,
  so it is not a claim and does not take a token.
- **Completion.** `finish_run` is `UPDATE ... WHERE id = :id AND fencing_token = :token AND
  status = 'running' RETURNING id`. Zero rows is `RunFenceOutcome.FENCED`, a typed no-op that
  the worker logs as `index.noop reason=run_fenced`; it never rewrites a terminal status.
- **Activation.** `require_run_fence` locks the run row with `SELECT ... FOR UPDATE` on the
  same `(id, token, status='running')` predicate in the **same transaction** as
  `activate_generation`. A mismatch raises `RunFenced` before any generation write, the
  worker ends the delivery as a no-op, and the transaction rollback leaves the active
  generation untouched. The row lock makes a concurrent reconciler close serialize with the
  activation: either it happens first (and the fence fails) or it waits for the activation.
- **Reconciler.** `close_expired_run` is a compare-and-set on
  `(id, fencing_token, status='running', started_at < now - LEASE)`. It only re-enqueues when
  the update affected the row, so a lease renewed after the selection is never closed.
- **No separate lease owner.** There is no stable worker identity in the API workers today; a
  random owner string per claim would be a second token with no extra identity, and the
  monotonic token plus `started_at` already identifies the live claim. A future worker registry
  can add `lease_owner` without changing this contract.

## Consequences

- Revision `0004` adds the column with `upgrade`/`downgrade`; A2.18's migration must follow
  `0004`.
- Live workers keep the same retry and idempotency behavior: the token only changes on a new
  successful claim, and the claim path is unchanged for them.
- Finish and activation of a stale worker are no-ops. Its **build writes are not fenced yet**:
  a stale worker can still commit chunks/embeddings after orphan cleanup, and because those
  rows keep the same `index_version` the next build can adopt them. This is not "bounded wasted
  work" and orphan cleanup is not the only writer. A2.18 must add a per-batch write fence or
  prove the build idempotent under a generation lock, and its criteria record this gap.
- The column is not part of `ESPECIFICACION.md`'s functional contract; the DDL sketch in
  `docs/DISENO_BD_API_SPRINTS.md` is a design reference and does not list this internal column.

## Alternatives rejected

- **Status-only guard (`UPDATE ... WHERE status='running'`).** A re-claimed run is `running`
  again, so the old worker would still win.
- **Advisory locks per run.** They are scoped to a database connection, not durable across a
  worker death, and the reconciler runs in another connection.
- **Deleting or tombstoning the run on lease expiry.** It would break the unique-key
  idempotency and the retry/reset path.

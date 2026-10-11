# Architecture decisions (ADR)

Short ADRs. Prefix `ADR-`. No secrets. ADR-0001–0004 are from L1.2; ADR-0005 (Redis)
is from J1.6 and ADR-0006 (tenant isolation) from A1.5. ADRs are historical records:
scaffold-era statements inside ADR-0001–0003 (e.g. "infra/ still placeholder") are
already resolved by J1.x and are kept as decision context.

| ID | Title | Status |
|---|---|---|
| [ADR-0001](ADR-0001-monorepo.md) | Monorepo structure and area boundaries | Accepted |
| [ADR-0002](ADR-0002-versiones.md) | Runtime and provider versions | Accepted |
| [ADR-0003](ADR-0003-lockfiles.md) | Lockfile policy | Accepted |
| [ADR-0004](ADR-0004-commits-pr.md) | Commit/PR format and reviews | Accepted |
| [ADR-0005](ADR-0005-redis.md) | Redis policy | Accepted |
| [ADR-0006](ADR-0006-aislamiento-tenant.md) | Tenant isolation in S1 (filtering; RLS in S2) | Accepted |

## L1.2 verification

- [x] The four ADRs exist with the `ADR-` prefix in this directory.
- [x] ADR-0002 matches [../VERSIONES.md](../VERSIONES.md) and the lockfiles of
  `apps/api`, `apps/ingest`, and `apps/web` (Python 3.12, Node 22,
  `nvidia-riva-client==2.27.0`).
- [x] Role review (API, ingestion, web, infra, leadership) in the ticket PR. S1
  already has claims accepted and merged in
  [../sprints/CLAIMS.md](../sprints/CLAIMS.md); the review happened when merging
  each PR, not in this file.

The verifiable artifact is the documentation diff of these ADRs.

## J1.6 verification

- [x] ADR-0005 reviewed and marked `Accepted` when merging the J1.6 PR (#17).
- [x] The policy matches `infra/docker-compose.yml` (no persistence,
  `noeviction`, provisional 256 MB cap, loopback-only port) and
  I-S1-JF-03 (`apps/api/tests/integration/test_redis_policy.py`).

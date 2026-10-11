# Maulwurf scaffold — what this script generated and what is missing

This tree was created by `scripts/scaffold_monorepo.sh` (the leader's L1.1 task).
It corresponds to the §3.3 structure of `docs/ESPECIFICACION.md`.
Pinned runtime versions are in [docs/VERSIONES.md](docs/VERSIONES.md).

> **Historical document (2026-09).** It describes the tree the scaffold left behind before
> sprint S1. The current state lives in [README.md](README.md),
> [docs/RUNBOOK-dev.md](docs/RUNBOOK-dev.md), and [docs/sprints/CLAIMS.md](docs/sprints/CLAIMS.md);
> the "Next steps" below have already landed (alembic, F0 spike, compose/Caddy/CI, ADRs).

## Backlog areas (sprint plan, §0.2 of the master plan)

The letter designates the **area**, not a person: tickets are claimed every Monday per
`docs/PLAN_SPRINTS.md` and recorded in the logbooks under `docs/sprints/`.

| Path | Typical area |
|---|---|
| `apps/api/` | `A-` API/DB (except `prompts/` → `L-` RAG, `app/workers/` → `J-` Infra) |
| `apps/ingest/` | `S-` Ingestion/ASR |
| `apps/web/` | `D-` Web/UX |
| `infra/` | `J-` Infra/Integrations |
| `scripts/` | coordination (leader) |
| `apps/api/prompts/` | `L-` RAG |

## Next steps per ticket

1. **`A1.x` (API/DB):** `pip install fastapi "uvicorn[standard]" "pydantic-settings"` etc.;
   `alembic init alembic`; Google auth + opaque session.
2. **`D1.x` (Web):** `npx create-next-app@latest apps/web --typescript --tailwind --app`;
   then `npx shadcn@latest init`.
3. **`S1.x` (Ingestion/ASR):** F0 spike with `requirements-riva.txt`; ephemeral prototype.
4. **`J1.x` (Infra):** `infra/docker-compose.yml` + `Caddyfile` + CI.
5. **`L1.x` (RAG/leadership):** ADRs in [docs/adr/](docs/adr/README.md).

> Rule: nothing here is implemented. TODOs mark pending work; owners are claimed every
> week, they are not pre-assigned.

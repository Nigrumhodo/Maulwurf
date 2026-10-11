# ADR-0001 — Monorepo structure and area boundaries

- Date (UTC): 2026-09-23
- Status: Accepted
- Ticket: L1.2
- Related: [ESPECIFICACION.md](../ESPECIFICACION.md) §3.1 and §3.3,
  [README.scaffold.md](../../README.scaffold.md)

## Context

The scaffold (`scripts/scaffold_monorepo.sh`) already materialized the tree. The
§3.1 diagram includes an ingestion service; the §3.3 listing does not name
`apps/ingest`. Folder boundaries per area (backlog letter, not person) must be
set for reviews and claims.

## Decision

The monorepo has these product roots:

```text
apps/api/      FastAPI, models, Alembic, versioned prompts
apps/ingest/   Ephemeral ingestion and ASR (included even though missing from §3.3)
apps/web/      Next.js App Router
infra/         Compose, Caddy, CI (J1.x; still placeholder)
scripts/       Scaffold, spikes, and load
docs/          Contracts and planning
```

Area boundaries (claiming; the letter does not assign an owner):

| Path | Area |
|---|---|
| `apps/api/` with exceptions | `A-` API/DB |
| `apps/api/prompts/` | `L-` RAG |
| `apps/api/app/workers/` | `J-` Infra |
| `apps/ingest/` | `S-` Ingestion/ASR |
| `apps/web/` | `D-` Web/UX |
| `infra/` | `J-` Infra/Integrations |
| `scripts/` coordination | leadership (`L-`) |

A PR that edits another area requires review from whoever holds an active claim
there, or from the lead if there is no claim. Parallel service names are not
invented for `TranscriptionService`, `SearchService`, `RAGService`,
`GoogleCalendarService`.

## Consequences

J1.1 places compose and Caddy only in `infra/`. Shared contracts are frozen on
Monday; an interface change updates the spec and the plan together.

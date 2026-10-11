# ADR-0002 — Runtime and provider versions

- Date (UTC): 2026-09-23
- Status: Accepted
- Ticket: L1.2
- Related: [VERSIONES.md](../VERSIONES.md) (L1.1),
  [ESPECIFICACION.md](../ESPECIFICACION.md) §3.2 and §9.2

## Context

L1.1 verified reproducible installations. J1.1 and J1.4 must copy the
same figures. The spec declares stack and CI; F0 has not yet closed
provider contracts.

## Decision

Development and future CI runtimes:

| Runtime | Pin |
|---|---|
| Python | 3.12 (`requires-python = ">=3.12"`) |
| Node.js | 22 (`engines.node` `>=22 <23`) |
| PostgreSQL | 16 |
| pgvector | image `pgvector/pgvector:pg16` |
| Riva client | `nvidia-riva-client==2.27.0` |

Files: `.python-version`, `.nvmrc`, `apps/*/pyproject.toml`,
`apps/web/package.json`, `requirements-riva.txt`.

CI: **GitHub Actions** (team decision 2026-09-20). Candidate registry:
GitHub Container Registry. The exact runner and image are confirmed in J1.4;
the `trigger-hoplite-qa.yml` workflow is not the quality pipeline.

Product providers (Whisper large-v3, §3.2 LLM/embeddings) remain
**provisional** until F0 and D2/D3. This ADR does not set quotas, sample
rate, or payload limits.

## Consequences

Changing Python, Node, PG, or the Riva pin updates `VERSIONES.md`,
manifests, and lockfiles in the same PR. Mixing embedding dimensions or
versions requires a new index (spec), not this ADR.

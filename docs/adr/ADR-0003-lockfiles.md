# ADR-0003 — Lockfile policy

- Date (UTC): 2026-09-23
- Status: Accepted
- Ticket: L1.2
- Related: [ADR-0002](ADR-0002-versiones.md), `.gitignore`

## Context

Without committed lockfiles, installations are not reproducible (L1.1
criterion). What enters git and how it is updated must be stated.

## Decision

Versioned:

- `apps/api/uv.lock`
- `apps/ingest/uv.lock`
- `apps/web/package-lock.json`
- `requirements-riva.txt` (aligned with `nvidia-riva-client==2.27.0`)

Not versioned: `.venv/`, `node_modules/`, `.env`, caches, audio
(`*.wav` and the rest of the `.gitignore` extensions), dumps, secrets.

Installation:

```text
cd apps/api && uv sync --extra dev
cd apps/ingest && uv sync --extra dev
cd apps/web && npm ci
```

`npm install` without a lock only to regenerate it; the PR includes the new
lock. A dependency change updates the manifest and the lock in the **same**
PR. CI does not resolve blindly: J1.4 will use `uv sync --frozen` / `npm ci`
once the pipeline exists.

## Consequences

Reviews reject PRs that add packages without a lock, or that commit
`.venv` / `node_modules` / `.env`. Scanning dependency vulnerabilities
remains pending (TODO in AGENTS.md); this ADR does not replace it.

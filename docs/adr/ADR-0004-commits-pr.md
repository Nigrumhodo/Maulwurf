# ADR-0004 — Commit/PR format and reviews

- Date (UTC): 2026-09-23
- Status: Accepted
- Ticket: L1.2
- Related: [docs/sprints/README.md](../sprints/README.md)

## Context

There was no commit template or hook. AGENTS.md asked to define the format
before enforcing it. PR rules already exist in the sprint model.

## Decision

### Commits

- Imperative subject (English or Spanish), around 72 characters.
- Body: the **why**, not the file listing, when it is not obvious.
- Include the ticket ID (`L1.2`, `S1.A1`, …) when the change closes or
  advances a ticket.
- No `--no-verify` or skipped hooks except by explicit author request.
- No commit-lint or husky in this ticket: the rule is documentation only.

### Pull requests

- Base: `develop`.
- Body: summary (1–3 points) and a test plan or verification checklist.
- At least **one** review before merge.
- If the diff crosses an area, review by whoever holds an active claim in that
  folder, or by the lead if there is no claim.
- A claim PR touches **only** its row in `docs/sprints/CLAIMS.md`.
- Do not merge secrets, `.env`, audio, private transcripts, or provider
  payloads.

## Consequences

J1.4 can add CI checks later without changing this rule. The AGENTS.md
privacy/provider/deployment reviews remain separate gates.

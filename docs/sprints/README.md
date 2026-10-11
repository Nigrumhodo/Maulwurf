# Sprint logbooks — Maulwurf

This directory contains the team's **personal logbooks**. The master plan with the weekly
backlog is [../PLAN_SPRINTS.md](../PLAN_SPRINTS.md); the test catalog that evidences each
ticket is [../PLAN_TESTS.md](../PLAN_TESTS.md); the contracts source is
[../ESPECIFICACION.md](../ESPECIFICACION.md) and the gates/evidence in
[../PLAN_IMPLEMENTACION.md](../PLAN_IMPLEMENTACION.md).

## How claiming works

1. **Tickets are not pre-assigned.** Each week publishes its backlog in the master plan
   (§2–§5), grouped by area (`L-` RAG/leadership, `A-` API/DB, `S-` Ingestion/ASR,
   `J-` Infra/Integrations, `D-` Web/UX) with dependencies and tests.
2. **On Monday** only the active sprint opens, after reviewing the previous gate,
   capacity, and availability. Each person picks tickets and claims them through a PR in
   the [canonical registry](CLAIMS.md). **One ticket = one owner.** The personal logbook
   keeps evidence and blockers, but does not assign a ticket by itself.
3. Rows pre-filled in logbooks before the 2026-09-19 review are historical candidates,
   not valid claims. S2–S4 are not claimed until they formally open.
4. Whatever nobody claims is resolved by the lead: reassign, split, or explicitly lower
   the DoD scope. A split creates child IDs in the backlog before the claim; a transfer
   keeps the previous owner, date, motive, and evidence.
5. Restrictions: a ticket that includes a `P-*` test or a real call to Riva,
   LLM/embeddings, Google, or Gmail requires verified eligibility for the protected
   account (without recording secrets); a ticket touching another area's folders needs
   review from whoever holds an active claim there or from the lead.
6. **Evidence over claims:** a ticket closes with a real command, date, environment, and
   result recorded in the logbook. A mock does not validate a provider; a unit test does
   not validate infrastructure; nothing is `done` just for being written down.

## Team logbooks

| Person | Suggested profile (not ownership) | Logbook | Handle in CLAIMS |
|---|---|---|---|
| José Leonardo Pinilla Zamora | Technical leadership · RAG | [jose-leonardo.md](jose-leonardo.md) | `jpinillaz` |
| Andres Felipe | API · Database | [andres.md](andres.md) | no claims yet |
| Santiago Montealegre | Ingestion · Audio · ASR | [santiago.md](santiago.md) | `satnovaOG` |
| Jefferson Figueroa | Infra · Workers · Integrations | [jefferson.md](jefferson.md) | no claims yet |
| Daniel Samaca | Web · UX | [daniel.md](daniel.md) | no claims yet |

Person↔handle correspondence observed in the repository history (authorship of
`santiago.md` by `satnovaOG`; `s1/*-jpinillaz` branches editing `jose-leonardo.md`).
Correct it in a PR if it changes.

Current ownership is consulted exclusively in [CLAIMS.md](CLAIMS.md).

The profile is a specialization suggestion, not a restriction: anyone may claim from any
area following the rules above.

## Coordination rules

1. **Frozen interfaces:** on Monday the contract of the shared interfaces is frozen (§6 of
   the master plan). Changing them during the week requires the leader's approval and
   updating consumers the same day.
2. **Anti-cycle (§6a of the master plan):** whoever provides an interface freezes the
   contract and an executable double/mock on Monday; delivers the first consumable
   integration no later than day 3 and closes behavior/E2E during days 4–5. Whoever
   consumes builds against the frozen contract and switches to the real code once
   available. Nobody stays blocked more than one day without a visible decision.
3. **PRs and reviews:** nothing merges without 1 review; code crossing area boundaries is
   reviewed by someone with an active claim in that folder or by the lead if there is no
   claim. Claim PRs touch only their `CLAIMS.md` row and merge as soon as they pass
   review; if two PRs claim the same ticket, the first one merged wins and the other is
   closed.
4. **Document priority:** if this plan conflicts with `ESPECIFICACION.md` or
   `PLAN_IMPLEMENTACION.md`, they win; update spec/plan in the same revision if a contract
   change is unavoidable.
5. **Backlog:** ideas outside the week's scope go to the F4 backlog; the lead prioritizes
   at the Friday retro.
6. **Cloud provider tests:** synthetic audio generated in RAM, protected test
   credentials, and an agreed budget; never real classes until D3 is resolved and G1
   passes.

## Weekly workflow

```
Monday: verify gate/capacity + open sprint + freeze interfaces + claims in CLAIMS.md
Daily: 15 min (status, blockers, visible transfers)
Day 3: first consumable integration; consumers switch mocks when appropriate
Days 4–5: E2E, technical closure, and evidence
Friday: demo + DoD verification + retro + logbook closure
```

## Logbook template

Each personal logbook keeps this minimum structure:

```markdown
# <Name> — sprint logbook

> Suggested profile: <area> · Master plan: [../PLAN_SPRINTS.md](../PLAN_SPRINTS.md) ·
> Tests: [../PLAN_TESTS.md](../PLAN_TESTS.md)

## Claimed tickets

### Week 1 (S1) — Foundation + spike

> Historical section: rows written before the 2026-09-19 review are candidates, not
> claims. Current tickets are recorded first in `CLAIMS.md`.

| Ticket | Area | Status (`pending/in progress/blocked/done/transferred`) | Evidence (command, date, environment, result) |
|---|---|---|---|
| <id> | <area> | pending | Claim accepted in `CLAIMS.md`: <PR/link> |

## Blockers and dependencies

- I expect from <who>: <what> (frozen contract §6 / real code on day <n>)

## Week notes

- <decisions, findings, scope deviations>
```

Template rules: one ticket per row and only after it appears in `CLAIMS.md`; the status
changes at the daily, not on Friday; a ticket marked `done` without evidence goes back
to `in progress`. Each week S2–S4 adds its own section only when the sprint is open.

## Links

- [CLAIMS.md](CLAIMS.md) — canonical registry of claims, transfers, and cloud eligibility.
- [../PLAN_SPRINTS.md](../PLAN_SPRINTS.md) — master plan: weekly backlog, dependencies, DoD.
- [../PLAN_TESTS.md](../PLAN_TESTS.md) — test catalog per sprint: unit, integration, provider-contract, E2E, and load.
- [../DISENO_BD_API_SPRINTS.md](../DISENO_BD_API_SPRINTS.md) — DB/API design and suggested sequence per area.
- [../ESPECIFICACION.md](../ESPECIFICACION.md) — M1–M9 contracts and data model.
- [../PLAN_IMPLEMENTACION.md](../PLAN_IMPLEMENTACION.md) — phases F0–F3, gates G1–G8, decisions D1–D8.
- [../NVIDIA_RIVA.md](../NVIDIA_RIVA.md) — audited parameters for the S1 spike.

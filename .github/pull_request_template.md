<!--
Format per ADR-0004 (docs/adr/ADR-0004-commits-pr.md).
PR base: develop. One PR = one ticket. Minimum 1 review.
No secrets, .env, private audio/transcripts, or provider payloads.
A claim PR touches only its row in docs/sprints/CLAIMS.md.
-->

## Summary

<!-- 1–3 points: what changes and why. -->

-
-

## Ticket and claim

- Ticket: <!-- e.g. J1.1 -->
- Active claim: <!-- row in docs/sprints/CLAIMS.md or claim PR -->

## Test plan / verification

<!-- Exact commands and expected result. Evidence: command, UTC date, environment, and result. -->

- [ ]
- [ ]

## Evidence

<!-- Link to the logbook and redacted outputs; no sensitive data. -->

## Checklist

- [ ] At least 1 review; if the diff crosses areas, review by whoever holds an active claim in that folder or the lead
- [ ] Quality CI green (J1.4); `P-*` and `load` never run in MRs
- [ ] No secrets, `.env`, private audio/transcripts, or provider payloads in the diff
- [ ] `docs/ESPECIFICACION.md` and `docs/PLAN_IMPLEMENTACION.md` updated if a contract changed
- [ ] Row in `docs/sprints/CLAIMS.md` updated with the evidence (or separate claim PR)

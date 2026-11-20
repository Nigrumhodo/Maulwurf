# F4 Backlog & Project Retrospective (`L4.6`)

- **Date:** `2026-11-19`
- **Baseline:** Full `F1 + F2 + F3` codebase merged on `develop` (migrations `0001`–`0022`, API/ingest workers, React SPA, reference production Compose `infra/docker-compose.prod.yml`, 17 CI checks).

---

## 1. Brief Project Retrospective (S1–S4)

### What Worked Well
- **Strict G1 zero-audio boundary end-to-end:** Keeping raw audio ephemeral in memory/tmpfs and verifying deletion before publishing text jobs prevented accidental bloat, browser media leaks, and backup privacy hazards across `F1`, `F2`, and `F3`.
- **State machine separation:** Decoupling ingestion, cleanup verification, indexing, task analysis, Calendar outbox sync, and Gmail notification delivery kept `F1` resilient even when `F2`/`F3` integrations degrade.
- **Automated quality gates:** Deterministic `check_refs.py`, `check_caddy_limits.py`, `check_compose_ingest_env.py`, `check_no_secrets.sh`, strict `ruff`/`mypy`, TypeScript, and unit/component suites caught contract regressions early (including secret-scan false positives, Caddy route bounds, and opt-in prod defaults).

### Friction & Lessons Learned
- **External provider dependency drift:** Several F0/cloud contracts (`D2`, `D3-Audio`, `D3-Text`, `D4`, `D6`, Google OAuth consent verification, and live NVIDIA Riva limits) blocked final end-to-end cloud sign-off even after the full application layer finished.
- **Documentation drift across fast merges:** Rapid feature waves temporarily left `README.md`, `docs/sprints/CLAIMS.md`, `docs/plan/S4.md`, and `docs/runbooks/README.md` behind the actual code (`22` migrations, `DeliveryPanel`, `CommandPalette`, `SubjectStats`, `deploy.md`). Automated closure reconciliation resolves this before release.
- **Compose vs. host secrets hardening:** Standard Compose env interpolation enabled reproducible local and reference staging stacks, but full Docker secrets (`*_FILE`) and dedicated `egress`/`data` network segregation require target host provisioning.

---

## 2. Prioritized Post-MVP Backlog (F4)

### P0 — Mandatory Pre-GA Cloud & Operational Sign-Offs
1. **Close F0 External Provider Decisions (`D2`, `D3-Audio`, `D3-Text`, `D4`, `D6`):** Verify live NVIDIA Riva allowlist/durations/bytes (`J4.10`) and LLM/embedding quotas on staging credentials.
2. **Execute Live Google Integration Tests (`J3.1`, `J3.9`, `J4.9`):** Complete OAuth verification/Testing consent flow and record live Calendar CRUD + Gmail `gmail.send` (`P-S4-JF-07`) evidence.
3. **Complete Human Multilingual Eval (`L2.7`, `L4.1`):** Score live `G4`/`G6` faithfulness & retrieval thresholds across `es`/`en`/`fr` and publish final signed metrics.
4. **Reference Host Rehearsal & Security Sign-Off (`D4.7`, `A4.8`, `S4.6`, `L4.2`):** Run two end-to-end rehearsals + live rollback drill using `infra/docker-compose.prod.yml` and `docs/runbooks/deploy.md`, and complete independent peer signature in `docs/reviews/security-cross-review.md`.

### P1 — Production Infrastructure Hardening
5. **Docker Secrets (`*_FILE`) Migration:** Replace direct env interpolation in `infra/docker-compose.prod.yml` with Swarm/Compose secret files for DB, Redis, Google OAuth, and NVIDIA keys.
6. **Physical `egress` / `data` Network Isolation:** Enforce OS/firewall egress allowlists on the reference host alongside internal Compose network segmentation.
7. **Alerting & Grafana Dashboards:** Ship pre-built Grafana dashboards & Alertmanager rules wired to `infra/deploy/prometheus.yml` (outbox lag, `cleanup_unrecorded`, provider error budget, `delivery_unknown` rate).

### P2 — Product Differentiators (`ESPECIFICACION.md` §13)
8. **Study Mode (Spaced Repetition & Quizzes):** Generate review flashcards and practice quizzes strictly from verified transcript spans with citation links (zero ungrounded claims).
9. **Slide / Whiteboard Companion Notes (G1-compliant):** Allow image/PDF slide attachment alongside transcript segments (text/metadata indexed without retaining raw class audio).
10. **Markdown / Anki / Obsidian Export:** Export class summaries, key topics, and confirmed tasks in portable study templates.
11. **Multi-device Session & Read-Only Offline Sync:** Extend PWA capabilities strictly for exported user study bundles while keeping private API/transcript endpoints excluded per `apps/web/public/sw.js`.

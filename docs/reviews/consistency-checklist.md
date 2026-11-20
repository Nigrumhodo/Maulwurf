# Consistency Checklist — `PLAN_IMPLEMENTACION.md §9` (`L4.3`)

- **Ticket:** `L4.3`
- **Verification date:** `2026-11-19`
- **Baseline:** `develop@4bb2208` + Wave 4 closure docs
- **Scope note:** Evaluates codebase, schema, Edge/Compose wiring, documentation consistency, and automated test gates (`F1 + F2 + F3`). External live-provider and host operational gates remain explicitly flagged as **OPEN / EXTERNAL**.

## Checklist Evaluation

1. **`ESPECIFICACION` and `PLAN` link existing files; phases `F0–F3`, gate variants, and decisions `D1–D8` match.**
   - **Status:** `PASS`
   - **Evidence:** `scripts/docs/check_refs.py` verifies all referenced repository paths across `README.md`, `docs/ESPECIFICACION.md`, `docs/PLAN_IMPLEMENTACION.md`, `docs/DISENO_BD_API_SPRINTS.md`, `docs/plan/S4.md`, and runbooks. Phase scope (`F1` transcript/library/search/chat, `F2` task extraction/review/Google Calendar, `F3` Gmail reminders/notifications/account deletion/stats/Ctrl+K/PWA/prod reference deploy) and decisions `D1–D8` match across contracts and `docs/sprints/CLAIMS.md`.

2. **No new flow requires audio after cleanup, or introduces storage/player/retranscription without re-upload.**
   - **Status:** `PASS (automated) / OPEN (live host soak S4.3)`
   - **Evidence:**
     - Backend ingest/upload lifecycle: `apps/api/app/services/uploads.py`, `apps/api/app/services/orphan_sweep.py`.
     - Client UI & storage invariants: `apps/web/src/components/TranscriptReader.tsx`, `apps/web/tests/unit/g1-privacy.test.ts` (zero `<audio>` elements, zero `indexedDB`/`sessionStorage`, transient object URLs restricted exclusively to plain `.txt`/`.srt` export in `apps/web/src/lib/reader.ts`), and `docs/reviews/g1-client.md`.
     - Pending external check: live reference-host G1 load verification (`D4.7`, `S4.3`).

3. **Mandatory language before bytes; UI formats/limits equal to validated capabilities.**
   - **Status:** `PASS (application & edge alignment) / OPEN (NVIDIA D2/D3-Audio/D4 live confirmation)`
   - **Evidence:**
     - Language validation and capabilities contract: `apps/api/app/routers/ingestion.py`, `apps/api/app/services/limits.py`, `apps/web/src/lib/capabilities.ts`, `apps/web/src/components/upload/UploadForm.tsx`, `apps/web/src/components/upload/AudioUploadPanel.tsx`.
     - Edge limits: `infra/Caddyfile` (`max_size 220MB`, `read_body 40m`), `infra/deploy/Caddyfile.prod`, and `scripts/ci/check_caddy_limits.py`.
     - Pending external gate: final live-provider allowlist/ceiling confirmation under `D2`, `D3-Audio`, `D4`, and `J4.10`.

4. **ASR, cleanup, analysis, index, task, Calendar, and notification states remain separate; `F1` does not depend on `F2` analysis.**
   - **Status:** `PASS`
   - **Evidence:** Separate persisted state machines and services in `apps/api/app/services/state_machine.py`, `apps/api/app/services/index_job.py`, `apps/api/app/services/analysis.py`, `apps/api/app/services/task_lifecycle.py`, `apps/api/app/services/calendar_sync.py`, and `apps/api/app/services/notification_delivery.py`.

5. **Cleanup verification requires absence evidence; a persisted `failed` cleanup closes admission, `pending` only blocks the outbox, and an unrecorded cleanup raises `cleanup_unrecorded` plus a failure metric.**
   - **Status:** `PASS (unit/integration) / OPEN (live host drill)`
   - **Evidence:** Enforced across `apps/api/app/services/orphan_sweep.py`, `apps/api/app/services/outbox.py`, `apps/api/app/routers/internal_metrics.py`, and operational procedures in `docs/runbooks/cleanup.md`.

6. **The schema supports every API contract: dedupe decided in the `PUT`, versioned consent, `spans`/`task_sources`, agenda start/end, revisions, versions, and traceable deletion.**
   - **Status:** `PASS`
   - **Evidence:** 22 sequential Alembic migrations from `apps/api/alembic/versions/0001_core_schema_s1.py` through `apps/api/alembic/versions/0022_s3_chat_tool_calls.py`, paired with `apps/api/app/services/revisions.py`, `apps/api/app/services/evidence.py`, and `apps/api/app/services/deletion.py`.

7. **Consent, isolation, and human confirmation also apply to jobs, tools, and later changes.**
   - **Status:** `PASS`
   - **Evidence:** Tenant isolation & RLS (`apps/api/app/services/tenant.py`), read-only chat tools (`apps/api/app/services/chat_tools.py`), human-confirmed Calendar synchronization (`apps/api/app/services/calendar_sync.py`), and opt-in `gmail.send` with fixed verified recipient and escaped templates (`apps/api/app/services/gmail.py`, `apps/api/app/services/gmail_templates.py`).

8. **Private API responses are `Cache-Control: no-store` structurally (routes, errors, redirects and SSE); every rate limit is a recorded decision and answers `429`/`503` with `Retry-After`, failing closed when the limiter is unavailable.**
   - **Status:** `PASS`
   - **Evidence:** `apps/api/app/main.py`, `apps/api/app/services/rate_limiter.py`, `apps/web/src/lib/api.ts` (`cache: "no-store"`), and `apps/web/public/sw.js` (strict exclusion of `no-store`, `text/event-stream`, cross-origin, non-`GET`, and private API prefixes).

9. **All pending or blocked gates are reported; no documentary test is presented as application/provider validation.**
   - **Status:** `PASS`
   - **Open external / manual gates explicitly carried forward:**
     - External provider / F0 contracts: `D2` (live NVIDIA Riva language allowlist), `D3-Audio` (live Riva duration/format stress), `D3-Text` (live LLM/embeddings context & quota), `D4` (confirmed byte ceiling), `D6` (live Calendar/Gmail scopes & quotas).
     - Live integration & human quality gates: `L2.7` / `L4.1` (full live `G4`/`G6` multilingual human-scored eval), `J3.1` / `J3.9` (live Google Calendar OAuth & sync tests), `J4.9` (live `gmail.send` test `P-S4-JF-07`), `J4.10` (live NVIDIA Riva load stress).
     - Host & governance sign-offs: `D4.7` / `A4.8` / `S4.6` (2x reference-host rehearsals + live rollback drill against `infra/docker-compose.prod.yml` and `docs/runbooks/deploy.md`) and `L4.2` peer reviewer signature in `docs/reviews/security-cross-review.md`.

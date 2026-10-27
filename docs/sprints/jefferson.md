# Jefferson Figueroa — sprint logbook

> Suggested profile: Infra · Workers · Integrations (plan §0.2) ·
> Master plan: [../PLAN_SPRINTS.md](../PLAN_SPRINTS.md) ·
> Tests: [../PLAN_TESTS.md](../PLAN_TESTS.md) ·
> Contracts: [../ESPECIFICACION.md](../ESPECIFICACION.md) (§M5, §M8) ·
> Gates: [../PLAN_IMPLEMENTACION.md](../PLAN_IMPLEMENTACION.md) (G7-CAL, G7-MAIL, G8, D7)

## Claimed tickets

### Week 1 (S1) — Foundation + spike

> Historical section: rows written before the 2026-09-19 review are candidates, not
> claims; current tickets are recorded first in [CLAIMS.md](CLAIMS.md). Until a row exists
> there, this table stays empty.

| Ticket | Area | Status | Evidence (command, date, environment, result) |
|---|---|---|---|
| D1.1 | D | done | 2026-10-03 UTC, Windows, Node 22. Scaffold migrated from Next to Vite + React 19 + Tailwind 4 (ADR-0007). Branch `frontJJ` (`437b53e`). `npm run lint` / `npm run typecheck` / `npm run build` green. Merged in #42. |
| D1.2 | D | done | 2026-10-03 UTC, Windows, Node 22. Base routes (`/`, `/biblioteca`, `/chat`, `/materias`, `/settings`) with `RequireAuth`/`AppLayout`. `34701c1`; Cyber Y2K restyle in `c168669`. Merged in #42. |
| D1.3 | D | done | 2026-10-02/03 UTC, Windows, Node 22. Google login: button → `/auth/google/start`, callback with loading/error, logout with revocation. `b164313`, `6a6c4da` (backend OAuth A1.3 included). Merged in #42. |
| D1.4 | D | in progress | 2026-10-17 UTC, Windows, Node 22. Same-origin HTTP client: `lib/csrf.ts` (in-memory token) + `apiFetch` injecting `X-CSRF-Token` on mutations and interceptors 401→login / 403→message / 429→retry with `Retry-After`. `npm run typecheck && npm run lint && npm test && npm run build` → U-S1-DA-01: **15 tests green** (`ad75e7c`). No new dependencies (native fetch). Pending PR to `develop`. |
| D1.5 | D | done | 2026-10-18 UTC, Windows, Node 22. Base components in `components/ui/`: `DateInput`, `TimezoneSelect`, `LanguageSelect` (language **without preselection**, allowlist es/en/fr), `StatusBadge` (exact contract values §1.4/§2.10, incl. `transcript_committed_cleanup_pending` and aggregates), `EmptyState`, `Toast`+`ToastProvider`. Logic in `lib/{status,languages,timezones,validation}.ts`. `npm run typecheck && npm run lint && npm test && npm run build` → U-S1-DA-02 (9) and C-S1-DA-03 (5) green; **29 tests total**. Pending PR to `develop`. |
| D1.6 | D | done | 2026-10-18 UTC, Windows, Node 22. Subject CRUD against the real A1.6 API (`/subjects`): `SubjectForm` (required-name validation + `#rrggbb` color + API error mapping `subject_name_taken`/`validation_failed`) and `MateriasPage` (list/create/edit/delete + `ConfirmDialog` on `409 subject_has_active_audios`). `lib/subjects.ts` + `tests/unit/subjects.test.ts`. `npm run typecheck && npm run lint && npm test && npm run build` → U-S1-DA-02 extended (6 tests); **35 tests total**. Live CRUD verified in local compose. Pending PR to `develop`. |
| D1.7 | D | in progress | 2026-10-18 UTC, Windows, Node 22. Static drafts (no wiring): `components/upload/UploadForm.tsx` (versioned consent `2026-09-19` + two mandatory acknowledgements, no language preselection, "audio not kept") used by `SubirClasePage`, and `BibliotecaPage` (per-stage + aggregate `StatusBadge`, permanent "audio not kept" notice, differentiated buttons). Feeds D2.1/D2.4. Pending Friday team review (product/privacy/accessibility/Web) with screenshots. |

## Blockers and dependencies

- (every Monday: note whom you are waiting on for a frozen contract §6 or real code, and which day)
- The D1.4 `403` handler still uses `window.alert`; D1.5 already ships `ToastProvider`/`useToast`, wiring that handler to the toast remains as polish.
- Pending: PR of `frontJJ` to `develop` (D1.4–D1.5 still on the branch).

## Week notes

- 2026-10-17: Cyber Y2K restyle (palette, glitch, CRT, floating chat) and theme-aware login images.
- 2026-10-17: D1.4 implemented with native `fetch` (no axios).
- 2026-10-18: D1.5 complete (base components + tests). Navigation: the "académico" dropdown became the "Gestión" button → `/gestion` hub with a collapsible side menu (subir clase, biblioteca, chat, materias, tareas, agenda).
- 2026-10-18: Merged `origin/develop` into `frontJJ` (develop was far ahead: S2, A1.6 done). D1.6 implemented (subject CRUD) against the real `/subjects` API and verified live.
- 2026-10-18: D1.7 static drafts (upload form + library). UX decisions for D2.1/D2.4: consent versioned `2026-09-19` shown with two mandatory checkboxes before enabling submit; language select has no preselection; "audio not kept" is a permanent notice in both the form and the library; the library groups per stage (index/análisis) with the aggregate badge, and differentiates "volver a subir" (no transcript) from "reintentar análisis/índice" (transcript kept).

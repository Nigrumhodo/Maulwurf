# ADR-0007 — Web stack: Vite + React SPA instead of Next.js

- Date (UTC): 2026-10-11
- Status: Accepted (decided by the lead, jpinillaz)
- Ticket: L2.10 (implemented by D1.1–D1.3 in PR #42)
- Supersedes: the `apps/web` line of [ADR-0001](ADR-0001-monorepo.md) and the Frontend row
  of [ESPECIFICACION.md](../ESPECIFICACION.md) §3.2
- Related: [ARCH.md](../ARCH.md) §7, [VERSIONES.md](../VERSIONES.md)

## Context

The specification, ADR-0001 and the original scaffold used Next.js App Router. PR #42 rebuilt
`apps/web` with Vite, React 19, React Router and Tailwind 4, served by a small Express process.
Both cannot be the architecture, and every `D2.x` ticket was waiting for the decision.

The web app has no server-side duty in this product: authentication, CSRF, tenant checks, the
binary `PUT`, SSE and every private read live in `api` (and later `ingest`) behind the same
Caddy origin. The browser talks to them with `fetch`; nothing needs server rendering.

## Decision

`apps/web` is a client-side single-page application:

| Concern | Choice |
|---|---|
| Build | Vite 7, TypeScript 5, `@vitejs/plugin-react` |
| UI | React 19, React Router 7, Tailwind 4 |
| Runtime | Node 22 + Express 5 serving `dist/public` and the SPA fallback on port 3000, only static files, no environment variables and no secrets |
| Headers | The Express process sets CSP, `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy` and `Permissions-Policy` |
| Image | Multi-stage; the runtime keeps production dependencies only and runs `node` as PID 1 |

The fixed requirements stay unchanged: same origin behind Caddy, streaming `PUT` with `fetch`
(never a framework upload helper), SSE consumed with `fetch`, no private data in any cache,
and an optional PWA (D4.5) whose service worker excludes upload, API and transcripts.

## Consequences

- No SSR or Server Components. Accepted: there is no public content that needs them, and private
  data must not be rendered on a server anyway. The landing page (D1.8) is a static route.
- Server Actions disappear as a risk for the binary upload; the spec rule ("`PUT`, not a
  framework upload helper") still applies.
- Follow-up work, tracked by its own tickets:
  - `D2.x` build on this stack; D4.5 uses a Vite PWA plugin, reviewed under ADR-0003.
  - `index.html` loads fonts from Google Fonts, a third-party request on every visit. Self-host
    them before real users (D2.8, browser privacy).
  - Planning documents that still said Next.js (`PLAN_SPRINTS.md`, `plan/S1.md`–`S4.md`,
    `DISENO_BD_API_SPRINTS.md`, `PROPUESTA_PROYECTO_FINAL.md`) were reconciled in L2.11
    (2026-10-30). The specification, the implementation plan, ADR-0001, `ARCH.md`,
    `AGENTS.md` and `README.md` changed in the same PR as this ADR.

# apps/web — React SPA (area `D-`)

- Vite + React + React Router + TypeScript + Tailwind (ADR-0007).
- The Express process in `src/server/` serves only the built static files and the SPA
  fallback; there is no server-side rendering and private data is always fetched from the API.
- The browser never sees secrets or audio (project-wide rule).
- Per-ticket ownership in [docs/sprints/CLAIMS.md](../../docs/sprints/CLAIMS.md).

## Commands

```bash
cd apps/web
npm ci                     # the lockfile is committed (L1.1, ADR-0003)
npm run dev                # Vite dev server
npm run lint
npm run typecheck
npm test                   # Vitest unit suites
npm run build              # Vite build + tsc for src/server
```

## Layout

| Path | Contents |
|---|---|
| `src/pages/` | Route pages: welcome, dashboard, login, subjects, library, upload, chat, settings |
| `src/components/` | Shared UI, layout, auth, dashboard, upload, chat and motion components |
| `src/lib/` | HTTP client, CSRF, capabilities, upload validation and other browser helpers |
| `src/server/` | Express static server and SPA fallback |
| `tests/unit/` | Vitest suites for the HTTP client, auth, subjects, status and upload logic |

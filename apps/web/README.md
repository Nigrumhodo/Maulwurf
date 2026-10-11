# apps/web — Next.js (area `D-`)

- Next.js App Router + TypeScript + Tailwind + shadcn/ui (§3.2).
- The browser never sees secrets or audio (project-wide rule).
- Per-ticket ownership in [docs/sprints/CLAIMS.md](../../docs/sprints/CLAIMS.md).
- `npx create-next-app` is NOT needed: this scaffold already includes `package.json`,
  `tsconfig.json`, `app/layout.tsx`, and `app/page.tsx`. The only missing steps are:

```bash
cd apps/web
npm ci                     # the lockfile is already committed (L1.1, ADR-0003)
npx shadcn@latest init     # D1.1
```

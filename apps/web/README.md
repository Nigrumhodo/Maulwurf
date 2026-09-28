# apps/web — Next.js (área `D-`)

- Next.js App Router + TypeScript + Tailwind + shadcn/ui (§3.2).
- El navegador nunca ve secretos ni audio (regla transversal del proyecto).
- Ownership por ticket en [docs/sprints/CLAIMS.md](../../docs/sprints/CLAIMS.md).
- `npx create-next-app` NO es necesario: este scaffold ya incluye `package.json`,
  `tsconfig.json`, `app/layout.tsx` y `app/page.tsx`. Solo faltan:

```bash
cd apps/web
npm ci                     # el lockfile ya está commiteado (L1.1, ADR-0003)
npx shadcn@latest init     # D1.1
```

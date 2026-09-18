# apps/web — Next.js (dueño: Daniel)

- Next.js App Router + TypeScript + Tailwind + shadcn/ui (§3.2).
- El navegador nunca ve secretos ni audio (regla transversal de la ficha de Daniel).
- `npx create-next-app` NO es necesario: este scaffold ya incluye `package.json`,
  `tsconfig.json`, `app/layout.tsx` y `app/page.tsx`. Solo faltan:

```bash
cd apps/web
npm install                # genera package-lock.json (lockfile exigido en L1.1)
npx shadcn@latest init     # D1.1
```

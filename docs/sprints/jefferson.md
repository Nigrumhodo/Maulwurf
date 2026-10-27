# Jefferson Figueroa — bitácora de sprint

> Perfil sugerido: Infra · Workers · Integraciones (§0.2 del plan) ·
> Plan maestro: [../PLAN_SPRINTS.md](../PLAN_SPRINTS.md) ·
> Pruebas: [../PLAN_TESTS.md](../PLAN_TESTS.md) ·
> Contratos: [../ESPECIFICACION.md](../ESPECIFICACION.md) (§M5, §M8) ·
> Puertas: [../PLAN_IMPLEMENTACION.md](../PLAN_IMPLEMENTACION.md) (G7-CAL, G7-MAIL, G8, D7)

## Tickets reclamados

### Semana 1 (S1) — Fundación + spike

> Sección histórica: las filas anteriores a la revisión 2026-09-19 son candidatos, no
> claims; los tickets vigentes se registran primero en [CLAIMS.md](CLAIMS.md). Hasta que
> exista una fila allí, esta tabla permanece vacía.

| Ticket | Área | Estado | Evidencia (comando, fecha, entorno, resultado) |
|---|---|---|---|
| D1.1 | D | en curso | 2026-10-03 UTC, Windows, Node 22. Scaffold migrado de Next a Vite + React 19 + Tailwind 4 (ADR-0007). Rama `frontJJ` (`437b53e`). `npm run lint` / `npm run typecheck` / `npm run build` verde. |
| D1.2 | D | en curso | 2026-10-03 UTC, Windows, Node 22. Rutas base (`/`, `/biblioteca`, `/chat`, `/materias`, `/settings`) con `RequireAuth`/`AppLayout`. `34701c1`; restyle Cyber Y2K en `c168669` (2026-10-17). |
| D1.3 | D | en curso | 2026-10-02/03 UTC, Windows, Node 22. Login Google: botón → `/auth/google/start`, callback con carga/error, logout con revocación. `b164313`, `6a6c4da` (backend OAuth A1.3 incluido). |
| D1.4 | D | en curso | 2026-10-17 UTC, Windows, Node 22. Cliente HTTP same-origin: `lib/csrf.ts` (token solo en memoria) + `apiFetch` con inyección `X-CSRF-Token` en mutaciones e interceptores 401→login / 403→mensaje / 429→reintento con `Retry-After`. `npm run typecheck && npm run lint && npm test && npm run build` → U-S1-DA-01: **15 tests verde** (`ad75e7c`). Sin dependencias nuevas (fetch nativo). |
| D1.5 | D | hecho | 2026-10-18 UTC, Windows, Node 22. Componentes base en `components/ui/`: `DateInput`, `TimezoneSelect`, `LanguageSelect` (idioma **sin preselección**, allowlist es/en/fr), `StatusBadge` (valores exactos del contrato §1.4/§2.10, incl. `transcript_committed_cleanup_pending` y agregados), `EmptyState`, `Toast`+`ToastProvider`. Lógica en `lib/{status,languages,timezones,validation}.ts`. `npm run typecheck && npm run lint && npm test && npm run build` → U-S1-DA-02 (9) y C-S1-DA-03 (5) verde; **29 tests total** (15 + 14). |

## Bloqueos y dependencias

- D1.4 depende de L1.6 (`hecho`, `CLAIMS.md`) → desbloqueado.
- `403`/`429` sin toast: el mensaje de permisos usa `window.alert` provisional; D1.5 ya entrega `ToastProvider`/`useToast`, queda como pulido conectar el handler 403 al toast (no bloquea D1.5).
- Pendiente: PR de `frontJJ` a `develop` (D1.1–D1.5 aún en rama).

## Notas de semana

- 2026-10-17: restyle Cyber Y2K (paleta, glitch, CRT, chat flotante) y login con imágenes por tema (clara/oscura).
- 2026-10-17: D1.4 implementado con `fetch` nativo (sin axios), como se decidió.
- 2026-10-18: D1.5 completo (componentes base + tests). Navegación: el menú desplegable "académico" pasó a ser el botón "Gestión" → hub `/gestion` con menú lateral colapsable (subir clase, biblioteca, chat, materias, tareas, agenda).

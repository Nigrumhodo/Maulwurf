# Scaffold Maulwurf — qué generó este script y qué falta

Este árbol fue creado por `scripts/scaffold_monorepo.sh` (tarea L1.1 del líder).
Corresponde a la estructura §3.3 de `docs/ESPECIFICACION.md`.

## Propiedad de carpetas (plan de sprints, §5a del plan maestro)

| Ruta | Dueño |
|---|---|
| `apps/api/` | Andres (excepto `prompts/` → José, `app/workers/` → Jefferson) |
| `apps/ingest/` | Santiago |
| `apps/web/` | Daniel |
| `infra/` | Jefferson |
| `scripts/` | José (coordinación) |
| `apps/api/prompts/` | José |

## Próximos pasos por dueño

1. **Andres (A1.x):** `pip install fastapi "uvicorn[standard]" "pydantic-settings"` etc.;
   `alembic init alembic`; auth Google + sesión opaca.
2. **Daniel (D1.1):** `npx create-next-app@latest apps/web --typescript --tailwind --app`;
   luego `npx shadcn@latest init`.
3. **Santiago (S1):** spike F0 con `requirements-riva.txt`; prototipo efímero.
4. **Jefferson (J1.x):** `infra/docker-compose.yml` + `Caddyfile` + CI.
5. **José (L1.2):** ADRs en `docs/`.

> Regla: nada aquí está implementado. Los TODOs marcan trabajo pendiente de cada dueño.

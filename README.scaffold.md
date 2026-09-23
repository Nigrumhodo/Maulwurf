# Scaffold Maulwurf — qué generó este script y qué falta

Este árbol fue creado por `scripts/scaffold_monorepo.sh` (tarea L1.1 del líder).
Corresponde a la estructura §3.3 de `docs/ESPECIFICACION.md`.
Las versiones de runtime pinneadas están en [docs/VERSIONES.md](docs/VERSIONES.md).

## Áreas del backlog (plan de sprints, §0.2 del plan maestro)

La letra designa el **área**, no a una persona: los tickets se reclaman cada lunes según
`docs/PLAN_SPRINTS.md` y se registran en las bitácoras de `docs/sprints/`.

| Ruta | Área típica |
|---|---|
| `apps/api/` | `A-` API/BD (excepto `prompts/` → `L-` RAG, `app/workers/` → `J-` Infra) |
| `apps/ingest/` | `S-` Ingesta/ASR |
| `apps/web/` | `D-` Web/UX |
| `infra/` | `J-` Infra/Integraciones |
| `scripts/` | coordinación (líder) |
| `apps/api/prompts/` | `L-` RAG |

## Próximos pasos por ticket

1. **`A1.x` (API/BD):** `pip install fastapi "uvicorn[standard]" "pydantic-settings"` etc.;
   `alembic init alembic`; auth Google + sesión opaca.
2. **`D1.x` (Web):** `npx create-next-app@latest apps/web --typescript --tailwind --app`;
   luego `npx shadcn@latest init`.
3. **`S1.x` (Ingesta/ASR):** spike F0 con `requirements-riva.txt`; prototipo efímero.
4. **`J1.x` (Infra):** `infra/docker-compose.yml` + `Caddyfile` + CI.
5. **`L1.x` (RAG/liderazgo):** ADRs en [docs/adr/](docs/adr/README.md).

> Regla: nada aquí está implementado. Los TODOs marcan trabajo pendiente; los responsables
> se reclaman cada semana, no están preasignados.

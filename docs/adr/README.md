# Decisiones de arquitectura (ADR)

ADRs cortos de L1.2. Prefijo `ADR-`. Sin secretos. Redis (ADR-0005) queda para J1.6.

| ID | Título | Estado |
|---|---|---|
| [ADR-0001](ADR-0001-monorepo.md) | Estructura del monorepo y límites por área | Aceptado |
| [ADR-0002](ADR-0002-versiones.md) | Versiones de runtime y proveedores | Aceptado |
| [ADR-0003](ADR-0003-lockfiles.md) | Política de lockfiles | Aceptado |
| [ADR-0004](ADR-0004-commits-pr.md) | Formato de commit/PR y reviews | Aceptado |

## Verificación L1.2

- [x] Los cuatro ADR existen con prefijo `ADR-` en este directorio.
- [x] ADR-0002 coincide con [../VERSIONES.md](../VERSIONES.md) y los lockfiles de
  `apps/api`, `apps/ingest` y `apps/web` (Python 3.12, Node 22,
  `nvidia-riva-client==2.27.0`).
- [ ] Revisión de roles (API, ingesta, web, infra, liderazgo) en el PR del
  ticket. El sprint aún no tiene claims aceptados en
  [../sprints/CLAIMS.md](../sprints/CLAIMS.md); la revisión ocurre al fusionar
  el PR, no en este archivo.

El artefacto verificable es el diff documental de estos ADR.

# Decisiones de arquitectura (ADR)

ADRs cortos. Prefijo `ADR-`. Sin secretos. Los cuatro primeros son de L1.2;
ADR-0005 (Redis) se añade en J1.6.

| ID | Título | Estado |
|---|---|---|
| [ADR-0001](ADR-0001-monorepo.md) | Estructura del monorepo y límites por área | Aceptado |
| [ADR-0002](ADR-0002-versiones.md) | Versiones de runtime y proveedores | Aceptado |
| [ADR-0003](ADR-0003-lockfiles.md) | Política de lockfiles | Aceptado |
| [ADR-0004](ADR-0004-commits-pr.md) | Formato de commit/PR y reviews | Aceptado |
| [ADR-0005](ADR-0005-redis.md) | Política de Redis | Propuesto |

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

## Verificación J1.6

- [ ] ADR-0005 revisado y marcado `Aceptado` al fusionar el PR de J1.6.
- [x] La política coincide con `infra/docker-compose.yml` (sin persistencia,
  `noeviction`, tope de 256 MB provisional, puerto solo loopback) y con
  I-S1-JF-03 (`apps/api/tests/integration/test_redis_policy.py`).

# ADR-0001 — Estructura del monorepo y límites por área

- Fecha UTC: 2026-09-23
- Estado: Aceptado
- Ticket: L1.2
- Relacionado: [ESPECIFICACION.md](../ESPECIFICACION.md) §3.1 y §3.3,
  [README.scaffold.md](../../README.scaffold.md)

## Contexto

El scaffold (`scripts/scaffold_monorepo.sh`) ya materializó el árbol. El
diagrama §3.1 incluye un servicio de ingesta; el listado §3.3 no nombra
`apps/ingest`. Hay que fijar límites de carpeta por área (letra del backlog,
no persona) para reviews y claims.

## Decisión

El monorepo tiene estas raíces de producto:

```text
apps/api/      FastAPI, modelos, Alembic, prompts versionados
apps/ingest/   Ingesta efímera y ASR (incluido aunque falte en §3.3)
apps/web/      Next.js App Router
infra/         Compose, Caddy, CI (J1.x; aún placeholder)
scripts/       Scaffold, spikes y carga
docs/          Contratos y planificación
```

Límites de área (claiming; la letra no asigna dueño):

| Ruta | Área |
|---|---|
| `apps/api/` salvo excepciones | `A-` API/BD |
| `apps/api/prompts/` | `L-` RAG |
| `apps/api/app/workers/` | `J-` Infra |
| `apps/ingest/` | `S-` Ingesta/ASR |
| `apps/web/` | `D-` Web/UX |
| `infra/` | `J-` Infra/Integraciones |
| `scripts/` de coordinación | liderazgo (`L-`) |

Un PR que edite otra área exige review de quien tenga claim activo allí, o
del líder si no hay claim. No se inventan nombres de servicio paralelos a
`TranscriptionService`, `SearchService`, `RAGService`,
`GoogleCalendarService`.

## Consecuencias

J1.1 coloca compose y Caddy solo en `infra/`. Contratos compartidos se
congelan el lunes; un cambio de interfaz actualiza spec y plan juntos.

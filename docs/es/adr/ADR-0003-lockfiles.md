# ADR-0003 — Política de lockfiles

- Fecha UTC: 2026-09-23
- Estado: Aceptado
- Ticket: L1.2
- Relacionado: [ADR-0002](ADR-0002-versiones.md), `.gitignore`

## Contexto

Sin lockfiles commiteados las instalaciones no son reproducibles (criterio
L1.1). Hay que decir qué entra al git y cómo se actualiza.

## Decisión

Se versionan:

- `apps/api/uv.lock`
- `apps/ingest/uv.lock`
- `apps/web/package-lock.json`
- `requirements-riva.txt` (alineado con `nvidia-riva-client==2.27.0`)

No se versionan: `.venv/`, `node_modules/`, `.env`, caches, audio
(`*.wav` y el resto de extensiones de `.gitignore`), dumps, secretos.

Instalación:

```text
cd apps/api && uv sync --extra dev
cd apps/ingest && uv sync --extra dev
cd apps/web && npm ci
```

`npm install` sin lock solo si hay que regenerarlo; el PR incluye el lock
nuevo. Un cambio de dependencia actualiza manifiesto y lock en el **mismo**
PR. No se resuelve a ciegas en CI: J1.4 usará `uv sync --frozen` / `npm ci`
cuando exista el pipeline.

## Consecuencias

Revisiones rechazan PRs que añaden paquetes sin lock, o que commitean
`.venv` / `node_modules` / `.env`. Escanear vulnerabilidades de
dependencias sigue pendiente (TODO de AGENTS.md) y no lo sustituye este ADR.

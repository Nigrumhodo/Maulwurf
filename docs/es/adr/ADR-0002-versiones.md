# ADR-0002 — Versiones de runtime y proveedores

- Fecha UTC: 2026-09-23
- Estado: Aceptado
- Ticket: L1.2
- Relacionado: [VERSIONES.md](../VERSIONES.md) (L1.1),
  [ESPECIFICACION.md](../ESPECIFICACION.md) §3.2 y §9.2

## Contexto

L1.1 verificó instalaciones reproducibles. J1.1 y J1.4 deben copiar las
mismas cifras. La spec declara stack y CI; F0 aún no cierra contratos de
proveedor.

## Decisión

Runtimes de desarrollo y de CI futuro:

| Runtime | Pin |
|---|---|
| Python | 3.12 (`requires-python = ">=3.12"`) |
| Node.js | 22 (`engines.node` `>=22 <23`) |
| PostgreSQL | 16 |
| pgvector | imagen `pgvector/pgvector:pg16` |
| Cliente Riva | `nvidia-riva-client==2.27.0` |

Archivos: `.python-version`, `.nvmrc`, `apps/*/pyproject.toml`,
`apps/web/package.json`, `requirements-riva.txt`.

CI: **GitHub Actions** (decisión de equipo 2026-09-20). Registry candidato:
GitHub Container Registry. Runner e imagen exactos se confirman en J1.4;
el workflow `trigger-hoplite-qa.yml` no es el pipeline de calidad.

Proveedores de producto (Whisper large-v3, LLM/embeddings de §3.2) siguen
**provisionales** hasta F0 y D2/D3. Este ADR no fija cuotas, sample rate ni
límites de payload.

## Consecuencias

Cambiar Python, Node, PG o el pin de Riva actualiza `VERSIONES.md`,
manifiestos y lockfiles en el mismo PR. Mezclar dimensiones o versiones de
embedding exige índice nuevo (spec), no este ADR.

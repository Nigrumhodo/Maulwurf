# José Leonardo Pinilla Zamora — bitácora de sprint

> Perfil sugerido: Líder técnico · RAG (§0.2 del plan) ·
> Plan maestro: [../PLAN_SPRINTS.md](../PLAN_SPRINTS.md) ·
> Pruebas: [../PLAN_TESTS.md](../PLAN_TESTS.md) ·
> Contratos: [../ESPECIFICACION.md](../ESPECIFICACION.md) (§M4, §M6, §M7) ·
> Puertas: [../PLAN_IMPLEMENTACION.md](../PLAN_IMPLEMENTACION.md) (G4, G6)

## Tickets reclamados

### Semana 1 (S1) — Fundación + spike

> Sección histórica: las filas anteriores a la revisión 2026-09-19 son candidatos, no
> claims; los tickets vigentes se registran primero en [CLAIMS.md](CLAIMS.md). Hasta que
> exista una fila allí, esta tabla permanece vacía.

| Ticket | Área | Estado | Evidencia (comando, fecha, entorno, resultado) |
|---|---|---|---|
| J1.8 | J | hecho | 2026-09-25 UTC, Ubuntu (Docker 29.8/WSL). `docker build --target base -t maulwurf-ingest:base apps/ingest`; dentro: ffmpeg 7.1.5-0+deb13u1, ffprobe 7.1.5, Python 3.12.14; Id `sha256:91ef546d…`. Pendiente: paridad con ffmpeg 8.0.1 del spike (S1.A4) — decisión registrada en el PR. |
| J1.1 | J | hecho | 2026-09-25 UTC. `docker compose up -d --build --wait`: 8/8 healthy (caddy en 8080/8443 porque 80/443 están ocupados en el host); `/`→404 web y `/readyz`→200 api; `docker compose config --volumes` solo `pgdata`/`caddy_data`/`caddy_config`; `ingest` sin mounts. |

## Bloqueos y dependencias

- (el lunes de cada semana: anotar de quién esperas contrato congelado §6 o código real, y qué día)

## Notas de semana

- (decisiones, hallazgos, desvíos de alcance — una línea por evento con fecha)

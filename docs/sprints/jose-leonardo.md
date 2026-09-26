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
| J1.2 | J | hecho | 2026-09-25 UTC. `docker inspect` de `ingest`: ReadonlyRootfs=true, User=10001:10001, CapDrop=ALL, no-new-privileges, tmpfs 256 MB, MemorySwap=1g, pids 128, NanoCpus 2; dentro: `/` de solo lectura, `/work/tmp` escribible, `ulimit -c 0`; cgroup `memory.swap.max=0`. Host: `zram` activo y `core_pattern` a systemd-coredump — pendiente de operación. |
| J1.3 | J | hecho | 2026-09-25 UTC. `caddy validate` OK; `scripts/load/proxy_no_buffering.sh`: 536 870 912 B por Caddy → HTTP 202; sin archivos nuevos (excluye certificados), sin descriptores borrados, 0 bodies en logs. Usa el Caddyfile real con el upstream de ingesta sustituido por un sink; pendiente repetir contra el endpoint real (S2). |
| J1.4 | J | hecho | 2026-09-25 UTC. Local: api `ruff`+`mypy` verdes (0 unitarias aún); ingest `ruff`+`mypy` verdes y 27 tests; web `lint`+`typecheck`+Vitest (0 tests)+`build` OK. `quality.yml` validado (YAML); primera corrida en GitHub pendiente del PR. Correcciones de tipos: plugin pydantic y `cast` del DSN; anotaciones en tests de ingesta; scripts `typecheck`/`test` en web. |
| J1.6 | J | hecho | 2026-09-25 UTC. `uv run pytest -m integration tests/integration/test_redis_policy.py`: 2 passed; `aof_enabled=0`, `save` vacío, `maxmemory-policy=noeviction`, barrido con umbral de 4 KiB (ADR-0005). El ADR queda `Propuesto` hasta el review. |
| J1.7 | J | hecho | 2026-09-25 UTC. `docs/RUNBOOK-dev.md` ejecutado sobre el árbol actual (compose up/down, healthchecks, TLS, tests y lint) y enlazado desde el README raíz. Pendiente: transcripción desde un clon limpio y la sección de migraciones cuando A1.7 entregue `alembic.ini`. |

## Bloqueos y dependencias

- (el lunes de cada semana: anotar de quién esperas contrato congelado §6 o código real, y qué día)

## Notas de semana

- (decisiones, hallazgos, desvíos de alcance — una línea por evento con fecha)

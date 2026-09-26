# infra/ — entorno local de desarrollo (S1)

Estado: `docker-compose.yml` (J1.1), el endurecimiento de `ingest` (J1.2), el
`Caddyfile` sin buffering (J1.3) y la política de Redis (J1.6) están fusionados en
`develop`. La propiedad de los tickets J1.x se registra en
[docs/sprints/CLAIMS.md](../docs/sprints/CLAIMS.md); los comandos completos, en
[docs/RUNBOOK-dev.md](../docs/RUNBOOK-dev.md).

## Contenido

| Archivo | Qué es |
|---|---|
| `docker-compose.yml` | Los 8 servicios de S1 con healthchecks; imágenes fijadas por digest |
| `Caddyfile` | Proxy TLS local (`tls internal`), mismo origen web/api/ingest, sin buffering ni caché |

| Servicio | Imagen | Notas |
|---|---|---|
| `web` | `maulwurf-web:local` (build `apps/web`) | Next.js 22-alpine, usuario no root |
| `api` | `maulwurf-api:local` (build `apps/api`) | FastAPI; `/readyz` aún placeholder (J1.5) |
| `ingest` | `maulwurf-ingest:local` (build `apps/ingest`, target `runtime`) | Endurecido: ver abajo |
| `worker` / `scheduler` | `maulwurf-api:local` | Placeholder sin efectos hasta J1.5 (ARQ) |
| `postgres` | `pgvector/pgvector:pg16` por digest | Único durable: volumen `pgdata` |
| `redis` | `redis:7.4-alpine` por digest | Sin persistencia, `noeviction` ([ADR-0005](../docs/adr/ADR-0005-redis.md)) |
| `caddy` | `caddy:2.8-alpine` por digest | `admin off`; access log JSON sin bodies |

## Garantías del entorno local

- **Sin audio durable:** no hay MinIO/S3, ni volumen de audio; los temporales de ingesta
  viven en tmpfs privado y se pierden al recrear el contenedor. Los únicos volúmenes son
  `pgdata`, `caddy_data` y `caddy_config`.
- **`ingest` endurecido (J1.2):** root fs de solo lectura, `cap_drop: ALL`,
  `no-new-privileges`, usuario `10001`, tmpfs `noexec,nosuid,nodev` en `/work/tmp`,
  límites de CPU/memoria, `pids_limit`, sin swap adicional y `core: 0`.
- **Redis (J1.6):** `--save "" --appendonly no`, `noeviction`, valores ≤ 4 KiB y solo
  claves `arq:*`/`mw:*`; publicado solo en loopback para el test I-S1-JF-03.
- **Proxy (J1.3):** uploads en streaming puro; el `PUT /audios/*/content` apunta a
  `api:8000` en S1 y conmuta a `ingest:8000` en S2 (comentario en el propio Caddyfile).
  La prueba de no-buffering es `scripts/load/proxy_no_buffering.sh` (I-S1-JF-02).
- **Secretos fuera del compose:** la contraseña de Postgres va como
  `${POSTGRES_PASSWORD:-maulwurf}` (override con `.env`); los `MAULWURF_*` llegan por
  `env_file`. Nunca commitear `.env`.

## Variables de entorno del stack

| Variable | Uso |
|---|---|
| `CADDY_HTTP_PORT` / `CADDY_HTTPS_PORT` | Puertos del host para 80/443 (p. ej. 8080/8443) |
| `REDIS_PORT` | Puerto del host para el Redis publicado en loopback (p. ej. 6380) |
| `POSTGRES_PASSWORD` | Override de la contraseña local de Postgres |
| `INGEST_TMPFS_BYTES` / `INGEST_MEM_LIMIT` | tmpfs y límite de memoria de `ingest`; provisionales |

Los valores de `INGEST_*` vienen de `.env.example` y **no están aprobados** hasta
S1.B2/D4 (límites medidos); los 200 MiB/3 h del spike S1.A5 tampoco lo están.

## Arranque rápido

```bash
cp .env.example .env   # completar los MAULWURF_* con openssl rand -hex 32
cd infra
docker compose up -d --build --wait
docker compose ps      # 8/8 healthy
```

Si 80/443 están ocupados en el host:

```bash
CADDY_HTTP_PORT=8080 CADDY_HTTPS_PORT=8443 docker compose up -d --build --wait
```

Healthchecks, TLS, tests y cierre: [docs/RUNBOOK-dev.md](../docs/RUNBOOK-dev.md).

## Pendiente en esta área

- **J1.5:** worker/scheduler reales (ARQ + dispatcher de outbox); hoy son placeholders.
- **Migraciones:** `alembic upgrade head` se documentará cuando A1.7 entregue
  `alembic.ini`.
- **Verificación del host (J1.2):** `zram` activo y `core_pattern` a systemd-coredump
  quedaron como pendiente de operación en la bitácora.
- **SSE y upload real:** el smoke end-to-end llega con A1.8/S2.

# Runbook de despliegue de referencia (`J4.7`)

Guía operativa para desplegar la pila de producción de Maulwurf sin imágenes mutables
`:latest`, preservando aislamiento de red (`edge` vs `data`), migraciones en un job único y
la política G1 de audio efímero (`tmpfs` no ejecutable sin volumen persistente de audio).

---

## 1. Artefactos de despliegue

- Compose de producción: `infra/docker-compose.prod.yml`
- Plantilla de variables de producción: `infra/deploy/.env.prod.example`
- Edge TLS / reverse proxy: `infra/deploy/Caddyfile.prod`
- Scraper interno de métricas (perfil opcional `observability`): `infra/deploy/prometheus.yml`

### 1.1 Front proxy on the shared host

The production host also runs Plane, whose own Caddy is the public entry point: it takes the
host's 80/443 and forwards Maulwurf's traffic to this stack's Caddy. Consequences:

- `Caddyfile.prod` sets `trusted_proxies static private_ranges` so Maulwurf's Caddy keeps the
  client IP that the front Caddy forwards in `X-Forwarded-For`; without it every request
  would carry the front Caddy's address and all users would share one API rate-limit bucket.
- The front (Plane) Caddy must **not** trust client-supplied `X-Forwarded-For`: it is the
  edge and must overwrite that header with the real peer address.
- Any private-network peer that can reach Maulwurf's Caddy directly is trusted too; narrow
  `trusted_proxies` to the shared Docker network's subnet once that subnet is fixed.
- This differs from ADR-0010 option (a) (Caddy as the only ingress) and from the `80:80` /
  `443:443` publication in `infra/docker-compose.prod.yml`; the host-specific wiring is not
  versioned in this repository.

---

## 2. Variables y secretos obligatorios

Ningún secreto viaja dentro de las imágenes o del repositorio. Configura en el entorno del
orquestador o en un archivo `.env.prod` fuera del control de versiones (`chmod 600`):

| Variable | Uso | Nota operativa |
| --- | --- | --- |
| `MAULWURF_DOMAIN` | Dominio público HTTPS (`Caddyfile.prod`) | Sin esquema (`app.ejemplo.com`) |
| `MAULWURF_IMAGE_TAG` | Tag inmutable / digest de release | Prohibido `:latest` |
| `POSTGRES_PASSWORD` | Credencial Postgres owner | Distinta en prod respecto a local |
| `REDIS_PASSWORD` | Autenticación Redis (`--requirepass`) | Obligatoria; compartida por `redis`, `api`, `worker` y `scheduler` |
| `MAULWURF_SECRET_KEY` | Firma HMAC de sesiones | >= 32 bytes aleatorios, distinto al resto |
| `MAULWURF_ENCRYPTION_KEY` | Cifrado AES-GCM de tokens OAuth | 32 bytes (`base64`), distinto al resto |
| `MAULWURF_OAUTH_STATE_SECRET` | Firma de states OAuth | >= 32 bytes aleatorios, distinto al resto |
| `MAULWURF_INGEST_CREDENTIAL_SECRET` | Secreto compartido de firma HMAC `api <-> ingest` | Independiente de `SECRET_KEY` y de `INGEST_METRICS_TOKEN` |
| `INGEST_METRICS_TOKEN` | Bearer de `GET /internal/metrics` en `ingest` | >= 32 bytes, distinto de `MAULWURF_INGEST_CREDENTIAL_SECRET` |
| `MAULWURF_METRICS_TOKEN` | Bearer de `GET /internal/metrics` en `api` | >= 32 bytes, independiente del resto |
| `MAULWURF_GOOGLE_CLIENT_ID` / `_SECRET` | OAuth 2.0 web | Redirect URI: `https://$MAULWURF_DOMAIN/auth/google/callback` |
| `MAULWURF_AUTH_RATE_LIMIT_PER_MINUTE` / `MAULWURF_INTEGRATION_RATE_LIMIT_PER_MINUTE` | Límites por minuto en `/auth/*` e `/integrations/*` | Por defecto `30` en `docker-compose.prod.yml` |
| `MAULWURF_NOTIFICATION_SCHEDULER_ENABLED` / `_DELIVERY_ENABLED` | Activación M8 Gmail/agenda | Por defecto `false` (activación explícita tras validar opt-in y DNS/OAuth) |
| `NVIDIA_API_KEY` / `RIVA_SERVER` / `RIVA_FUNCTION_ID` | Solo contenedor `ingest` | Nunca expuestas a `web` ni a logs |

Si habilitas el perfil `--profile observability`, coloca los tokens en dos ficheros de solo
lectura (`chmod 400`) dentro de `PROMETHEUS_SECRETS_DIR`:

- `api_metrics_token` (igual a `MAULWURF_METRICS_TOKEN`)
- `ingest_metrics_token` (igual a `INGEST_METRICS_TOKEN`, nunca el secreto de firma HMAC)

---

## 3. Secuencia de despliegue

### 3.1 Construcción local (x86_64 o ARM64) o pull desde registry + migraciones (`migrate`)

`api`, `worker`, `scheduler` e `ingest` declaran `depends_on.migrate: condition: service_completed_successfully`.
Si construyes directamente en el servidor (p. ej. VPS `linux/arm64` con `MAULWURF_REGISTRY=local` y `MAULWURF_IMAGE_TAG=<sha-corto>`):

```bash
docker compose -f infra/docker-compose.prod.yml --env-file .env.prod build
docker compose -f infra/docker-compose.prod.yml --env-file .env.prod run --rm migrate
```

Si usas imágenes ya publicadas en un registry privado/GHCR:

```bash
docker compose -f infra/docker-compose.prod.yml --env-file .env.prod pull
docker compose -f infra/docker-compose.prod.yml --env-file .env.prod run --rm migrate
```

### 3.2 Levantar servicios y esperar healthchecks

```bash
docker compose -f infra/docker-compose.prod.yml --env-file .env.prod up -d --wait
# Con métricas Prometheus internas:
docker compose -f infra/docker-compose.prod.yml --env-file .env.prod --profile observability up -d --wait
```

### 3.3 Verificación posterior al despliegue

1. Estado de contenedores y healthchecks:
   `docker compose -f infra/docker-compose.prod.yml ps`
2. Readiness público y bloqueo de métricas internas en Caddy:
   - `GET https://$MAULWURF_DOMAIN/readyz` -> `200`
   - `GET https://$MAULWURF_DOMAIN/internal/metrics` -> `404`
3. Verificación G1 en `ingest`:
   comprobar que `ls -la /work/tmp` esté vacío en reposo y `mount | grep /work/tmp`
   incluya `noexec,nosuid,nodev`.

---

## 4. Rollback e incidentes

- Para revertir la aplicación sin tocar el esquema, ajusta `MAULWURF_IMAGE_TAG` a la versión
  anterior y ejecuta `docker compose -f infra/docker-compose.prod.yml up -d --no-deps --wait api worker scheduler ingest web`.
- Si se aplicó una migración con fallo o se requiere restauración completa de Postgres,
  sigue `docs/runbooks/restore.md`.
- Ante fallos específicos de pipeline consulta:
  - `docs/runbooks/cleanup.md`
  - `docs/runbooks/tokens.md`
  - `docs/runbooks/account-deletion.md`
  - `docs/runbooks/budget-alerts.md`
  - `docs/runbooks/provider-down.md`

# Runbook de despliegue de referencia (`J4.7`)

Guía operativa para desplegar la pila de producción de Maulwurf sin imágenes mutables
`:latest`, preservando aislamiento de red (`edge` vs `data`), migraciones en un job único y
la política G1 de audio efímero (`tmpfs` no ejecutable sin volumen persistente de audio).

---

## 1. Artefactos de despliegue

- Compose de producción: `infra/docker-compose.prod.yml`
- Edge TLS / reverse proxy: `infra/deploy/Caddyfile.prod`
- Scraper interno de métricas (perfil opcional `observability`): `infra/deploy/prometheus.yml`

---

## 2. Variables y secretos obligatorios

Ningún secreto viaja dentro de las imágenes o del repositorio. Configura en el entorno del
orquestador o en un archivo `.env.prod` fuera del control de versiones (`chmod 600`):

| Variable | Uso | Nota operativa |
| --- | --- | --- |
| `MAULWURF_DOMAIN` | Dominio público HTTPS (`Caddyfile.prod`) | Sin esquema (`app.ejemplo.com`) |
| `MAULWURF_IMAGE_TAG` | Tag inmutable / digest de release | Prohibido `:latest` |
| `POSTGRES_PASSWORD` | Credencial Postgres owner | Distinta en prod respecto a local |
| `MAULWURF_SECRET_KEY` | Firma HMAC de sesiones y states OAuth | >= 32 bytes aleatorios |
| `MAULWURF_ENCRYPTION_KEY` | Cifrado AES-GCM de tokens OAuth | 32 bytes (`base64`) |
| `MAULWURF_INGEST_CREDENTIAL_SECRET` | Secreto compartido `api <-> ingest` + `/internal/metrics` de `ingest` | Independiente de `SECRET_KEY` |
| `MAULWURF_METRICS_TOKEN` | Bearer de `GET /internal/metrics` en `api` | Independiente del resto |
| `MAULWURF_GOOGLE_CLIENT_ID` / `_SECRET` | OAuth 2.0 web | Redirect URI: `https://$MAULWURF_DOMAIN/auth/google/callback` |
| `NVIDIA_API_KEY` / `RIVA_SERVER` / `RIVA_FUNCTION_ID` | Solo contenedor `ingest` | Nunca expuestas a `web` ni a logs |

Si habilitas el perfil `--profile observability`, coloca los tokens en dos ficheros de solo
lectura (`chmod 400`) dentro de `PROMETHEUS_SECRETS_DIR`:

- `api_metrics_token` (igual a `MAULWURF_METRICS_TOKEN`)
- `ingest_credential_secret` (igual a `MAULWURF_INGEST_CREDENTIAL_SECRET`)

---

## 3. Secuencia de despliegue

### 3.1 Migraciones previas (`migrate`)

`api`, `worker`, `scheduler` e `ingest` declaran `depends_on.migrate: condition: service_completed_successfully`.
Para mayor trazabilidad en un rollout:

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

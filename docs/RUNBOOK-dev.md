# RUNBOOK de desarrollo (local)

> Alcance: entorno local de desarrollo. No es un runbook de producción (S4/J4.x).
> La fuente de verdad de contratos es [ESPECIFICACION.md](ESPECIFICACION.md);
> el estado real del repositorio, lo que estas páginas documentan.

## 1. Requisitos

- Docker Engine + Compose v2 (probado con Docker 29.8 y Compose 5.5).
- `uv` 0.12.18 o superior (gestiona también Python 3.12 vía `.python-version`).
- Node.js 22 (`.nvmrc`) y `npm`.
- `openssl` para generar secretos locales.

## 2. Primer arranque

```bash
git clone https://github.com/Nigrumhodo/Maulwurf.git
cd Maulwurf
cp .env.example .env

# Generar secretos locales (no se commitean; .env está en .gitignore)
openssl rand -hex 32   # MAULWURF_SECRET_KEY
openssl rand -hex 32   # MAULWURF_ENCRYPTION_KEY
openssl rand -hex 32   # MAULWURF_OAUTH_STATE_SECRET

cd infra
docker compose up -d --build --wait
docker compose ps
```

Si los puertos 80/443 ya están ocupados en el host:

```bash
CADDY_HTTP_PORT=8080 CADDY_HTTPS_PORT=8443 docker compose up -d --build --wait
# usar https://localhost:8443 en los comandos siguientes
```

Los 8 servicios (`web`, `api`, `ingest`, `worker`, `scheduler`, `postgres`,
`redis`, `caddy`) quedan `healthy`. `worker`/`scheduler` corren un placeholder
sin efectos hasta J1.5.

## 3. Healthchecks

```bash
curl -k -o /dev/null -w '%{http_code}\n' https://localhost/          # web (404 hasta D1.x)
curl -k https://localhost/readyz                                     # api (placeholder 200)
docker compose exec api    python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/healthz').status)"
docker compose exec ingest python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/healthz').status)"
```

## 4. TLS local

Caddy usa su CA interna (`tls internal`), persistida en el volumen `caddy_data`.
Para confiar en ella en lugar de usar `curl -k`:

```bash
docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt /tmp/maulwurf-root.crt
sudo cp /tmp/maulwurf-root.crt /usr/local/share/ca-certificates/maulwurf-root.crt
sudo update-ca-certificates
```

## 5. Migraciones (pendiente A1.7)

`alembic.ini` aún no existe; cuando A1.7 lo entregue, el comando será:

```bash
docker compose exec api alembic upgrade head
docker compose exec api alembic current
```

## 6. Tests y lint

```bash
# API
cd apps/api
uv sync --frozen --extra dev
uv run ruff check . && uv run mypy .
uv run pytest -m "not integration and not provider and not load"

# Ingesta
cd apps/ingest
uv sync --frozen --extra dev
uv run ruff check . && uv run mypy .
uv run pytest -m "not integration and not provider and not load"

# Web
cd apps/web
npm ci
npm run lint && npm run typecheck && npm test && npm run build
```

Integración local (Redis se publica solo en `127.0.0.1:6379`; PostgreSQL no se
publica al host):

```bash
cd apps/api && uv run pytest -m integration
```

Para pruebas que necesiten PostgreSQL desde el host, crear un
`infra/docker-compose.override.yml` local (no versionado) con:

```yaml
services:
  postgres:
    ports:
      - "127.0.0.1:5432:5432"
```

Las suites `P-*` (proveedores) y `load` no corren en MR; requieren cuenta
protegida y presupuesto, y se ejecutan aparte.

## 7. Apagar y limpiar

```bash
cd infra
docker compose down        # conserva pgdata
docker compose down -v     # destructivo: borra volúmenes
```

No hay audio persistido que borrar: los temporales de ingesta son `tmpfs` y
desaparecen al recrear el contenedor.

## 8. Problemas comunes

- **Puerto ocupado**: usar `CADDY_HTTP_PORT`/`CADDY_HTTPS_PORT` (§2).
- **La API no arranca**: `MAULWURF_ENV` distinto de `local` con secretos
  `change-me` falla a propósito (fail-fast de `app/core/config.py`).
- **Aviso de certificado**: confiar en la CA (§4) o usar `curl -k`.
- **Primer build lento**: descarga ffmpeg, dependencias Python y `npm ci`.

## 9. Reglas de privacidad del entorno local

- Sin grabaciones reales de clase ni audio durable: la ingesta real llega en S2.
- Sin `NVIDIA_API_KEY` en logs, frontend ni commits.
- Las suites `P-*` solo con cuenta autorizada y presupuesto documentado.

## Enlaces

- [../README.md](../README.md) — visión y estado del repositorio.
- [ESPECIFICACION.md](ESPECIFICACION.md) — contratos M1–M9 y seguridad.
- [PLAN_SPRINTS.md](PLAN_SPRINTS.md) — backlog y DoD por sprint.
- [adr/](adr/README.md) — decisiones de arquitectura.

# infra/ — dueño: Jefferson (J1.x)

Pendiente (placeholders intencionales, no implementación):

- `docker-compose.yml` — 8 servicios: web, api, ingest, worker, scheduler,
  postgres (`pgvector/pgvector:pg16` fijada), redis, caddy. Sin MinIO/S3 ni volumen de audio.
- `Caddyfile` — proxy sin buffering/caché para uploads.
- CI (GitLab): ruff+ESLint, mypy+tsc, pytest+Vitest, builds.

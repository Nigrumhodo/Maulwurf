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
| J1.1 | J | hecho | 2026-09-25 UTC. `docker compose up -d --build --wait`: 8/8 healthy (caddy en 8080/8443 porque 80/443 están ocupados en el host); `/`→404 web con el Caddyfile mínimo de este PR (solo enruta a web) y `/readyz`→200 verificado dentro del contenedor `api`; el ruteo de `/readyz` por Caddy se evidencia en J1.3. `docker compose config --volumes` solo `pgdata`/`caddy_data`/`caddy_config`; `ingest` sin mounts. |
| J1.2 | J | hecho | 2026-09-25 UTC. `docker inspect` de `ingest`: ReadonlyRootfs=true, User=10001:10001, CapDrop=ALL, no-new-privileges, tmpfs 256 MB, MemorySwap=1g, pids 128, NanoCpus 2; dentro: `/` de solo lectura, `/work/tmp` escribible, `ulimit -c 0`; cgroup `memory.swap.max=0`. Host: `zram` activo y `core_pattern` a systemd-coredump — pendiente de operación. |
| J1.3 | J | hecho | 2026-09-25 UTC. `caddy validate` OK; `scripts/load/proxy_no_buffering.sh`: 536 870 912 B por Caddy → HTTP 202; sin archivos nuevos (excluye certificados), sin descriptores borrados, 0 bodies en logs. Usa el Caddyfile real con el upstream de ingesta sustituido por un sink; pendiente repetir contra el endpoint real (S2). |
| J1.4 | J | hecho | 2026-09-25 UTC. Local: api `ruff`+`mypy` verdes (0 unitarias aún); ingest `ruff`+`mypy` verdes y 27 tests; web `lint`+`typecheck`+Vitest (0 tests)+`build` OK. `quality.yml` validado (YAML); primera corrida en GitHub pendiente del PR. Correcciones de tipos: plugin pydantic y `cast` del DSN; anotaciones en tests de ingesta; scripts `typecheck`/`test` en web. |
| J1.6 | J | hecho | 2026-09-25 UTC. `uv run pytest -m integration tests/integration/test_redis_policy.py`: 2 passed; `aof_enabled=0`, `save` vacío, `maxmemory-policy=noeviction`, barrido con umbral de 4 KiB (ADR-0005). El ADR queda `Propuesto` hasta el review. |
| J1.7 | J | hecho | 2026-09-25 UTC. Verificación desde clon limpio (`git clone --branch s1/j1.7-runbook`, `cp .env.example .env` + secretos, `docker compose -p maulwurfcheck up -d --build --wait` con puertos 8081/8444 y `REDIS_PORT=6380`): 8/8 healthy, `/`→200 web y `/readyz`→200 api; volúmenes solo `pgdata`/`caddy_data`/`caddy_config`. Ejecutado también sobre el árbol actual. Pendiente: sección de migraciones cuando A1.7 entregue `alembic.ini`. |
| A1.1 | A | hecho | 2026-09-27 UTC, Arch Linux, Python 3.12.11 (`uv sync --python 3.12`), Docker 29.8.0, Postgres `pgvector/pgvector:pg16` desechable en `127.0.0.1:55432`. Rama `s1/a1.1-api-scaffold` (`30df7bb`, QA `96cc80c`: conftest hermético frente a `MAULWURF_*` del shell). `uv run pytest tests/unit/test_health.py`: 2 passed; suite con `MAULWURF_ENV=prod` exportado: verde. `uv run uvicorn app.main:app --port 8765` + `curl /healthz`: 200 `{"status":"ok"}`. `ruff` y `mypy` verdes. Mergeado en #21. |
| A1.2 | A | hecho | 2026-09-27 UTC, Arch Linux, Python 3.12.11 (`uv sync --python 3.12`), Docker 29.8.0, Postgres `pgvector/pgvector:pg16` desechable en `127.0.0.1:55432`. Rama `s1/a1.2-settings` (`015dddb`, `de43cf1`, QA `0e9fa1e`, `6ffc83e`, `8e7a9d5`, `20a01ae`). `pytest tests/unit/test_config.py tests/unit/test_db.py`: 30 passed (fail-fast por secreto ausente o vacío, DSN/Origin de desarrollo fuera de local, `VAR=` vacío = no configurado, par OAuth id/secret, guard de defaults también con `default_factory`, `change-me` y Origin sin https fuera de local, Origin con path, query o fragment, `MAULWURF_*` desconocida en entorno o en el `.env` efectivo, claves ajenas del `.env` ignoradas, errores sin eco del valor, `repr`/dump sin secretos). Arranque desde `apps/api` con el `.env.example` de la raíz: OK. En compose: `api` healthy y `docker compose run -e MAULWURF_DATABSE_URL=x api` falla con «variables de entorno desconocidas». Barrido del diff sin valores de secretos. Tras la fusión con `develop`: 29 passed y suite de la API 63 passed. Mergeado en #23 y #26. |
| A1.5 | A | hecho | 2026-09-27 UTC, Arch Linux, Python 3.12.11 (`uv sync --python 3.12`), Docker 29.8.0, Postgres `pgvector/pgvector:pg16` desechable en `127.0.0.1:55432`. Rama `s1/a1.5-core-schema` (`af9bf82`, `af042fb`, `af979b4`, QA `dfedf0b`: estados activos explícitos, AAD `user:campo:versión` tipado, autogenerate con `compare_type`/`compare_server_default`). `alembic upgrade head` desde vacío → `downgrade base` → `upgrade head` OK; `alembic check`: sin diferencias con los modelos. `pytest tests/unit/test_crypto.py tests/integration/test_core_schema.py`: 22 passed (U-S1-AN-03: nonce único, AAD por usuario/campo, versión de clave, nada plano en BD; CHECK de gate de outbox; FK compuesta; índice de intento activo; filtro de tenant). Mergeado en #24. |
| A1.4 | A | hecho | 2026-09-27 UTC, Arch Linux, Python 3.12.11 (`uv sync --python 3.12`), Docker 29.8.0, Postgres `pgvector/pgvector:pg16` desechable en `127.0.0.1:55432`. Rama `s1/a1.4-session-csrf` (`15478c2`, `54c1a61`, QA `2fe18ec`). `pytest tests/unit/test_session_csrf.py tests/integration/test_sessions.py`: 30 passed (U-S1-AN-01: flags de cookie, solo hashes en BD, expiración, revocación y rotación; U-S1-AN-02: POST y PUT binario rechazan sin token, token falso, sin Origin, Origin ajeno, con sufijo o con bytes no ASCII (403, no 500); rotación concurrente con un solo sucesor (409 `session_conflict`); usuario no activo → 401; `GET /me` con `no-store`). Código 409 `session_conflict` añadido a DISENO §3.2. Suite completa: 94 passed. Mergeado en #22. |
| J1.5 | J | hecho | 2026-09-27 UTC, Arch Linux, Python 3.12.11 (`uv sync --python 3.12`), Docker 29.8.0, Postgres `pgvector/pgvector:pg16` desechable en `127.0.0.1:55432`. Rama `s1/j1.5-arq-outbox` (`47043b2`, `039cece`, QA `d022f17`), parcial mergeado en #25. `pytest tests/unit/test_arq_settings.py tests/unit/test_health.py`: 10 passed. `/readyz` real en compose: 200 `ready`; con `redis` parado 503 `{"failed":["redis"]}` y `/healthz` sigue 200; al volver Redis, 200. Worker/scheduler dependen solo de Postgres/Redis, con `cap_drop: [ALL]` y `no-new-privileges`. `docker compose up -d --build --wait worker scheduler`: ambos healthy; claves `arq:queue:health-check` y `arq:scheduler:health-check` con TTL ~31 s y 72 bytes; con el worker parado 33 s, `arq ... --check` sale 1. Dispatcher (rama `s1/j1.5-dispatcher`, QA `4ec1923`, `a9fdd99`, `9b54c72`): `pytest tests/unit/test_outbox_policy.py` (U-S1-JF-04) e integración `tests/integration/test_dispatcher.py` contra Postgres y Redis reales verdes: solo IDs, gate respetado aun con `enabled`, sin inanición del lote, tipos desconocidos a `skipped`, dedup contada. Suite de la API: 140 passed. E2E en compose: el worker ejecutó `index` del evento listo y el bloqueado quedó `pending`. Mergeado en #25 y #30. Para J2.1: ack `completed`/`failed`, reintentos y `next_attempt_at`. |
| A1.7 | A | hecho | 2026-09-27 UTC, Arch Linux, Python 3.12.11, Docker 29.8.0, `pgvector/pgvector:pg16`. Rama `s1/a1.7-alembic`. `pytest tests/integration/test_migrations.py`: 4 passed (I-S1-AN-04: desde vacío, downgrade + upgrade, idempotencia, 4 procesos `alembic` concurrentes). Suite de la API en clon limpio: 98 passed. Compose desde cero: 8/8 healthy, la API migra a `0001` y `/readyz` por Caddy 200; 3 réplicas de `api` sobre esquema vacío: una migra, 0 errores. Un test con hilos se colgó (el `context` de Alembic es global al proceso): el test usa procesos y `env.py` fija `lock_timeout`. Mergeado en #28. |
| A1.8 | A | hecho | 2026-09-27 UTC, Arch Linux, Python 3.12.11, Docker 29.8.0, `pgvector/pgvector:pg16`. Rama `s1/a1.8-endpoints` (QA `187e490`, `8ecaff4`). `pytest tests/integration/test_a18_endpoints.py`: verde (201 `awaiting_upload`, 422 sin crear filas ni eco de valores, materia ajena 404, `PUT` 503 sin leer el cuerpo, 413/410/409, chunked sin `Content-Length` 503, `PATCH /me` y `/integrations/status` con `no-store`, logout revoca). Suite de la API: 123 passed. Compose vía Caddy sin sesión: 401 en los cinco endpoints. Mergeado en #29. Pendiente: 429 por usuario hasta que D4 fije límites. |
| S1.B1 | S | hecho | 2026-09-27 UTC, Arch Linux, Python 3.12.11 (venv de `apps/ingest` recreado desde 3.13), Docker 29.8.0, imagen `maulwurf-ingest:local`. Rama `s1/b1-hardening` (`9f0b991`, `d6204bd`). Unitarias de ingest: 59 passed. `pytest -m integration tests/test_hardening_container.py` contra el `ingest` de compose: 10 passed (I-S1-SG-06, parte B1: `docker inspect`, autochequeo `[]`, `/readyz` 200, escrituras fuera del tmpfs rechazadas, `ENOSPC` antes de 256 MiB, sin volcado tras SIGSEGV, tmpfs vacío al reiniciar). Control negativo: la imagen sin endurecer no arranca (8 fallos) y en modo `report` fallan 5 tests. Mergeado en #33. |
| S1.B3 | S | hecho | 2026-09-27 UTC, Arch Linux, Python 3.12.11, Docker 29.8.0, ffmpeg del sistema y `7:7.1.5` en la imagen, `pgvector/pgvector:pg16` desechable. Rama `s1/b3-supervisor` (`269691b`). `pytest tests/test_supervisor.py`: 8 passed, 5 de 5 ejecuciones (éxito, SIGKILL al hijo con ffmpeg vivo, timeout, descriptor a un borrado → `failed`, lease vencido no publica, 4 `acquire` concurrentes → gana 1, lease por usuario). Control negativo: sin `killpg` falla el test de SIGKILL. En el contenedor endurecido contra la BD de compose: `succeeded`, 5 fragmentos, `verified`, tmpfs vacío; filas de prueba borradas. Suite de ingest: 77 passed. Mergeado en #34. |

## Bloqueos y dependencias

- (el lunes de cada semana: anotar de quién esperas contrato congelado §6 o código real, y qué día)

## Notas de semana

- 2026-09-27: S1.B1 no modifica el host de desarrollo (zram y `core_pattern` a
  systemd-coredump quedan como están). Las garantías se prueban a nivel de contenedor
  (`memswap_limit` = `mem_limit`, `memory.swap.max=0`, `ulimit -c 0`); el host real se
  valida en el despliegue (J4.x). Se registra como excepción, no como aprobación.
- 2026-09-27: aislamiento S1 por filtrado en aplicación y RLS en S2 (ADR-0006).
- 2026-09-27: `extra="forbid"` de pydantic-settings solo valida el `.env`; en Compose las
  variables llegan por entorno y se ignoraban. A1.2 añade una validación explícita.
- 2026-09-27: resuelto en A1.2 (QA): la API no arrancaba con el `.env` de la raíz porque
  trae claves sin prefijo (`POSTGRES_PASSWORD`, `RIVA_*`, `INGEST_*`). Ahora lee
  `../../.env` y `.env`, ignora las claves ajenas y sigue rechazando `MAULWURF_*`
  desconocidas; los errores de validación ya no repiten el valor recibido.
- 2026-09-27: pendiente A1.7: la imagen de `api` no copia `alembic/` ni `alembic.ini`, así
  que no puede migrar desde el contenedor. A1.5 ya incluye `alembic.ini` y `env.py` para
  probar el esquema desde vacío; A1.7 conserva I-S1-AN-04 y la ejecución en compose.
- 2026-09-27: `apps/api/.venv` estaba en Python 3.13.13 (ADR-0002 fija 3.12); recreado con
  `uv sync --python 3.12` y la evidencia de A1.x/J1.5 re-ejecutada en 3.12.11.
- 2026-09-27: QA de A1.4 aplicado; propuestas no aplicadas: prefijo `__Host-` en la cookie
  (cambia el nombre `mw_session` congelado en S1.md §1.1, va a L1.6) y filtro de tenant
  global con `with_loader_criteria` (se evalúa con A1.9).
- 2026-09-27: convención de transacciones: `get_session` no confirma; cada handler que
  escribe hace `commit` explícito (docstring de `app/core/db.py`).
- 2026-09-27: decisiones de A1.4 sin fuente en la spec: sesión de 168 h
  (`MAULWURF_SESSION_TTL_HOURS`) y rechazo de mutaciones sin cabecera `Origin`.
- 2026-09-27: el control negativo de S1.B1 dejó en el host un volcado de un `python -c` sin
  datos (`/var/lib/systemd/coredump/core.python.10001.*`, 1,6 MB). Pertenece a root y lo
  retira systemd-coredump; no contiene audio.
- 2026-09-27: S1.B1 no verificó el swap (zram) ni el `core_pattern` del host; queda para
  el despliegue (J4.x), igual que la excepción anterior.
- 2026-09-28: `cleanup_status = verified` solo se alcanza en el contenedor de ingesta; en
  WSL o sin contenedor, los procesos de otro UID dejan `unreadable_processes > 0` y el
  intento queda `failed` a propósito (L1.6, excepción 5). No se relaja sin revisión de
  seguridad.
- (decisiones, hallazgos, desvíos de alcance — una línea por evento con fecha)

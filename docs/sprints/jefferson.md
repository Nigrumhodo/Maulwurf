# Jefferson Figueroa — Integraciones · Infraestructura · Calidad

> Ficha personal de sprints. Plan maestro: [../PLAN_SPRINTS.md](../PLAN_SPRINTS.md) ·
> Contratos: [../ESPECIFICACION.md](../ESPECIFICACION.md) (§M5, §M8, §9) ·
> Puertas: [../PLAN_IMPLEMENTACION.md](../PLAN_IMPLEMENTACION.md) (G7, G8, D7) ·
> Pruebas: [../PLAN_TESTS.md](../PLAN_TESTS.md) (§1.4, §2.4, §3.4, §4.2)

## Resumen del rol

- Eres dueño de: `infra/` (compose, Caddy), CI/CD, `apps/api/workers/` (ARQ, scheduler, outbox dispatcher), integraciones Google (Calendar + Gmail).
- Tu responsabilidad transversal: **nada se declara hecho sin pipeline verde y evidencia**; tú administramos la conexión entre todo.
- Regla de oro de tus integraciones: toda escritura externa es idempotente y solo tras confirmación humana; toda entrega incierta se reporta, no se reintenta a ciegas.

## Semana 1 (S1) — Infraestructura y CI

### Objetivo
Entorno reproducible y pipeline de CI que valide todo el trabajo del equipo desde el día 2.

### Tareas
- [ ] J1.1 — `infra/docker-compose.yml` con los 8 servicios: `web`, `api`, `ingest`, `worker`, `scheduler`, `postgres` (`pgvector/pgvector:pg16` versión fijada), `redis`, `caddy`. **Sin MinIO/S3 ni volumen de audio.** **Día 1.**
- [ ] J1.2 — Servicio `ingest` con root filesystem read-only, `tmpfs` por instancia, usuario no root, límites de recursos, sin swap/core dumps; ffmpeg/ffprobe en la imagen. **Días 1–2.**
- [ ] J1.3 — Caddy como reverse proxy con TLS local: **sin buffering a disco, sin caché, sin captura de bodies** para uploads; verificar con prueba de carga que no escribe nada. **Días 2–3.**
- [ ] J1.4 — CI: lint (ruff + ESLint), tipos (mypy + tsc), tests (pytest + Vitest), integración con Postgres/pgvector/Redis reales, builds de API/ingest/web. Pipeline sin secretos de proveedores en MRs. **Días 2–4.**
- [ ] J1.5 — Esqueleto de ARQ: `worker` y `scheduler` conectados a Redis, `outbox_events` dispatcher básico, `GET /healthz` por proceso y `GET /readyz` (API: Postgres/Redis; ingest: BD, temporales seguros, cleanup y capacidad; **sin invocar proveedores pagados**). **Días 3–4.**
- [ ] J1.6 — Redis solo con IDs/estado (nunca payloads de audio); persistencia configurada solo para Postgres (y Redis si se decide). **Día 4.**
- [ ] J1.7 — README con comandos reproducibles (levantar, testear, lint). **Día 5.**
- [ ] J1.8 — Soporte al spike de Santiago: imagen base con ffmpeg disponible temprano. **Día 2.**

### Entregables verificables
- `docker compose up` levanta los 8 servicios — el equipo trabaja sobre esto desde el martes.
- Pipeline CI verde en MRs — evidencia en el log del pipeline.
- Prueba de no-buffering del proxy — documento con el método y resultado.

### Interfaces que provee
- Compose + servicios — todos.
- Shape de outbox + esqueleto ARQ — consumidores: Andres (emisores), José (consumidor del índice).

### Alcance
**Dentro:** compose con los 8 servicios, imagen `ingest` endurecida, Caddy sin buffering, CI
completo, esqueleto ARQ + dispatcher básico, healthchecks, política Redis, README reproducible,
soporte temprano al spike de Santiago.
**Fuera:** tablas y emisores de outbox (Andres); job `index` real (S2); clientes de Google (S3);
scaffold del API (Andres); no deployas producción en S1.

### Criterios de aceptación (cierre de la semana)
- [ ] `docker compose up` levanta los 8 servicios y todo el equipo trabaja sobre compose el martes.
- [ ] Pipeline CI en verde en MRs (lint, tipos, tests, builds) sin secretos de proveedores.
- [ ] Prueba de no-buffering del proxy documentada con método y resultado.
- [ ] `/readyz` de ingest verifica temporales seguros y capacidad sin invocar proveedores pagados.

### Pruebas requeridas
- Smoke — healthchecks de los 8 servicios tras `compose up` limpio — script de CI.
- Carga ligera — upload grande vía Caddy mientras se inspecciona que no aparece ningún archivo —
  evidencia sin audio.

### Riesgos y plan B
- La imagen `ingest` con ffmpeg tarda más de lo previsto → señal: día 2 → entregar imagen base
  sin endurecer para desbloquear el spike de Santiago y endurecer en paralelo (solo para spike,
  nunca para ingesta real).
- CI inestable por servicios externos → mocks solo para CI; los contratos reales se validan en
  las pruebas locales con servicios reales de compose.

### Dependencias y bloqueos
- Necesitas de Andres: contratos de sesión/BD para que los servicios compartan auth sin duplicar.
- Necesitas de José: ADR de versiones/estructura para fijar imágenes.

## Semana 2 (S2) — Outbox, workers y observabilidad (F1)

### Objetivo
El trabajo de texto fluye de forma durable: outbox → Redis → worker idempotente, con recuperación ante pérdida de Redis.

### Tareas
- [ ] J2.1 — Dispatcher de outbox real: publica a Redis **solo IDs/configuración no sensible** cuando `cleanup_status=verified`; nunca antes. **Días 1–2.**
- [ ] J2.2 — Job `index(audio_id)` idempotente: consume el job de José, verifica propiedad/tombstone/versión antes de publicar, reintentos con backoff + jitter (máx 3 transitorios). **Días 2–3.**
- [ ] J2.3 — Reconstrucción si se pierde Redis: BD/outbox permiten regenerar trabajos durables; probar el camino completo. **Día 3.**
- [ ] J2.4 — Reconciliador: intentos muertos marcados, outbox atascada re-publicada, ejecuciones duplicadas resueltas por unicidad/lease. **Días 3–4.**
- [ ] J2.5 — Observabilidad mínima: logs JSON con `request_id/audio_id/attempt_id/run_id` sin payloads sensibles; métricas de RAM/tmpfs/slots, lag de outbox, retries, edad de temporales; alerta inmediata por cleanup fallido. **Días 4–5.**
- [ ] J2.6 — Smoke E2E automatizado: upload → transcript → cleanup → índice → pregunta con cita, contra compose. **Día 5.**
- [ ] J2.7 — Fault injection con Andres/Santiago: crash antes/después del commit; expiración de lease no marca cleanup verificado. **Día 5.**

### Entregables verificables
- G5 con evidencia: sin versiones parciales activas ni duplicados tras retries; crash resuelto según contrato.
- Smoke E2E en el pipeline — se ejecuta en cada MR.
- Panel de métricas mínimo — el equipo ve estados en tiempo real el viernes.

### Alcance
**Dentro:** dispatcher de outbox con gating de cleanup, job `index` idempotente, reconstrucción
tras pérdida de Redis, reconciliador, observabilidad mínima, smoke E2E, fault injection.
**Fuera:** lógica del índice (José); schema de outbox (Andres); pipeline ASR (Santiago);
Google (S3); Gmail (S4).

### Criterios de aceptación (cierre de la semana)
- [ ] G5 con evidencia: sin versiones parciales activas ni duplicados tras retries.
- [ ] Pérdida total de Redis: el sistema se reconstruye desde BD/outbox (prueba real, no teórica).
- [ ] Smoke E2E ejecutándose en cada MR del pipeline.
- [ ] Fault injection del día 5 con Andres/Santiago documentada.

### Pruebas requeridas
- Integración — crash antes/después del commit, expiración de lease, outbox atascada — pytest con
  servicios reales de compose.
- Reconstrucción — `redis-cli FLUSHALL` en un entorno de prueba y verificar regeneración de jobs.

### Riesgos y plan B
- Redis se degrada bajo carga en la demo → persistencia de Postgres/outbox como fuente de verdad ya
  diseñada; en el peor caso los jobs se re-disparan desde BD durante la demo.
- El smoke E2E es flaky en CI → reintentos acotados y marca `allow-failure` temporal solo para
  diagnose, con ticket para estabilizar antes de S3; nunca en el MR de cierre de semana.

### Interfaces que consume
- Outbox y estados de cleanup — dueño: Andres/Santiago.
- Índice y búsqueda — dueño: José.

### Interfaces que provee
- Job runner idempotente — consumidor: José (`analyze` en S3).
- Métricas/healthchecks — consumidor: todos; base de G8.

## Semana 3 (S3) — Google Calendar (F2)

### Objetivo
Confirmación humana → exactamente un evento, con reconciliación ante fallos remotos.

### Tareas
- [ ] J3.1 — Verificar scopes reales (D7): probar `calendar.app.created` + `calendar.events.readonly` + `calendar.calendarlist.readonly` con llamadas reales; registrar qué permite cada uno; **no asumir**. **Día 1.**
- [ ] J3.2 — `GoogleCalendarService`: cliente bloqueante aislado del event loop; reintentos acotados para 429/5xx respetando `Retry-After`; `invalid_grant` desconecta (no cualquier 403 es reintentable). **Días 1–2.**
- [ ] J3.3 — Calendario secundario único «Maulwurf» al conectar; guardar ID; reconciliar creación ambigua antes de repetir. **Día 2.**
- [ ] J3.4 — Job `sync_task_event(task_id, task_version)`: ID de evento determinista desde el UUID de la tarea (hex/base32hex permitido por Google); timeout de insert → consultar ese ID; 409 → comprobar identidad y reconciliar; descartar jobs de versiones obsoletas. **Días 2–4.**
- [ ] J3.5 — Evento con prefijo `[Maulwurf]`, detalle y **link autenticado al segmento textual** (sin audio ni tokens en URL); all-day con `start.date`/`end.date` fin exclusivo; con hora, inicio/fin válidos y zona IANA; duración por defecto visible/editable antes de confirmar. **Días 3–4.**
- [ ] J3.6 — Patch del evento al confirmar revisión, con control de ETag; no sobrescribir cambios externos; completar/descartar cancela recordatorios pero conserva el evento. **Día 4.**
- [ ] J3.7 — Lectura: ventana -7/+60 días con paginación completa, recurrencias expandidas, eliminaciones; publicación atómica en `calendar_events` (espejo de Andres); sin mezclar `syncToken` con filtros incompatibles; registrar cobertura y `last_synced_at`. **Días 4–5.**
- [ ] J3.8 — Conflicto horario (solape con hora) distinto de concentración de entregas por día; endpoint `GET /calendar/conflicts` y `POST /calendar/sync`. **Día 5.**
- [ ] J3.9 — Cuenta Google de prueba protegida; pruebas remotas crean solo recursos de prueba y los limpian. **Continuo.**

### Entregables verificables
- Doble clic / respuesta perdida / reintento concurrente **no duplica** eventos — demo con G7.
- Rechazo de scopes no rompe biblioteca/chat — prueba con cuenta sin permisos.
- Lectura de agenda con cobertura visible — dashboard de Daniel la consume.

### Alcance
**Dentro:** verificación de scopes (D7), `GoogleCalendarService`, calendario secundario único, job
`sync_task_event` idempotente, patch con ETag, lectura con paginación/expansiones, espejo
`calendar_events`, endpoint de conflictos.
**Fuera:** Gmail (S4); tabla `tasks`/outbox de confirmación (Andres); UI de estados (Daniel);
definición de tools del chat (José).

### Criterios de aceptación (cierre de la semana)
- [ ] G7 demostrada: doble clic/respuesta perdida/reintento concurrente no duplica eventos.
- [ ] Rechazo de scopes no rompe biblioteca ni chat (probado con cuenta sin permisos).
- [ ] Reconciliación de timeout/409 probada con el ID determinista, no asumida.
- [ ] Cobertura de lectura de agenda visible (ventana -7/+60, recurrencias, eliminaciones).

### Pruebas requeridas
- Provider-contract — llamadas reales a Calendar con cuenta protegida: insert/patch/409/timeout,
  reintentos respetando `Retry-After` — sin tocar datos reales; recursos de prueba y limpieza.
- Integración — outbox → sync job → espejo `calendar_events` — pytest con compose.

### Riesgos y plan B
- Cuota de Google agotada por pruebas → presupuesto de llamadas diario documentado antes de
  empezar; pruebas destructivas solo en el calendario de prueba; si se agota, priorizar el camino
  feliz y dejar la reconciliación de conflictos documentada con la prueba diferida.
- Scopes menos potentes de lo esperado (D7) → registrar el hallazgo, ajustar el flujo con Andres
  (estado `blocked`) y comunicarlo a José/Daniel el mismo día para no arrastrar UIs sobre supuestos.

### Interfaces que consume
- Outbox de confirmación — dueño: Andres.
- `calendar_events` — dueño: Andres.
- Tools del chat (definición) — dueño: José.

### Interfaces que provee
- Contrato de sync `tasks → calendar` — consumidores: Andres (estados), Daniel (UI de estados).
- Eventos de agenda para chat/dashboard — consumidores: José (tool), Daniel (dashboard).

## Semana 4 (S4) — Gmail, recordatorios y despliegue (F3)

### Objetivo
Recordatorios confiables con entrega incierta bien manejada, y el proyecto desplegado y observable.

### Tareas
- [ ] J4.1 — Gmail opt-in con `gmail.send` incremental; destinatario fijo al email verificado; templates HTML + texto plano escapados; ningún GET de correo muta estado; «Posponer» abre UI autenticada. **Días 1–2.**
- [ ] J4.2 — Scheduler ARQ con rol único activo: calcula cada minuto vencimientos/horas locales y reclama filas atómicamente; workers múltiples protegidos por unicidad y leases; SLA objetivo 15 min. **Días 1–2.**
- [ ] J4.3 — Recordatorios escalados: T-48/T-24/T-2 h para instantes; T-3d/T-1d a hora local para all-day; tareas sin fecha no tienen recordatorios; sin ráfaga retrospectiva al confirmar tarde. **Día 2.**
- [ ] J4.4 — Dedupe persistente `(user_id, tipo, task_id, due_version, occurrence)`; cambiar vencimiento/preferencias cancela ocurrencias obsoletas; DST: hora inexistente → siguiente instante válido, hora repetida → una sola vez. **Días 2–3.**
- [ ] J4.5 — `delivery_unknown` cuando se pierde la respuesta tras enviar o el lease `sending` vence: **sin reintento automático**; visible en panel; reexpedición manual advierte del riesgo. **Días 3–4.**
- [ ] J4.6 — Digests diario (07:00 configurable) y semanal (domingo) por fecha local, con conflictos de la semana. **Día 4.**
- [ ] J4.7 — Despliegue de referencia (servidor ARM + dominio + Cloudflare Tunnel según propuesta §5), imágenes versionadas, migración Alembic como job único, rollback probado. **Días 3–5.**
- [ ] J4.8 — Presupuesto/alertas activos (tokens, ASR, cuotas) y runbooks: cleanup, caída de proveedor, tokens revocados, restore, borrado de cuenta. **Días 4–5.**
- [ ] J4.9 — G8: builds/tests de las 3 apps, restore comprobado, recordatorios dentro del objetivo con dependencias saludables. **Viernes.**
- [ ] J4.10 — Envío real de prueba observado con cuenta autorizada y limpieza posterior. **Viernes.**

### Entregables verificables
- Envío real observado + dedupe/DST probados — G7/G8 cerradas.
- Restore de backup comprobado sin reintroducir fuentes borradas — con Andres.
- Despliegue con alertas y presupuesto — demo final corre en él.

### Alcance
**Dentro:** Gmail opt-in con dedupe, scheduler con rol único, recordatorios escalados, manejo de
`delivery_unknown`, digests, despliegue de referencia, alertas/presupuesto, runbooks, cierre G8,
envío real de prueba.
**Fuera:** tabla `notifications`/preferencias (Andres); UI de panel (Daniel); evaluación final
(José); re-verificación G1 (Santiago).

### Criterios de aceptación (cierre de la semana)
- [ ] Envío real observado con cuenta autorizada + limpieza posterior (G7/G8).
- [ ] `delivery_unknown` sin reintento automático, visible en panel, reexpedición manual advierte.
- [ ] Restore comprobado sin reintroducir borrados (con Andres).
- [ ] Dedupe/DST probados: hora inexistente → siguiente válido; hora repetida → una sola vez.
- [ ] Demo final corriendo en el despliegue de referencia con alertas activas.

### Pruebas requeridas
- Provider-contract — envío real de Gmail con cuenta protegida, observado de punta a punta con
  limpieza — una vez, controlado, no en CI.
- Integración — scheduler: vencimientos, DST, ocurrencias canceladas, leases — pytest.

### Riesgos y plan B
- Despliegue (ARM/túnel) no listo a mitad de semana → la demo corre en compose local; el
  despliegue se termina en paralelo y la demo final se repite allí antes del cierre.
- Gmail rechaza la plantilla por políticas → simplificar a texto plano escapado y reintentar; el
  HTML se degrada, el flujo no se bloquea.

### Interfaces que consume
- `notifications` y preferencias — dueño: Andres.
- Estados de tareas — dueño: Andres.

### Interfaces que provee
- Panel de entregas/sincronizaciones fallidas — consumidor: Daniel (dashboard final).

## Dependencias semanales — qué NO te bloquea

Regla anti-ciclo (§5a del plan maestro): consumes el **contrato congelado el lunes**; el código
real de quien te provee llega a más tardar el día 3; si tarda, construyes con mock y conmutas el
miércoles/jueves. Tu mapa por semana:

| Semana | No esperes a | Consumes desde el lunes | Código real disponible |
|---|---|---|---|
| S1 | Andres (esqueleto API) | ADR de versiones/estructura de José (día 1) | Compose no necesita el API para levantar servicios; healthchecks dummy se sustituyen cuando Andres merga día 3–5 |
| S2 | José (índice real) | Contrato del job de índice congelado | Índice stub que registra la llamada; conectas el real de José día 4–5 |
| S3 | Andres (confirmación atómica) | Shape de outbox de confirmación congelado | Confirmación atómica de Andres día 3; hasta entonces pruebas Calendar con outbox simulada (cuenta de prueba) |
| S4 | Andres (tabla `notifications`) | Contrato M8 congelado el lunes | Tabla de Andres día 1–2; el scheduler se prueba con filas sembradas |

# Andres Felipe — Backend · Base de datos

> Ficha personal de sprints. Plan maestro: [../PLAN_SPRINTS.md](../PLAN_SPRINTS.md) ·
> Contratos: [../ESPECIFICACION.md](../ESPECIFICACION.md) (§M1, §M2, §M4, §6) ·
> Puertas: [../PLAN_IMPLEMENTACION.md](../PLAN_IMPLEMENTACION.md) (G3, G5) ·
> Pruebas: [../PLAN_TESTS.md](../PLAN_TESTS.md) (§1.2, §2.2, §3.2, §4.1)

## Resumen del rol

- Eres dueño de: `apps/api` (routers, services, models) excepto `prompts/` (José) y `workers/` (Jefferson).
- Tu responsabilidad transversal: **aislamiento tenant** — toda tabla con `user_id`, toda query filtrada, identidad solo desde la sesión del backend.
- Tu palabra clave: el modelo de datos soporta cada contrato de la especificación; si una query necesita algo que el esquema no tiene, el esquema cambia primero.

## Semana 1 (S1) — Fundación

### Objetivo
API corriendo en compose con auth y esquema núcleo; el equipo puede empezar a construir sobre tus contratos.

### Tareas
- [ ] A1.1 — Scaffold `apps/api`: FastAPI + Pydantic v2 + SQLAlchemy 2 async, estructura routers/services/models/core (§3.3). **Día 1.**
- [ ] A1.2 — Config con Pydantic Settings; `NVIDIA_API_KEY` y secretos solo por entorno, nunca en código. **Día 1.**
- [ ] A1.3 — OAuth Google en el backend: Authorization Code con `state`, PKCE, validación de `nonce`/issuer/audience; identidad por `sub` de Google, no email. **Días 2–3.**
- [ ] A1.4 — Sesión opaca: cookie `Secure`/`HttpOnly`/`SameSite=Lax`, solo hash del identificador en `sessions`, expiración/revocación; CSRF y validación de Origin en mutaciones. **Días 2–3.**
- [ ] A1.5 — Tablas núcleo: `users`, `sessions`, `google_credentials` (tokens cifrados AES-GCM con nonce único y versión de clave), `subjects`; FKs compuestas `(user_id, recurso_id)`; RLS base. **Días 3–4.**
- [ ] A1.6 — CRUD materias `GET/POST /subjects`, `PATCH/DELETE /subjects/{id}`; impedir borrar materia con clases activas sin moverlas o confirmar. **Día 4.**
- [ ] A1.7 — Migraciones Alembic: desde vacío y actualización; probar con pool de conexiones y workers. **Día 4.**
- [ ] A1.8 — Esqueleto de `POST /audios` + `PUT` binario según el contrato congelado; `GET /me`, `PATCH /me`, logout e integraciones status. **Día 5.**
- [ ] A1.9 — Tests de aislamiento básicos: usuario B no ve recursos de A ni con IDs adivinados. **Día 5.**

### Entregables verificables
- Login/logout/me funcionando en compose — Daniel puede construir la UI contra esto.
- Migraciones desde vacío y actualización pasan — comando documentado en el README.
- Tablas núcleo con FKs compuestas y RLS — tests de aislamiento en verde.

### Interfaces que provee
- Contrato de sesión/cookie/CSRF — consumidores: todos (API, ingesta, web).
- `POST /audios` + `PUT` binario — consumidores: Santiago (ingesta), Daniel (upload).
- Tablas núcleo — consumidor: todo el backend.

### Alcance
**Dentro:** scaffold `apps/api`, config/secretos, OAuth+PKCE, sesión opaca/CSRF, tablas núcleo,
CRUD materias, migraciones, esqueleto `POST /audios`+`PUT`, primeros tests de aislamiento.
**Fuera:** servicios `worker`/`scheduler` (Jefferson); pantallas de login (Daniel); lógica de
transcripción (Santiago); prompts (José). El `PUT` binario S1 es solo esqueleto: la recepción
real la completa Santiago en S2.

### Criterios de aceptación (cierre de la semana)
- [ ] Login/logout/`GET /me` funcionan en compose contra Caddy de Jefferson.
- [ ] Migraciones desde vacío y actualización pasan en CI.
- [ ] Tests de aislamiento: usuario B no accede a recursos de A ni adivinando IDs.
- [ ] Tokens de Google cifrados AES-GCM con nonce único y versión de clave; nada plano en BD o logs.

### Pruebas requeridas
- Integración — migraciones contra Postgres real de compose — pytest + CI de Jefferson.
- Unit/seguridad — sesiones: revocación, expiración, CSRF rechazado sin token/Origin — pytest.

### Riesgos y plan B
- OAuth de prueba no disponible el día 2 → desarrollar sesión opaca + CSRF con login de desarrollo
  marcado `feature flag` temporal y nunca mergeado a demo; OAuth se integra en cuanto haya
  credenciales, sin cambiar el contrato de sesión.

### Dependencias y bloqueos
- Necesitas de Jefferson: `postgres` + `pgvector` en compose y credenciales OAuth de prueba en variables de entorno.
- Necesitas de José: decisión de versiones y estructura (día 1).

## Semana 2 (S2) — Ingesta, transcript y búsqueda (F1)

### Objetivo
Todos los endpoints de M2 operativos sobre el esquema completo de transcript/chunks/índice.

### Tareas
- [ ] A2.1 — Tablas: `audios` (solo metadatos, **sin storage_key**), `ingestion_attempts` (lease/heartbeat/fencing/cleanup_status), `transcripts` (único por audio+versión), `segments` (ordinal, texto, offsets de caracteres, tiempos nullables), `processing_runs`, `outbox_events` (solo IDs/config). **Días 1–2.**
- [ ] A2.2 — `GET /ingestion/capabilities` (datos del spike de Santiago). **Día 1.**
- [ ] A2.3 — `POST /audios`: valida metadatos (materia, fecha, zona horaria, idioma de la allowlist), crea intento `awaiting_upload`, devuelve URL de upload relativa + expiración. **Día 2.**
- [ ] A2.4 — `PUT` binario autenticado con sesión/CSRF/attempt_id; 202 solo tras recepción/admisión; compensar reserva si falla BD; cortar streaming al superar límite. **Días 2–3.**
- [ ] A2.5 — Deduplicación por identidad exacta (hash, idioma, materia, fecha, zona): unicidad transaccional; variante exige confirmación explícita; nunca retener audio esperando decisión. **Día 3.**
- [ ] A2.6 — Estados por etapa independientes (`not_requested|pending|running|succeeded|failed|cancelled`) + estado agregado (`processing/ready/partial/failed`); `not_requested` ≠ éxito. **Día 3.**
- [ ] A2.7 — Cancelación, tombstone y fencing: invalidar token de ejecución; commits tardíos rechazados; `DELETE /audios/{id}` con purga de derivados. **Días 3–4.**
- [ ] A2.8 — `retry` (solo análisis/índice) y `reupload` (solo sin transcript utilizable); transcribir el conflicto correcto en cada caso. **Día 4.**
- [ ] A2.9 — `GET /audios?subject_id=`, `GET /audios/{id}`, SSE `GET /audios/{id}/progress` con snapshot desde BD, `transcript.txt`, `transcript.srt` (solo con tiempos válidos). **Días 4–5.**
- [ ] A2.10 — Recibir commit atómico de transcript/segmentos del servicio de ingesta; outbox deshabilitada hasta `cleanup_status=verified`. **Día 5.**
- [ ] A2.11 — Endpoint `GET /search` (con José): filtros tenant/materia/fechas; snippet + audio + timestamp válido. **Día 5.**

### Entregables verificables
- Suite de endpoints M2 probada (incluye concurrencia de dedupe y reupload fallido).
- Esquema completo soporta chunking/embeddings/versiones de José sin migración de emergencia.
- G3 (aislamiento) con evidencia en búsqueda, SSE y borrado concurrente.

### Interfaces que consume
- Límites efectivos del spike — dueño: Santiago.
- Contrato de commit de transcript — dueño: Santiago.

### Interfaces que provee
- Todos los endpoints M2 — consumidores: Daniel (UI), Jefferson (outbox/worker).
- Tablas chunks/embeddings/outbox — consumidor: José (índice), Jefferson (dispatcher).

### Alcance
**Dentro:** esquema completo de transcript/ingesta (M2), endpoints de capabilities/upload/dedupe/
estados/cancelación/tombstone, `retry`/`reupload`, listado/SSE/transcripts exportables, commit del
servicio de ingesta, `GET /search` (con José).
**Fuera:** lógica de fragmentación/ASR (Santiago); chunking/embeddings (José); pantallas
(Daniel); dispatcher/worker (Jefferson). No almacenas nunca audio ni `storage_key`.

### Criterios de aceptación (cierre de la semana)
- [ ] Suite M2 en verde: dedupe concurrente, `reupload` fallido, tombstone con commit tardío rechazado.
- [ ] Esquema soporta chunks/embeddings/versiones sin migración de emergencia durante S2.
- [ ] SSE sirve snapshot desde BD reconectable; outbox solo con `cleanup_status=verified`.
- [ ] `DELETE /audios/{id}` purga derivados sin dejar transcript huérfano.

### Pruebas requeridas
- Integración — concurrencia: dedupe simultáneo, doble confirmación, commit tardío tras tombstone —
  pytest contra Postgres real.
- Contrato — commit de transcript de Santiago: transacción atómica y estados coherentes — pytest.

### Riesgos y plan B
- El esquema no soporta algo del índice de José a mitad de semana → cambiar el esquema primero
  (tu regla) y avisar en el daily; congela la versión del contrato de chunks el día 2 con José.

## Semana 3 (S3) — Tareas, evidencia y revisiones (F2)

### Objetivo
Modelo de tareas con control de versiones, evidencia verificada y estados separados de Calendar.

### Tareas
- [ ] A3.1 — Tabla `tasks` completa: tipo, título, detalle, `due_date`/`due_at` mutuamente excluyentes con CHECKs, `date_status`, `confidence_score` [0,1] nullable, `status`, `calendar_sync_status` separado, `version`, pares de inicio/fin Calendar, revisión pendiente. **Días 1–2.**
- [ ] A3.2 — Evidencia persistida: transcript/segmentos/spans + texto original de fecha + motivo de revisión + `due_version` + snapshot de zona horaria. **Día 2.**
- [ ] A3.3 — Endpoints M4: `GET /tasks?status=`, `PATCH /tasks/{id}` con versión (optimistic locking), `confirm`, `dismiss`, `complete`, `POST /tasks` manual/chat. **Días 2–3.**
- [ ] A3.4 — Confirmación atómica: guarda tarea + outbox de evento en una transacción; 202 con `calendar_sync_status=pending`; sin integración → confirmada + `blocked`. **Día 3.**
- [ ] A3.5 — Reanálisis crea revisión nueva: no borrar ni sobrescribir tareas editadas/confirmadas/programadas; activar revisión solo al completarse. **Días 3–4.**
- [ ] A3.6 — Reconstrucción de evidencia desde BD para validar spans de José: offsets Unicode por segmento; rechazar pertenencia inválida. **Días 3–4.**
- [ ] A3.7 — Reglas de estado: resolver fecha/calidad antes de confirmar; confirmar exige fecha válida y evidencia comprobada; `scheduled` no es estado de tarea. **Día 4.**
- [ ] A3.8 — Tabla `calendar_events` espejo (clave `(user_id, calendar_id, event_id)`, all-day separado de instantes, ETag, `last_synced_at`) para Jefferson. **Días 4–5.**
- [ ] A3.9 — Tests: doble análisis no duplica, edición concurrente no pierde datos, doble confirmación no crea doble outbox. **Día 5.**

### Entregables verificables
- Endpoints M4 con locking de versión — demo de edición concurrente sin pérdida.
- Evidencia 100% validada contra BD — ninguna propuesta se publica con span inventado.
- `calendar_events` lista para el worker de Jefferson.

### Alcance
**Dentro:** tabla `tasks` con versionado y CHECKs, evidencia persistida, endpoints M4 con locking,
confirmación atómica con outbox, reanálisis como revisión nueva, validación de spans de José,
espejo `calendar_events`, tests de idempotencia.
**Fuera:** prompts/validadores de extracción (José); bandeja y edición UI (Daniel); job de sync
de Calendar (Jefferson); nada de llamadas a la API de Google desde `apps/api`.

### Criterios de aceptación (cierre de la semana)
- [ ] Demo: edición concurrente no pierde datos (409 + refresco), doble análisis no duplica,
      doble confirmación no crea doble outbox.
- [ ] Ninguna tarea con evidencia no reconstruible desde BD pasa a confirmable.
- [ ] `calendar_events` con clave compuesta, ETag y `last_synced_at` lista para Jefferson.

### Pruebas requeridas
- Integración — locking optimista, revisiones sin sobrescritura, outbox atómica — pytest.
- Contrato — validación de spans de José contra segmentos reales — pytest compartido.

### Riesgos y plan B
- El schema de extracción de José cambia a mitad de semana → fijar `prompt_version` y versionar el
  esquema en BD desde el día 1; los campos nuevos se añaden como nullable, nunca rompiendo filas.
- Confirmación atómica falla a mitad de transacción → la outbox garantiza reintento idempotente;
  probar el camino en el test A3.9 antes del viernes.

### Interfaces que consume
- Schema de extracción y spans — dueño: José.
- Contrato de Calendar (ID determinista, ETag) — dueño: Jefferson.

### Interfaces que provee
- Contrato `tasks` + revisiones — consumidores: Daniel (bandeja), Jefferson (sync job), José (chat tool `propose_task` en S4).

## Semana 4 (S4) — Preferencias, borrado y cierre

### Objetivo
Ciclo de vida completo de usuario y hardening de validaciones.

### Tareas
- [ ] A4.1 — Tabla `notifications` según M8: `scheduled_at`, `due_version`, `dedupe_key`, estados (`pending|sending|sent|failed|delivery_unknown|cancelled`), `provider_message_id`, errores redactados. **Día 1.**
- [ ] A4.2 — `PATCH /me/notifications` (preferencias) y `POST /notifications/test`. **Día 1–2.**
- [ ] A4.3 — Cambiar vencimiento/preferencias cancela ocurrencias futuras obsoletas; nunca ráfaga retrospectiva al confirmar tarde. **Día 2.**
- [ ] A4.4 — Borrado de cuenta: tombstone, bloquear sesiones/jobs, purgar datos y fuentes derivadas; si se piden eventos, ejecutar borrado remoto antes de revocar tokens. **Días 2–3.**
- [ ] A4.5 — Política D8: backups solo texto/metadatos cifrados, retención 30 días, borrados reaplicados al restaurar. **Día 3.**
- [ ] A4.6 — Hardening final de validaciones: ownership en API/SSE/tools/jobs; `no-store` en contenido privado; rate limits básicos con `429/503`. **Días 3–4.**
- [ ] A4.7 — Tests de aislamiento finales (G3 completo) y soporte de restore. **Día 4.**
- [ ] A4.8 — Soporte en ensayo de demo. **Viernes.**

### Entregables verificables
- Borrado de cuenta purga datos y fuentes derivadas — verificado en E2E.
- Restore no reintroduce borrados — prueba documentada.
- G3 cerrada con evidencia completa.

### Alcance
**Dentro:** tabla `notifications` (M8), preferencias, cancelación de ocurrencias futuras, borrado
de cuenta con tombstone y purga, política D8 de backups, hardening de validaciones y rate limits
básicos, tests G3 finales.
**Fuera:** scheduler/envío real (Jefferson); panel de entregas (Daniel); evaluación final (José).

### Criterios de aceptación (cierre de la semana)
- [ ] E2E de borrado de cuenta: sesiones/jobs bloqueados, purga completa, restore no reintroduce.
- [ ] Cambiar vencimiento/preferencias cancela ocurrencias futuras sin ráfaga retrospectiva.
- [ ] Rate limits activos con `429/503` y cabeceras correctas en endpoints sensibles.
- [ ] G3 cerrada con evidencia completa (búsqueda, SSE, tools, jobs, borrado concurrente).

### Pruebas requeridas
- Integración — borrado de cuenta: doble ejecución idempotente, jobs en vuelo, tokens revocados —
  pytest + E2E con Jefferson.
- Restore — probar que un backup restaurado reaplica tombstones — con Jefferson (J4.9).

### Riesgos y plan B
- La purga de derivados deja huérfanos (chunks/embeddings sin clase) → sweep de integridad + test
  de huérfanos en CI antes del viernes; si aparece alguno, bloquear la G3 hasta corregirlo.

## Dependencias semanales — qué NO te bloquea

Regla anti-ciclo (§5a del plan maestro): consumes el **contrato congelado el lunes**; el código
real de quien te provee llega a más tardar el día 3; si tarda, construyes con mock y conmutas el
miércoles/jueves. Tu mapa por semana:

| Semana | No esperes a | Consumes desde el lunes | Código real disponible |
|---|---|---|---|
| S1 | Jefferson (compose) ni José (versiones) | Decisión de versiones del día 1 (ADR de José) | Postgres+pgvector en compose día 1–2; mientras, Postgres local efímero (`docker run`, sin audio) |
| S2 | Santiago (cliente Riva) | Shape de commit de transcript/estados congelado S1 | Cliente Riva de Santiago día 3–4; tus endpoints se prueban con commits simulados (fixtures de texto) |
| S3 | José (schema de extracción final) | Borrador Pydantic §M4 congelado desde S1 | Prompts/validadores de José día 1–2; tú ejecutas la validación de spans después (A3.6) |
| S4 | Jefferson (scheduler/envío) | Contrato M8: la tabla `notifications` es tuya | Tú entregas tabla+endpoints día 1–2; Jefferson conecta el scheduler después |

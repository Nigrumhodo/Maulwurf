# Maulwurf — Plan de sprints (4 semanas)

> Estado: plan operativo del equipo, revisión 2026-09-16. Subordinado a
> [ESPECIFICACION.md](ESPECIFICACION.md) (contratos) y
> [PLAN_IMPLEMENTACION.md](PLAN_IMPLEMENTACION.md) (puertas y evidencias).
> Si este documento contradice la especificación, gana la especificación. Este
> plan define *quién hace qué y cuándo*; no declara nada implementado.

## Equipo y roles (de PROPUESTA_PROYECTO_FINAL.md §4)

| Persona | Rol | Dominio |
|---|---|---|
| **José Leonardo Pinilla Zamora** | Líder técnico · RAG | Coordinación, arquitectura, chunking/embeddings/RRF, chat, prompts, evaluación de calidad |
| **Andres Felipe** | Backend · Base de datos | API FastAPI, modelos/migraciones, auth/sesiones, aislamiento tenant, outbox |
| **Santiago Montealegre** | Audio · Transcripción | FFmpeg/ffprobe, NVIDIA Riva, tmpfs efímero, supervisor/lease/fencing, cleanup |
| **Jefferson Figueroa** | Integraciones · Infra · Calidad | Docker Compose, CI/CD, Redis/ARQ/scheduler, Google Calendar/Gmail, pruebas E2E |
| **Daniel Samaca** | Frontend · UX | Next.js, formularios, biblioteca/lector, chat UI, bandeja de revisión, dashboard |

Principio anti-choques: **cada persona toca carpetas propias**; la única zona
compartida por semana es la lista de **interfaces congeladas** (§5) que se
acuerdan el lunes de cada sprint y ya no se cambian esa semana.

## 0. Resumen para el equipo

| Semana | Nombre | Objetivo en una frase | Puerta de salida |
|---|---|---|---|
| S1 | **Fundación + spike** | Monorepo corriendo, auth básica, esquema inicial, y contrato real de Riva probado | Builds/tests verdes; informe F0 con idiomas/límites/timestamps |
| S2 | **Conocimiento (F1)** | Subir audio → transcript → índice híbrido → chat con citas, en un corte vertical completo | E2E con clase sintética de 1 h; G1–G3 y avance G4/G5 |
| S3 | **Acción (F2)** | Extracción LLM, bandeja de revisión, Calendar idempotente, tools de lectura | G6/G7: confirmación → un único evento; ninguna escritura sin confirmar |
| S4 | **Recordatorios y cierre (F3)** | Gmail opt-in, recordatorios/digests, dashboard completo, Ctrl+K, hardening | G8; demo final + seguridad/costos revisados |

Regla dura: **nada de ingesta real antes de que S1 cierre la puerta F0**
(retención de proveedores D3, límites D2/D4, timestamps D6).

## 1. Semana 1 (S1) — Fundación + spike de viabilidad (F0)

Criterios de salida: monorepo §3.3 creado, `docker compose up` con los 8
servicios sin almacenamiento de audio, login Google funcional en local,
migraciones desde vacío, pipeline CI en verde, informe del spike con evidencia
(versiones, llamadas, idiomas, formatos, límites, comportamiento de timestamps).

| Persona | Responsabilidades S1 | Entregables verificables |
|---|---|---|
| **José Leonardo** | Prioriza y desbloquea; crea repos/estructura monorepo; decide versiones fijadas y lockfiles; prepara protocolo del spike de Riva y el borrador del dataset de evaluación; revisa PRs de todos | ADRs cortos de decisiones (estructura, versiones); plantilla de informe del spike; esqueleto de `apps/api/prompts/` y criterios de evaluación G4/G6 |
| **Andres** | Scaffold `apps/api` (FastAPI + Pydantic v2 + SQLAlchemy async); OAuth Google + sesión opaca + CSRF/Origin; tablas núcleo: users, sessions, google_credentials, subjects; FKs compuestas y RLS base; CRUD materias | API corre en compose; login/logout/me; migraciones desde vacío; tests de aislamiento básicos |
| **Santiago** | Spike bloqueante F0.1/F0.2 con audio sintético **generado en RAM**: idiomas candidatos, formatos, sample rate, límites payload/duración, cuotas, offsets/timestamps, latencia y memoria del SDK; prototipo de ingesta tmpfs + supervisor/lease/fencing + cleanup en `finally` | Informe de contrato NVIDIA redactado (sin claves ni audio); decisión `word|segment|none` documentada; prototipo de limpieza ante éxito/SIGKILL/timeout demostrado |
| **Jefferson** | `infra/docker-compose.yml` (web, api, ingest, worker, scheduler, postgres+pgvector, redis, caddy); CI (lint ruff/ESLint, mypy/tsc, pytest/Vitest, integración, builds); Caddy como proxy **sin buffering a disco**; esqueleto de ARQ + outbox + `/healthz` `/readyz` | Pipeline CI verde en MR; compose levanta todo; prueba de no-buffering del proxy documentada |
| **Daniel** | Scaffold `apps/web` (Next.js + TS + Tailwind + shadcn/ui); layout de rutas (/, /biblioteca, /chat, /materias, /settings); login con redirección; componentes base y manejo de estados/errores | Web corre contra API local; flujo de login visible; design system mínimo |

Interfaces congeladas el lunes S1: contrato de sesión/cookie, esquema de
`POST /audios` + `PUT` binario (URL de upload relativa), shape de outbox,
estados de ingesta (§M2), shape de respuesta del chat SSE.

## 2. Semana 2 (S2) — Conocimiento: corte vertical F1

Criterios de salida: E2E real navegador → proxy → servicios con clase sintética
de 1 h y el límite efectivo publicado; audio ausente antes de indexar; chat
responde con citas válidas; G1–G3 pasan; G4/G5 con avance medible.

| Persona | Responsabilidades S2 | Entregables verificables |
|---|---|---|
| **José Leonardo** | Chunking (~800/100 tokens, offsets preservados), embeddings versionados + HNSW, FTS por idioma + RRF, filtros tenant en ambas ramas; `SearchService`; pipeline de chat SSE con presupuesto de contexto; dataset de evaluación (≥50 preguntas/idioma, 15 no respondibles) | Recall@8 ≥ 0.85 en respondibles; citas ≥ 0.95 de precisión; abstención ≥ 0.90; medición p95 recuperación ≤ 500 ms |
| **Andres** | Endpoints M2 completos (capabilities, POST /audios, PUT binario, dedupe por identidad, cancelación, retry/reupload, DELETE con tombstone, transcript.txt/srt); tablas transcripts/segments/chunks/embeddings/processing_runs/outbox; estados independientes por etapa | Endpoints probados; dedupe y concurrencia verificados; commit atómico de transcript + outbox deshabilitada hasta cleanup verificado |
| **Santiago** | Pipeline de ingesta real: validación ffprobe, normalización WAV PCM 16-bit mono, fragmentación según límites medidos (no asumir 10 min), reconciliación de fronteras/overlap, cliente Riva gRPC bloqueante fuera del event loop, reintentos acotados, cleanup comprobado y reconciliador post-commit | Clase de 1 h transcrita de punta a punta sin persistir audio; `timestamp_precision` real; fallos SIGKILL/reinicio recuperados según contrato |
| **Jefferson** | Worker ARQ para `index(audio_id)` idempotente; dispatcher de outbox; reconstrucción tras perder Redis; prueba de reintento desde texto; smoke E2E automatizado; observabilidad mínima (logs JSON con request_id/audio_id/attempt_id, métricas de RAM/tmpfs/slots) | Índice se publica atómicamente y completo; crash antes/después de commit resuelto; G1/G5 con evidencia |
| **Daniel** | Formulario de upload (materia, fecha, zona horaria, idioma **sin preselección**, consentimiento); progreso SSE + polling; biblioteca + `TranscriptReader` enlazable; export TXT/SRT condicionado; UI de chat con citas clicables y estados de carga/error | Flujo completo usable en navegador; ningún reproductor/descarga de audio; estados `not_requested/processing/ready/partial/failed` visibles |

Congelaciones S2: shape final de chunks/embeddings, eventos SSE (delta, citation,
done, error), formato de citas, contrato de `/search` para Ctrl+K.

## 3. Semana 3 (S3) — Acción: extracción y Calendar (F2)

Criterios de salida: E2E con cuenta Google de prueba: propuesta → revisión →
confirmación → **un único evento**; lectura de agenda y edición confirmada;
G6/G7 aprobadas; ninguna escritura sin confirmación humana.

| Persona | Responsabilidades S3 | Entregables verificables |
|---|---|---|
| **José Leonardo** | Prompts versionados v1 en `prompts/` + tests de regresión; esquema Pydantic §M4 (tipo, fechas, spans Unicode, cita textual); reglas deterministas de validación; resolución de fechas contra fecha/zona de clase (DST, ambiguas → needs_review); evaluación G6 (≥50 actividades anotadas) | Precisión ≥ 0.95, recall ≥ 0.85, fecha exacta ≥ 0.95 en resolubles, evidencia validada al 100%; resumen/temas versionados |
| **Andres** | Tabla/estados `tasks` (pending/needs_review/confirmed/dismissed/completed + calendar_sync_status separado); endpoints M4 con control de versión (optimistic locking); evidencia reconstruida desde BD; revisión activa atómica sin sobrescribir tareas editadas | Doble análisis no duplica; edición concurrente no pierde datos; confirmación exige fecha válida + evidencia comprobada |
| **Santiago** | Backfill de análisis sobre clases F1 sin reupload; soporte de `processing_runs` por versión de transcript; eficiencia de ventanas con overlap y dedupe por evidencia; robustez cuando ASR no entregó offsets | Backfill explícito funciona; fallo de análisis no bloquea chat con índice válido (y viceversa) |
| **Jefferson** | `GoogleCalendarService` + calendario secundario «Maulwurf»; job `sync_task_event(task_id, task_version)` idempotente con ID de evento determinista; reconciliación timeout/409; ETag; lectura de ventana -7/+60 días con publicación atómica; conflictos; scopes reales verificados (D7) | Doble clic/respuesta perdida no duplica eventos; all-day vs instante correctos; rechazo de scopes no rompe biblioteca/chat; G7 |
| **Daniel** | Bandeja de revisión con cita textual + «ver segmento», edición de fecha/hora/zona, botones confirmar/descartar/completar; resumen/temas en ficha de clase; dashboard con agenda + tareas sin duplicar evento vinculado; UI de reconexión Google | Flujo de revisión completo usable; estados de sincronización visibles; ninguna escritura automática en la UI |

Congelaciones S3: contrato de `tasks` + revisiones pendientes, shape de
`calendar_events`, tools de chat de lectura (`get_calendar_events`,
`get_pending_tasks`, `propose_task`) y sus límites de iteraciones.

## 4. Semana 4 (S4) — Recordatorios, cierre y demo (F3)

Criterios de salida: envío real de prueba observado con cuenta autorizada;
dedupe/DST/entrega incierta probados; dashboard completo + Ctrl+K; runbooks y
revisión de seguridad/costos; demo final.

| Persona | Responsabilidades S4 | Entregables verificables |
|---|---|---|
| **José Leonardo** | Cierre de evaluaciones G4/G6 con números finales; revisión cruzada de seguridad (aislamiento, evidencia, tools); go/no-go de demo; retroadaptación de la documentación (spec + plan en la misma revisión) | Informe de calidad final; checklist §9 del plan de implementación marcada con evidencia |
| **Andres** | Endpoints de preferencias (`PATCH /me/notifications`), borrado de cuenta con tombstone; hardening de validaciones; soporte de `notifications` y `dedupe_key`; política D8 (retención/backups 30 días) implementada | Borrado de cuenta purga datos y fuentes derivadas; restore no reintroduce borrados; tests de aislamiento finales |
| **Santiago** | Runbook de cleanup/recuperación; hardening del supervisor (sweeper 60 s, cleanup fallido bloquea admisión); verificación final G1 bajo carga; medición de costos de ASR | Runbook publicado; G1 re-verificada con carga; tabla de costos reales por hora de clase |
| **Jefferson** | Gmail opt-in con `gmail.send` a email verificado; scheduler ARQ único (T-48/T-24/T-2 h; T-3d/T-1d all-day; dedupe `(user_id, tipo, task_id, due_version, occurrence)`); `delivery_unknown` sin reintento ciego; digests diario/semanal; deploy final + alertas/presupuesto; G8 | Envío real observado; task completada cancela recordatorios futuros; DST probado; restore de backup comprobado; alertas activas |
| **Daniel** | Dashboard final (agenda + bandeja + estadísticas por materia); Ctrl+K global; preferencias de notificaciones; PWA sin cachear datos privados; pulido de errores/vacíos; ensayo de demo | Demo completa sin puntos muertos; `no-store` en contenido privado; service worker excluye API/transcripts/SSE |

## 5. Interfaces congeladas por semana (anti-choques)

Cambiar algo de esta tabla dentro de la semana exige aprobación del líder y
actualizar los consumidores el mismo día.

| Semana | Congelado |
|---|---|
| S1 | Sesión/cookie y CSRF; `POST /audios` + `PUT` binario; outbox shape; estados de ingesta; SSE de progreso |
| S2 | Chunks/embeddings; eventos SSE del chat; formato de citas `[Materia · Clase · mm:ss]`; `/search` |
| S3 | `tasks` + revisiones; `calendar_events`; tools de chat de lectura; prompts v1 |
| S4 | Preferencias/notificaciones; dedupe de recordatorios; rutas finales de UI |

### 5a. Topología de dependencias: ¿por qué no hay ciclo?

El flujo entre semanas es **acíclico**: el proveedor de una interfaz siempre la termina a
más tardar a mitad de semana (día 3), y el consumidor construye contra el **contrato**
congelado el lunes, no contra el código terminado. La dirección es siempre:

```text
Andres (contratos de sesión/BD) ──> Jefferson (infra) ──> todos (entorno)
Andres (esquema/endpoints) ──> José (índice/chat), Daniel (UI), Jefferson (workers)
Santiago (límites/transcript) ──> Andres (capabilities), José (chunking), Daniel (estados)
José (prompts/citas) ──> Daniel (UI), Andres (tasks), Jefferson (jobs)
Jefferson (outbox/jobs) ──> José (analyze), Andres (consumo outbox)
```

Quien figura **a la izquierda** entrega primero la interfaz; quien figura a la derecha
consume el contrato congelado y avanza en paralelo. Los ciclos aparentes se resuelven
así:

| Cruce aparente | Por qué NO es dependencia circular |
|---|---|
| Andres necesita outbox (Jefferson) y Jefferson necesita tablas (Andres) | Andres define el **shape** de outbox el lunes S1 (congelado §5); Jefferson implementa el dispatcher sobre ese shape en S2. La tabla la crea Andres S1; el dispatcher Jefferson S2. Sin ciclo. |
| José necesita `tasks` (Andres S3) y Andres valida spans de José (S3) | José entrega el **esquema Pydantic y los validadores** el día 1–2 (U-S3-JL-01/02, corre sin BD); Andres construye la tabla y las reglas de estado (A3.1–A3.3) y luego ejecuta la validación de spans de José (A3.6). Primero schema, después validación cruzada. |
| Daniel necesita endpoints (Andres) y Andres prueba E2E (Daniel) | Daniel construye contra el contrato congelado con mock local (riesgo D1.5); el E2E cruzado es del **miércoles**, cuando ambos ya tienen su mitad. |
| Jefferson necesita job `analyze` (José S3) y José necesita runner (Jefferson S2) | José entrega la función del análisis; Jefferson solo la **encapsula** en el worker (J3: `analyze(audio_id)`). El runner genérico idempotente ya existe desde S2 (J2.2). |
| Santiago necesita imagen con ffmpeg (Jefferson) y Jefferson necesita datos del spike (Santiago) | Jefferson entrega imagen base el **día 2** (J1.8, sin endurecer); el spike corre; los límites medidos alimentan capabilities en S2, no en S1. |

**Regla general:** si A necesita el *código* de B en la misma semana, B entrega la
*interfaz* el lunes y el código a más tardar el día 3; si B no puede, A construye contra
el contrato congelado con mock y se cambia de mock a código real el miércoles o el jueves.
Nadie espera bloqueado a nadie más de un día.

### 5b. Qué NO es dependencia dentro de la misma semana

Cada ficha lo declara por semana; el resumen por persona:

- **Andres** no espera a Santiago en S1: el `PUT` binario es esqueleto; la recepción real la
  completa Santiago en S2 sobre el contrato S1 congelado.
- **Santiago** no espera a Andres en S2: los endpoints los implementa Andres, pero Santiago
  consume el shape congelado S1 y usa su propio prototipo F0.2 mientras tanto.
- **José** no espera a Santiago en S2: el chunking/índice se desarrolla y evalúa con
  transcripciones sintéticas/redactadas; los transcripts reales solo alimentan la medición final.
- **Daniel** no espera a Andres ni a Jefferson: mock local contra contratos congelados (D1.5).
- **Jefferson** no espera al índice de José en S2: `index(audio_id)` se prueba con un índice
  stub que registra la llamada; la lógica real la conecta José el día 4–5 (J2.2 + L2.2–L2.4).
- **Nadie** espera a Google/NVIDIA en S3–S4: los `P-` de PLAN_TESTS.md son
  provider-contract con presupuesto; el desarrollo usa mocks y el contrato congelado.

## 6. Rutinas semanales

- **Lunes (45 min, todo el equipo):** plan del sprint, congelación de interfaces, reparto de tickets.
- **Daily (15 min):** hecho/ayer, hoy, bloqueos; el líder desbloquea.
- **Miércoles (30 min):** integración cruzada — cada uno prueba el trabajo de otro en su entorno.
- **Viernes (45 min):** demo de lo terminado, revisión de la puerta de salida, retro corta, actualización de este documento si cambió el alcance.
- **Regla de PRs:** nada se mergea sin 1 review; el código que cruza fronteras de propiedad lo revisa el dueño de la carpeta.
- **Definición de hecho por ticket:** código + tests + documento/contrato actualizado + demostrable en compose local.

## 7. Gestión de riesgos

| Riesgo | Señal temprana | Mitigación |
|---|---|---|
| Riva no entrega timestamps fiables | Spike S1 con `none` | Decisión D6 **en S1**, no en S2: producto textual aceptado explícitamente o alineación efímera; la UI nunca inventa minutos |
| Límite de memoria/3 h no cabe | Benchmark S1 | Bajar límites efectivos publicados en `/ingestion/capabilities` con aprobación del líder; jamás introducir disco como workaround |
| Retención de proveedores sin garantía (D3) | Revisión contractual S1 | Bloquea clases reales; el E2E usa audio sintético generado en RAM; documentar y decidir con el usuario |
| Scope creep en F2/F3 | Tickets que cruzan carpetas | El líder rechaza PRs fuera de propiedad; nuevas features → backlog F4 |
| Google Testing caduca tokens a 7 días | Reconnect frecuente en S3 | Probar reconexión desde S3; registrar límites en docs |
| Enfermedad/baja de un miembro | — | Cada carpeta tiene un segundo lector: Andres↔Santiago (backend/ingesta), Jefferson↔Daniel (compose/web), José cubre decisiones y RAG |

## 8. Reglas no negociables (de la especificación)

1. Cero audio durable: nada en disco, S3, Redis, logs, trazas, cachés ni backups. Perder audio = reupload.
2. Idioma obligatorio elegido antes de subir; sin `multi`, sin traducción, sin fallback silencioso.
3. Escritura a Calendar solo con confirmación humana explícita; el chat nunca escribe ni envía correo.
4. Identidad siempre desde la sesión del backend, nunca del payload ni del LLM; todo filtrado por `user_id`.
5. Citas solo hacia IDs validados en BD; el LLM no genera timestamps ni fuentes.
6. `NVIDIA_API_KEY` y tokens solo en el backend; jamás en frontend, logs o commits.

## 9. Enlaces

- [ESPECIFICACION.md](ESPECIFICACION.md) — contratos M1–M9, modelo de datos, NFRs.
- [PLAN_IMPLEMENTACION.md](PLAN_IMPLEMENTACION.md) — puertas G1–G8, decisiones D1–D8, capas de prueba.
- [PLAN_TESTS.md](PLAN_TESTS.md) — catálogo de pruebas (unitarias, integración, provider, E2E, carga) por sprint y persona.
- [NVIDIA_RIVA.md](NVIDIA_RIVA.md) — parámetros de conexión auditados para el spike S1.
- [PROPUESTA_PROYECTO_FINAL.md](PROPUESTA_PROYECTO_FINAL.md) — visión y roles.
- Fichas personales: [sprints/jose-leonardo.md](sprints/jose-leonardo.md) ·
  [sprints/andres.md](sprints/andres.md) · [sprints/santiago.md](sprints/santiago.md) ·
  [sprints/jefferson.md](sprints/jefferson.md) · [sprints/daniel.md](sprints/daniel.md) ·
  [sprints/README.md](sprints/README.md) — reglas de coordinación y plantilla de checklist.

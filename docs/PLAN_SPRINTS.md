# Maulwurf — Secuencia de sprints propuesta (S1–S4)

> Estado: revisión 2026-09-19. **Modelo: backlog abierto por sprint, sin preasignación.**
> El único registro de ownership aceptado es
> [sprints/CLAIMS.md](sprints/CLAIMS.md); las bitácoras personales documentan trabajo y
> evidencia, pero no asignan tickets. Solo puede abrirse a claiming un sprint activo y las
> semanas futuras permanecen bloqueadas. Subordinado a
> [ESPECIFICACION.md](ESPECIFICACION.md) (contratos) y
> [PLAN_IMPLEMENTACION.md](PLAN_IMPLEMENTACION.md) (puertas y evidencias). La
> **especificación detallada de cómo debe funcionar cada sprint** vive en
> [plan/S1.md](plan/S1.md) · [plan/S2.md](plan/S2.md) · [plan/S3.md](plan/S3.md) ·
> [plan/S4.md](plan/S4.md) (contratos congelados, comportamiento por ticket, criterios de
> aceptación y tests). Si este documento contradice la especificación, gana la
> especificación. Nada de lo aquí descrito está implementado salvo lo marcado explícitamente
> como esqueleto existente.

## 0. Modelo de planificación

### 0.1 Cómo funciona

1. **Backlog por funcionalidad:** cada semana (§2–§5) publica tickets abiertos agrupados
   por área, con dependencias y pruebas ([PLAN_TESTS.md](PLAN_TESTS.md)). La letra del ID no
   asigna persona ni equipo.
2. **Un solo sprint abierto:** el estado de apertura vive en
   [sprints/CLAIMS.md](sprints/CLAIMS.md). Solo se reclaman tickets del sprint activo; S2–S4
   no se reservan por anticipado. El siguiente sprint se abre únicamente después de revisar
   la puerta previa, capacidad y disponibilidad del equipo.
3. **Claim aceptado por PR:** el ownership nace solo al fusionar una fila válida en
   `CLAIMS.md`, con ticket, owner único, `claimed_at` UTC, estado, elegibilidad cloud no
   sensible y revisión. Una fila personal, comentario o rama no reserva un ticket.
4. **Dependencias declaradas:** el proveedor congela el contrato el lunes y entrega una
   **primera implementación integrable** a más tardar el día 3; no se exige código final ese
   día. Consumidores avanzan contra el contrato/mock y la integración, pruebas y cierre se
   completan los días 4–5.
5. **Definition of Done por sprint:** cada semana cierra solo si cumple su DoD (funcional,
   pruebas por nivel, documentación y demo). El detalle de cada nivel está en
   [PLAN_TESTS.md](PLAN_TESTS.md).
6. **Autoridad y evidencia:** `CLAIMS.md` es canónico para ownership/estado; las bitácoras
   guardan comandos, fecha, entorno, resultados y notas. Una fila preexistente en una
   bitácora sin claim aceptado es un candidato histórico, no ownership. Un `hecho` sin
   evidencia vuelve a `en curso`.

### 0.2 Áreas y perfiles

Los identificadores de ticket conservan la letra histórica; **la letra designa el área, no
a una persona**:

| Letra | Área | Carpetas típicas | Perfil sugerido |
|---|---|---|---|
| `L-` | RAG · prompts · calidad · liderazgo | `apps/api/prompts/`, datasets, ADRs | LLM/embeddings, coordinación |
| `A-` | API · base de datos | `apps/api` (routers, services, models, core) | Python/FastAPI/SQLAlchemy |
| `S-` | Ingesta · audio · ASR | `apps/ingest`, cliente Riva, ffmpeg, tmpfs | Sistemas, audio, resiliencia |
| `J-` | Infra · workers · integraciones | `infra/`, CI, `apps/api/workers/`, Google | Docker/CI, ARQ/Redis, APIs Google |
| `D-` | Web · UX | `apps/web` | Next.js/TypeScript, UX |

Cualquier miembro puede reclamar de cualquier área. Un ticket cuyo campo Tests incluya
`P-*`, o cuya ejecución haga una llamada real a Riva, LLM/embeddings o Google, requiere que
el owner tenga elegibilidad autorizada y presupuesto aprobado. `CLAIMS.md` registra solo
`no_aplica` o `verificada:<proveedor>`, nunca cuentas, claves, tokens ni cuotas sensibles.
Cruzar carpetas exige review del owner de esa interfaz o, si no existe, de un revisor del
sprint; esa revisión no convierte al reviewer en owner.

### 0.3 Reglas anti-choques

1. **Interfaces congeladas el lunes** (§6): cambiarlas exige revisión y actualizar a los
   consumidores el mismo día. El día 3 exige una primera implementación integrable, no la
   versión final; el cierre ocurre los días 4–5.
2. **Un ticket, un owner:** contributors y reviewers se reconocen en PRs/bitácoras, pero no
   comparten ownership. La tabla vigente de `CLAIMS.md` no admite dos filas para un ticket.
3. **Concurrencia:** gana el primer PR de claim revisado y fusionado. Todo PR concurrente se
   rebasa contra la rama objetivo; si el ticket ya aparece, queda en conflicto y no concede
   ownership. No hay reservas por orden de comentario o por trabajo previo.
4. **Cambios de ownership:** transferencia y desclaim requieren PR atómico, motivo,
   evidencia del estado del trabajo e historial en `CLAIMS.md`. El owner anterior conserva
   el ticket hasta fusionar la transferencia; al fusionar un desclaim, vuelve al backlog.
5. **Split auditable:** no se reclaman IDs hijos inventados en `CLAIMS.md`. Primero se
   actualiza y fusiona este backlog con los IDs hijos y la disposición del padre; después
   cada hijo puede reclamarse en otro PR. Las ideas fuera del alcance van a F4.
6. **Estados cerrados:** el vocabulario canónico es el de
   [sprints/CLAIMS.md](sprints/CLAIMS.md) §2 — `pendiente`, `en curso`, `bloqueado`, `hecho`,
   `transferido` y `cancelado`. Reclamar, transferir y desclamar son **acciones/eventos**, no
   estados; `continuo` describe la naturaleza acotada de un ticket, nunca un estado. Un ticket
   de trabajo continuo se acota al sprint y cierra con evidencia; no permanece `en curso` al
   abrir la semana siguiente.

## 1. Resumen de sprints

| Semana | Nombre | Objetivo en una frase | Puerta de salida (resumen) |
|---|---|---|---|
| S1 | **Fundación + spike** | Monorepo corriendo, auth básica, esquema inicial y contrato real de Riva probado | F0: G1 y exactamente G2-T o G2-X; decisiones D2/D3/D4/D6 |
| S2 | **Conocimiento (F1)** | Subir audio → transcript → índice híbrido → chat con citas, corte vertical completo | E2E 1 h; G1, G2-T o G2-X, G3, G4 y G5-F1 |
| S3 | **Acción (F2)** | Extracción LLM, bandeja de revisión, Calendar idempotente, tools de lectura | G5-F2, G6 y G7-CAL: confirmación → un evento |
| S4 | **Recordatorios y cierre (F3)** | Gmail opt-in, recordatorios/digests, dashboard completo, Ctrl+K, hardening | G7-MAIL y G8; demo final + seguridad/costos revisados |

El estado inicial y toda apertura se auditan en [sprints/CLAIMS.md](sprints/CLAIMS.md): no
hay sprint activo ni tickets reclamados hasta que un PR de apertura registre capacidad y
disponibilidad. S1 se abre primero; S2–S4 permanecen bloqueados hasta cerrar la puerta previa.

Regla dura: **nada de ingesta real antes de que S1 cierre la puerta F0** (retención D3,
límites D2/D4, timestamps D6, G1 y una variante G2 aprobada).

**Estado del esqueleto (revisión 2026-09-19):** ya existen `apps/api` (FastAPI mínimo con
`/healthz`+`/readyz` y config Pydantic Settings), `apps/ingest` (paquete vacío),
`apps/web` (scaffold Next.js; landing placeholder a reemplazar por D1.8) y
`scripts/scaffold_monorepo.sh`. No existen compose, CI de calidad, tests ni migraciones.
Los tickets `L1.1`, `A1.1`, `A1.2`, `D1.1` se marcan como *verificar/completar*, no como
creación desde cero.

## 2. Semana 1 (S1) — Fundación + spike de viabilidad (F0)

**Objetivo:** monorepo corriendo, auth básica, esquema inicial y contrato real de Riva
probado. **Especificación detallada:** [plan/S1.md](plan/S1.md). Secuencia sugerida por
área: [DISENO_BD_API_SPRINTS.md](DISENO_BD_API_SPRINTS.md) §4.1.

**Interfaces congeladas el lunes:** contrato de sesión/cookie y CSRF; esquema de
`POST /audios` + `PUT` binario (URL de upload relativa); shape de outbox; estados de
ingesta (§M2); shape de respuesta SSE de progreso.

### Backlog S1 — RAG · liderazgo (`L1.x`)

| Ticket | Tarea (resumen) | Depende de | Tests |
|---|---|---|---|
| L1.1 | Verificar/ajustar estructura monorepo §3.3, versiones y lockfiles *(esqueleto ya en repo)* | — | — |
| L1.2 | ADRs cortos: estructura, versiones, política de lockfiles, formato commit/PR | L1.1 | — |
| L1.3 | Protocolo del spike: qué medir, formato del informe, criterios D2/D4/D6 | L1.1 | — |
| L1.4 | Borrador Pydantic de análisis (§M4) y de chunk (§M6) para congelación | — | U-S1-JL-01/02 |
| L1.5 | Formato del dataset G4: ≥50 preguntas/idioma, 15 no respondibles, evidencia esperada | — | U-S1-JL-03 |
| L1.6 | Congelar interfaces S1 (§6) | — | — |
| L1.7 | Coordinación, tablero y reviews de S1; cierre con decisiones y bloqueos registrados | — | — |

### Backlog S1 — API/BD (`A1.x`)

| Ticket | Tarea (resumen) | Depende de | Tests |
|---|---|---|---|
| A1.1 | Verificar scaffold `apps/api` (FastAPI + Pydantic v2 + SQLAlchemy async) *(base ya creada)* | L1.1 | — |
| A1.2 | Completar config Pydantic Settings; secretos solo por entorno *(esqueleto ya creado)* | A1.1 | — |
| A1.3 | OAuth Google backend: `state`, PKCE, `nonce`/issuer/audience; identidad por `sub` | A1.2 | I-S1-AN-06 |
| A1.4 | Sesión opaca: cookie `Secure/HttpOnly/SameSite=Lax`, hash en `sessions`, revocación; CSRF/Origin | A1.2 | U-S1-AN-01/02 |
| A1.5 | Tablas núcleo `users/sessions/google_credentials/subjects`; AES-GCM con nonce y versión de clave; FKs compuestas; RLS base | A1.1 | U-S1-AN-03, I-S1-AN-05 |
| A1.6 | CRUD materias con regla de borrado con clases activas | A1.5 | I-S1-AN-07 |
| A1.7 | Migraciones Alembic desde vacío y actualización | A1.5 | I-S1-AN-04 |
| A1.8 | Esqueleto `POST /audios` + `PUT` binario (contrato congelado); `GET/PATCH /me`, logout, `GET /integrations/status` | A1.4, L1.6 | — |
| A1.9 | Tests de aislamiento básicos: usuario B no ve recursos de A ni adivinando IDs | A1.5 | I-S1-AN-05 |

### Backlog S1 — Ingesta/ASR (`S1.x`)

| Ticket | Tarea (resumen) | Depende de | Tests |
|---|---|---|---|
| S1.A1 | Generador de audio sintético en RAM con texto esperado conocido | — | U-S1-SG-01 |
| S1.A2 | Primera llamada real a Riva (`nvidia-riva-client==2.27.0`, Bearer solo backend) | L1.3; API key autorizada | P-S1-SG-07 |
| S1.A3 | Idiomas candidatos uno por uno; sin `multi` ni traducción | S1.A2 | P-S1-SG-07 |
| S1.A4 | Formatos de entrada y sample rate efectivo (WAV PCM s16 mono candidato) | S1.A2 | U-S1-SG-02 |
| S1.A5 | Límites: payload, duración, cuotas, deadlines, cancelación (presupuesto) | S1.A2 | P-S1-SG-07/08 |
| S1.A6 | Offsets palabras/segmentos → decisión `word\|segment\|none` | S1.A2 | P-S1-SG-07, U-S1-SG-03 |
| S1.A7 | Latencia por duración/idioma, memoria SDK, costo | S1.A2 | P-S1-SG-08 |
| S1.A8 | Informe del contrato NVIDIA + revisión de términos de retención/tratamiento — cierra D3 (sin claves ni audio) | S1.A3–A7 | — |
| S1.A9 | Presentar informe; cerrar D2/D3/D4/D6 con el equipo | S1.A8 | — |
| S1.B1 | Ingesta con root read-only, tmpfs acotado, no root, sin swap/core dumps | J1.2 (imagen) | I-S1-SG-06 |
| S1.B2 | Reserva de RAM por slot (200 MiB/3 h como objetivo a validar) | S1.B1 | — |
| S1.B3 | Supervisor, lease, fencing y cleanup independiente del proceso ASR | S1.B1 | I-S1-SG-04/05 |
| S1.B4 | Matriz de fallos: 9 escenarios con inyección real (SIGKILL, reinicios, etc.) | S1.B3 | I-S1-SG-04 |
| S1.B5 | Verificación de ausencia de audio en disco/Redis/logs/trazas/cachés | S1.B3 | I-S1-SG-06 |
| S1.B6 | Lease vencido no marca cleanup verificado; outbox bloqueada sin evidencia | S1.B3 | I-S1-SG-05 |

### Backlog S1 — Infra/Integraciones (`J1.x`)

| Ticket | Tarea (resumen) | Depende de | Tests |
|---|---|---|---|
| J1.1 | `docker-compose.yml` con 8 servicios; `pgvector/pgvector:pg16` fijada; volumen nombrado para PG; secretos fuera del compose; imágenes multi-stage no-root; sin volumen de audio | L1.1 | I-S1-JF-01 |
| J1.2 | Servicio `ingest` endurecido (read-only, tmpfs, no root, límites) con ffmpeg/ffprobe | J1.1 | I-S1-SG-06 |
| J1.3 | Caddy TLS local sin buffering/caché para uploads | J1.1 | I-S1-JF-02 |
| J1.4 | CI: ruff/ESLint, mypy/tsc, pytest/Vitest, integración, builds | J1.1 | pipeline verde |
| J1.5 | Esqueleto ARQ worker+scheduler, dispatcher outbox básico, `/healthz` `/readyz` | J1.1, A1.8 | I-S1-JF-01, U-S1-JF-04 |
| J1.6 | Redis solo IDs/estado; persistencia solo Postgres | J1.1 | I-S1-JF-03 |
| J1.7 | README con comandos reproducibles (levantar, testear, lint) | J1.1 | — |
| J1.8 | Imagen base con ffmpeg temprana para el spike (`S1.A`) | J1.1 | — |

### Backlog S1 — Web/UX (`D1.x`)

| Ticket | Tarea (resumen) | Depende de | Tests |
|---|---|---|---|
| D1.1 | Verificar scaffold `apps/web` (Next.js + TS + Tailwind) *(base ya creada; landing placeholder se reemplaza en D1.8)* | L1.1 | — |
| D1.2 | Rutas base (`/biblioteca`, `/chat`, `/materias`, `/settings`) y layout de navegación | D1.1 | — |
| D1.3 | Login Google con redirección/callback; logout; estados cargando/error | A1.3/A1.4, L1.6 | — |
| D1.4 | Cliente HTTP: CSRF en mutaciones; interceptores 401/403/429 | L1.6 | U-S1-DA-01 |
| D1.5 | Componentes base: inputs fecha/zona/idioma, badges de estado, empty states, toasts | D1.1 | U-S1-DA-02, C-S1-DA-03 |
| D1.6 | CRUD materias contra la API real | A1.6 | U-S1-DA-02 |
| D1.7 | Borradores estáticos de upload y biblioteca (sin wiring) para validar UX temprano | D1.5 | — |
| D1.8 | Landing pública de producto: hero, flujo SUBE→ENCUENTRA→DECIDE, panel de privacidad, FAQ, footer; visible para visitantes en `/` (con sesión activa `/` sigue siendo dashboard, M9). **Pendiente de decisión de diseño: no hay propuesta de front vigente** | D1.5; decisión de diseño | — |

### Definition of Done (puerta de salida S1)

**Funcional (demostrable en compose):**
- `docker compose up` levanta los 8 servicios sin almacenamiento de audio.
- Login/logout/`GET /me` funcionando vía Caddy; CRUD materias; migraciones desde vacío.
- Informe del spike publicado; matriz de fallos del prototipo demostrada; D2/D3/D4/D6 cerradas.
- Landing pública estática en `/` para visitantes (D1.8), sin datos privados ni secretos.

**Pruebas en verde (ver PLAN_TESTS §1):**
- Unitarias `U-S1-*`; integración `I-S1-*` (compose, no-buffering, Redis, migraciones, aislamiento).
- Provider-contract `P-S1-SG-07/08` ejecutadas (protegida, con presupuesto) → informe.
- Smoke E2E: login desde navegador.

**Documentación:** ADRs revisados; informe del spike; README de arranque; spec/plan
actualizados si cambió un contrato.

**Claims y bitácoras:** ownership/estado cerrados en `sprints/CLAIMS.md`; cada ticket
`hecho` enlaza evidencia reproducible en PR o bitácora (comando, fecha, entorno, resultado).

## 3. Semana 2 (S2) — Conocimiento: corte vertical F1

**Objetivo:** subir audio → transcript → índice híbrido → chat con citas, completo.
**Especificación detallada:** [plan/S2.md](plan/S2.md). Secuencia sugerida por área:
DISENO_BD_API_SPRINTS.md §4.2.

**Interfaces congeladas el lunes:** shape final de chunks/embeddings y job `index(audio_id)`;
eventos SSE del chat `snapshot|delta|citation|done|error` con ID monotónico y terminal
único; formato de citas `[Materia · Clase · mm:ss]`; contrato paginado de `/search`.

### Backlog S2 — RAG (`L2.x`)

| Ticket | Tarea (resumen) | Depende de | Tests |
|---|---|---|---|
| L2.1 | Chunking ~800/100 tokens, cortes en frase/segmento, offsets Unicode, `chunk_segments` | L1.4; tablas A2.1 (contrato) | U-S2-JL-01 |
| L2.2 | Embeddings versionados + HNSW coseno; generación de índice con activación atómica | A2.1 | U-S2-JL-02, P-S2-JL-10 |
| L2.3 | FTS por idioma + pgvector + fusión RRF; filtros tenant en ambas ramas antes de top-k | A2.1 | U-S2-JL-03/04 |
| L2.4 | `SearchService` + `GET /search` (en pareja con A2.11) | L2.3 | I-S2-JL-06 |
| L2.5 | Chat SSE: embedding → 30/rama → RRF → ~8 chunks bajo presupuesto; streaming | L2.4 | I-S2-JL-07/08 |
| L2.6 | Citas solo a IDs validados en BD; «segmento N» sin offsets; `message_sources` completo | L2.5 | U-S2-JL-05 |
| L2.7 | Evaluación G4 sobre dataset S1 (Recall@8 con filtros activos ≥ 0.85, citas ≥ 0.95, abstención ≥ 0.90) | L2.1–L2.6 | G4 |
| L2.8 | p95 de recuperación ≤ 500 ms y primer token de chat p95 ≤ 5 s medidos | L2.4 | G4 |

### Backlog S2 — API/BD (`A2.x`)

| Ticket | Tarea (resumen) | Depende de | Tests |
|---|---|---|---|
| A2.1 | Migración S2 completa: ingesta, transcript/segmentos, chunks/embeddings, outbox, chat (FKs tenant-aware) | A1.5 | I-S2-AN-05, I-S2-AN-11 |
| A2.2 | `GET /ingestion/capabilities` con límites del spike (no constantes duras) | S1.A8 | U-S2-SG-01 |
| A2.3 | `POST /audios`: consentimiento versionado, reserva de slot, URL relativa; sin dedupe aquí | A2.1, L1.6 | U-S2-AN-01 |
| A2.4 | `PUT` binario autenticado; SHA-256 y dedupe tras cleanup; corte por límite | A2.3 | I-S2-AN-05 |
| A2.5 | Dedupe por identidad en PUT; token de variante opaco de un uso; nunca retener audio | A2.3 | U-S2-AN-02, I-S2-AN-05 |
| A2.6 | Estados por etapa independientes + agregado (`processing/ready/partial/failed`) | A2.1 | U-S2-AN-03 |
| A2.7 | Cancelación, tombstone y fencing; `DELETE /audios/{id}` con purga de derivados | A2.6 | U-S2-AN-04, I-S2-AN-06 |
| A2.8 | `retry` (solo análisis/índice) vs `reupload` (sin transcript utilizable) | A2.6 | I-S2-AN-08 |
| A2.9 | Listado, `GET /audios/{id}`, SSE progreso desde BD, exports `txt`/`srt` condicionado | A2.6 | I-S2-AN-07/09 |
| A2.10 | Commit atómico + estado `cleanup_pending`; gate de outbox solo para index/analyze | S2.5 | I-S2-AN-10 |
| A2.11 | `GET /search` (en pareja con L2.4) | L2.3 | I-S2-JL-06 |
| A2.12 | Tablas `conversations`/`messages`/`message_sources` + endpoints M7: CRUD de conversaciones (modos all/subject/class), historial, `client_message_id` idempotente, cancelación de streaming | A2.1, L2.5 (contrato) | I-S2-AN-11 |

### Backlog S2 — Ingesta/ASR (`S2.x`)

| Ticket | Tarea (resumen) | Depende de | Tests |
|---|---|---|---|
| S2.1 | Recepción streaming a tmpfs, SHA-256, validación ffprobe completa | A2.2/A2.3; S1.A8 | U-S2-SG-02 |
| S2.2 | ffmpeg sin red ni shell, con límites; normalización WAV PCM; fragmentación por límites reales | S2.1 | U-S2-SG-03 |
| S2.3 | Procesamiento secuencial con backpressure; reconciliación de fronteras/overlap | S2.2 | U-S2-SG-04 |
| S2.4 | Cliente Riva bloqueante fuera del event loop; reintentos clasificados | S2.2 | U-S2-SG-05, P-S2-SG-12 |
| S2.5 | Commit atómico transcript+segmentos (contrato compartido con A2.10) | S2.3/S2.4 | I-S2-SG-06 |
| S2.6 | Cleanup en `finally`; `cleanup_status` separado; outbox solo tras commit+cleanup | S2.5 | I-S2-SG-07 |
| S2.7 | Reconciliador post-commit (sin retranscribir); fencing token | S2.6 | I-S2-SG-08/09 |
| S2.8 | Cancelación: desconexión/lease/pestaña/post-commit | A2.7 | I-S2-SG-10 |
| S2.9 | E2E clase sintética de 1 h con `word\|segment` (G2-T) o `none` con UI textual (G2-X) | S2.1–S2.8; D2.2/D2.3 | E-S2-SG-11 |

### Backlog S2 — Infra/Integraciones (`J2.x`)

| Ticket | Tarea (resumen) | Depende de | Tests |
|---|---|---|---|
| J2.1 | Dispatcher outbox real: gate de cleanup solo para index/analyze; otros tipos habilitados | A2.10; U-S1-JF-04 | U-S2-JF-01 |
| J2.2 | Job `index(audio_id)` idempotente; reintentos backoff+jitter | J2.1 | I-S2-JF-02 |
| J2.3 | Reconstrucción tras perder Redis (regeneración desde BD/outbox) | J2.1 | I-S2-JF-03 |
| J2.4 | Reconciliador: intentos muertos, outbox atascada, duplicados por lease | J2.2 | I-S2-JF-04 |
| J2.5 | Observabilidad: logs JSON con IDs, métricas RAM/tmpfs/slots, alerta cleanup | J2.1 | C-S2-JF-10 |
| J2.6 | Smoke E2E automatizado en pipeline | J2.2; S2.9; D2.2; D2.6 | E-S2-JF-06 |
| J2.7 | Fault injection con los tickets de commit (S2.5/A2.10) | S2.5, A2.10 | I-S2-JF-05 |

### Backlog S2 — Web/UX (`D2.x`)

| Ticket | Tarea (resumen) | Depende de | Tests |
|---|---|---|---|
| D2.1 | Formulario upload M2: idioma sin preselección, consentimiento visible | A2.3, L1.6 | U-S2-DA-01 |
| D2.2 | Upload `PUT` streaming con progreso real; cancelar subida cancela intento | A2.4 | U-S2-DA-02 |
| D2.3 | Progreso SSE + fallback polling; bytes reales, cero porcentajes inventados | A2.9 | U-S2-DA-03 |
| D2.4 | Biblioteca con estados por etapa honestos; «volver a subir» ≠ «reintentar» | A2.9 | E-S2-DA-06 |
| D2.5 | `TranscriptReader` con `?segment=`, export condicionado, resumen vacío si `not_requested` | A2.9 | C-S2-DA-04 |
| D2.6 | Chat UI: historial de conversaciones, SSE con `fetch`, eventos `delta/citation/done/error`, cancelación | L2.5, A2.12 | C-S2-DA-05 |
| D2.7 | Citas clicables → lector con segmento resaltado; Markdown sanitizado | L2.6, D2.5 | C-S2-DA-05, E-S2-JL-09 |
| D2.8 | Privacidad de navegador: `no-store`, liberar `File`/object URLs, sin caché privada | continuo | C-S2-DA-07 |

### Definition of Done (puerta de salida S2)

**Funcional:** E2E real navegador → proxy → servicios con clase sintética de 1 h y límite
efectivo publicado; audio ausente antes de indexar; chat responde con citas válidas y con
historial persistente (conversaciones/mensajes, `client_message_id` idempotente).

**Pruebas en verde (PLAN_TESTS §2):** unitarias `U-S2-*`; integración `I-S2-*` (dedupe
concurrente, cleanup, fencing, reconciliador); smoke E2E `E-S2-JF-06` en cada MR;
`E-S2-SG-11` con clase de 1 h. G1, G2-T o G2-X y G3 con evidencia; G4 y G5-F1 completas
(Recall@8, citas, abstención por idioma con numerador/denominador).

**Documentación:** contratos S2 congelados sin cambios; decisión `timestamp_precision`
documentada; D3-Texto cerrada para el proveedor de embeddings/LLM antes de procesar texto no
sintético; spec/plan actualizados si cambió un contrato; bitácoras con evidencia.

## 4. Semana 3 (S3) — Acción: extracción y Calendar (F2)

**Objetivo:** propuesta → revisión → confirmación → **un único evento**; ninguna escritura
sin confirmación humana. **Especificación detallada:** [plan/S3.md](plan/S3.md).
Secuencia sugerida por área: DISENO_BD_API_SPRINTS.md §4.3.

**Interfaces congeladas el lunes:** contrato de `tasks` + revisiones; shape de
`calendar_events`; tools de chat de lectura (`get_calendar_events`, `get_pending_tasks`,
`propose_task`) y límites de iteraciones; prompts v1.

### Backlog S3 — RAG (`L3.x`)

| Ticket | Tarea (resumen) | Depende de | Tests |
|---|---|---|---|
| L3.1 | `apps/api/prompts/v1/` versionada + tests de regresión en CI | L1.4 | R-S3-JL-06 |
| L3.2 | Esquema Pydantic final §M4 (spans Unicode, cita textual, `date_status`) | L1.4 | U-S3-JL-01 |
| L3.3 | Validación determinista post-LLM: pertenencia, evidencia literal contra BD | L3.2 | U-S3-JL-01/02 |
| L3.4 | Resolución de fechas contra zona de la clase (DST, relativas, ambiguo → `needs_review`) | L3.2 | U-S3-JL-03 |
| L3.5 | Ventanas con overlap + dedupe por evidencia; idempotencia con `processing_runs` | S3.4 | U-S3-JL-04 |
| L3.6 | `analyze(audio_id)` + orquestador backend de tools de lectura; reanálisis con revisión | J2.2, A3.1 | U-S3-JL-05, I-S3-AN-04 |
| L3.7 | Evaluación G6 (≥50 actividades/idioma; precisión ≥ 0.95, recall ≥ 0.85) | L3.1–L3.5 | P-S3-JL-07 |
| L3.8 | Resumen (3–5 frases) y temas versionados | L3.1 | R-S3-JL-06 |

### Backlog S3 — API/BD (`A3.x`)

| Ticket | Tarea (resumen) | Depende de | Tests |
|---|---|---|---|
| A3.1 | Tabla `tasks` completa: CHECKs excluyentes, `date_status`, versión, `calendar_sync_status` separado | A2.1; L3.2 (contrato) | U-S3-AN-01 |
| A3.2 | Evidencia + `task_sources` (manual sin fuentes; corpus con fuentes validadas) | A3.1 | I-S3-AN-05 |
| A3.3 | Endpoints M4 con optimistic locking (`confirm/dismiss/complete`, POST manual/chat) | A3.1 | U-S3-AN-02 |
| A3.4 | Confirmación atómica: tarea + outbox en una transacción; 202 con `pending` | A3.3, J1.5 | I-S3-AN-03 |
| A3.5 | Reanálisis crea revisión nueva; no toca tareas editadas/confirmadas | A3.1 | I-S3-AN-04 |
| A3.6 | Reconstrucción de evidencia desde BD para validar spans de L3.3 | L3.3 | I-S3-AN-05 |
| A3.7 | Reglas de estado: confirmar exige fecha, bloque Calendar y evidencia; `blocked→pending` solo en reconnect | A3.3 | I-S3-AN-03 |
| A3.8 | `settings` Calendar + espejo `calendar_events` (calendarios de escritura/lectura) | A3.1 | I-S3-JF-05 |
| A3.9 | Tests de idempotencia: doble análisis/confirmación no duplica | A3.4 | I-S3-AN-06 |

### Backlog S3 — Ingesta/ASR (`S3.x`)

| Ticket | Tarea (resumen) | Depende de | Tests |
|---|---|---|---|
| S3.1 | Backfill de análisis sobre clases F1: solo desde texto persistido | L3.6 (contrato día 1), A3.1 | I-S3-SG-01 |
| S3.2 | Ventanas eficientes con coste de tokens medido | S3.1 | U-S3-SG-02 |
| S3.3 | Robustez sin offsets del ASR: evidencia textual, cero minutos inventados | S3.1 | U-S3-SG-03 |
| S3.4 | `processing_runs` por versión de transcript/config | S3.1 | I-S3-SG-04 |
| S3.5 | Medición de costos ASR y RAM/tmpfs bajo carga | S3.2 | base C-S4-SG-04 |
| S3.6 | Hardening: backoff+jitter (máx 3 transitorios); advertencias de calidad ASR | S3.1 | U-S3-SG-05 |

### Backlog S3 — Infra/Integraciones (`J3.x`)

| Ticket | Tarea (resumen) | Depende de | Tests |
|---|---|---|---|
| J3.1 | Verificar scopes reales (D7) con cuenta protegida; registrar hallazgos | credenciales autorizadas | P-S3-JF-01 |
| J3.2 | `GoogleCalendarService`: bloqueante aislado; `Retry-After`; `invalid_grant` desconecta | J3.1 | P-S3-JF-02 |
| J3.3 | Endpoints `POST /integrations/google/connect\|reconnect` (OAuth incremental) + calendario secundario único «Maulwurf»; guardar ID; reconciliar creación ambigua | J3.2, A3.8 | I-S3-JF-06 |
| J3.4 | Job `sync_task_event(task_id, version)` con ID determinista; timeout→consultar; 409→reconciliar | A3.4 (outbox) | I-S3-JF-03, E-S3-DA-05 |
| J3.5 | Evento `[Maulwurf]` con link autenticado al segmento; all-day vs instante correctos | J3.4 | I-S3-JF-03 |
| J3.6 | Patch con ETag; no sobrescribir cambios externos; completar cancela recordatorios | J3.4 | I-S3-JF-04 |
| J3.7 | Lectura -7/+60 días con paginación; publicación atómica en `calendar_events`; `GET /calendar/events` | A3.8 | I-S3-JF-05 |
| J3.8 | Conflictos (solape ≠ concentración); `GET /calendar/conflicts`, `POST /calendar/sync` | J3.7 | I-S3-JF-07 |
| J3.9 | Cuenta de prueba protegida; recursos de prueba con limpieza (acotado al sprint) | D7-CAL | C-S3-JF-08 |

### Backlog S3 — Web/UX (`D3.x`)

| Ticket | Tarea (resumen) | Depende de | Tests |
|---|---|---|---|
| D3.1 | Bandeja «para confirmar»: estados, evidencia con cita, badges `date_status` | A3.3; L3.2 (schema) | C-S3-DA-01 |
| D3.2 | Edición con bloques separados vencimiento/agenda (duración por defecto editable) | A3.3 | C-S3-DA-02 |
| D3.3 | Confirmar/descartar/completar con versión; 409 → refrescar; preview de escritura | A3.3/A3.4 | C-S3-DA-03 |
| D3.4 | «Ver segmento de transcripción» desde la tarea | D2.5 | C-S2-DA-04, E-S2-JL-09 |
| D3.5 | Ficha de clase: resumen, temas, items, estados de análisis/índice | D2.5, L3.8 | C-S3-DA-06 |
| D3.6 | Dashboard con agenda sin duplicar tarea/evento; frescura de sync visible | J3.7, A3.8 | C-S3-DA-07 |
| D3.7 | Estados de sync por tarea + UI de reconexión Google | J3.4 | C-S3-DA-08 |
| D3.8 | Tools de chat solo lectura; `propose_task` crea propuesta, nunca escribe agenda | L3.6 (tools) | C-S3-DA-04 |

### Definition of Done (puerta de salida S3)

**Funcional:** E2E con cuenta Google de prueba: propuesta → revisión → confirmación →
un único evento; lectura de agenda y edición confirmada; rechazo de scopes no rompe
biblioteca/chat.

**Pruebas en verde (PLAN_TESTS §3):** unitarias `U-S3-*`; regresión `R-S3-JL-06` en CI;
integración `I-S3-*` (idempotencia, ETag, revisión, reconnect); provider `P-S3-JF-01/02` y
`P-S3-JL-07` (protegidas, presupuesto). G5-F2, G6 y G7-CAL con evidencia.

**Documentación:** prompts v1 registrados con `prompt_version`; hallazgos D7 documentados;
spec/plan actualizados si cambió un contrato; bitácoras con evidencia.

## 5. Semana 4 (S4) — Recordatorios, cierre y demo (F3)

**Objetivo:** recordatorios confiables, dashboard completo, hardening y demo final.
**Especificación detallada:** [plan/S4.md](plan/S4.md). Secuencia sugerida por área:
DISENO_BD_API_SPRINTS.md §4.4.

**Interfaces congeladas el lunes:** preferencias/notificaciones; dedupe de recordatorios;
rutas finales de UI.

### Backlog S4 — API/BD (`A4.x`)

| Ticket | Tarea (resumen) | Depende de | Tests |
|---|---|---|---|
| A4.1 | `notifications` + extensión Gmail/digests de `settings` (base Calendar ya existe desde S3) | A3.8 | U-S4-AN-01/02 |
| A4.2 | `PATCH /me/notifications` + `POST /notifications/test` | A4.1 | I-S4-JF-05 |
| A4.3 | Cambiar vencimiento/preferencias cancela solo ocurrencias futuras obsoletas | A4.1 | U-S4-AN-03 |
| A4.4 | Borrado de cuenta con operación observable: `DELETE /me` + `GET /me/deletion/{id}` | A3.8, J3.3 | I-S4-AN-04 |
| A4.5 | D8 ya aprobada: validación de backups y restore con tombstones reaplicados | A4.4, D8 aprobada | I-S4-AN-05 |
| A4.6 | Hardening: ownership en API/SSE/tools/jobs; `no-store`; rate limits `429/503` | A4.4, J4.2 | I-S4-AN-06 |
| A4.7 | Tests G3 finales + sweep de huérfanos (chunks/embeddings sin clase) | A4.4 | I-S4-AN-07 |
| A4.8 | Soporte en ensayo de demo | — | — (soporte: se evidencia con E-S4-DA-04) |

### Backlog S4 — Infra/Integraciones (`J4.x`)

| Ticket | Tarea (resumen) | Depende de | Tests |
|---|---|---|---|
| J4.1 | Gmail opt-in `gmail.send`; plantillas HTML+texto escapadas; destinatario verificado | D7-MAIL; credenciales | U-S4-JF-03, P-S4-JF-07 |
| J4.2 | Scheduler ARQ con rol único; reclamación atómica; SLA 15 min | A4.1 | I-S4-JF-04 |
| J4.3 | Recordatorios escalados T-48/T-24/T-2 h; T-3d/T-1d all-day hora local | J4.2 | U-S4-JF-01 |
| J4.4 | Dedupe persistente + DST (hora inexistente/repetida) | J4.3 | U-S4-JF-02 |
| J4.5 | `delivery_unknown` sin reintento ciego; reexpedición manual advierte duplicado | J4.2 | I-S4-JF-05 |
| J4.6 | Digests diario 07:00 y semanal por fecha local, con conflictos | J4.2 | I-S4-JF-06 |
| J4.7 | Despliegue de referencia; imágenes versionadas; migración como job único; rollback | J1.1 | L-S4-JF-08 |
| J4.8 | Alertas/presupuesto + runbooks (cleanup, proveedor caído, tokens, restore) | J4.7, S4.1, A4.5 | C-S4-JF-10 |
| J4.9 | Cierre G8: builds/tests de las 3 apps, restore, recordatorios en SLA | todo | E-S4-JF-09 |
| J4.10 | Envío real de Gmail observado con limpieza posterior | J4.1; cuenta autorizada | P-S4-JF-07 |

### Backlog S4 — Ingesta/ASR (`S4.x`)

| Ticket | Tarea (resumen) | Depende de | Tests |
|---|---|---|---|
| S4.1 | Runbook de cleanup/recuperación ejecutable tal cual | S2.6, S2.7 | I-S4-SG-03 |
| S4.2 | Hardening supervisor: sweeper 60 s; cleanup fallido bloquea admisión | S2.7 | L-S4-SG-01 |
| S4.3 | Re-verificación G1 bajo carga con fallos inyectados | S2.x | L-S4-SG-01 |
| S4.4 | TTLs efectivos (10/30/60 min) publicados con benchmark | S2.1 | L-S4-SG-02 |
| S4.5 | Tabla de costos reales por hora de clase | S3.5 | C-S4-SG-04 |
| S4.6 | Soporte en ensayo de demo | — | — (soporte: se evidencia con E-S4-DA-04) |

### Backlog S4 — RAG (`L4.x`)

| Ticket | Tarea (resumen) | Depende de | Tests |
|---|---|---|---|
| L4.1 | Cierre G4/G6 con números finales y slices difíciles | L2.7, L3.7 | R-S4-JL-01 |
| L4.2 | Revisión cruzada de seguridad (aislamiento, evidencia, tools, secretos) | — | C-S4-JL-02 |
| L4.3 | Checklist §9 de PLAN_IMPLEMENTACION con evidencia ítem por ítem | — | C-S4-JL-03 |
| L4.4 | Spec + plan + diseño actualizados en la misma revisión si un contrato cambió | — | C-S4-JL-03 |
| L4.5 | Go/no-go de demo y guion completo | — | E-S4-DA-04 |
| L4.6 | Retro final + backlog F4 priorizado | — | C-S4-JL-04 |

### Backlog S4 — Web/UX (`D4.x`)

| Ticket | Tarea (resumen) | Depende de | Tests |
|---|---|---|---|
| D4.1 | Preferencias de notificaciones en `/settings` | A4.2 | C-S4-DA-01 |
| D4.2 | Panel de entregas: `delivery_unknown` explicado; reexpedición advierte | J4.5 | C-S4-DA-02 |
| D4.3 | Ctrl+K global contra `GET /search` | L2.4 | C-S4-DA-03 |
| D4.4 | Estadísticas por materia sin exponer audio | A2.9 | C-S4-DA-06 |
| D4.5 | PWA opcional: service worker que excluye upload/API/transcripts/chat/SSE | D4.6 | C-S4-DA-05 |
| D4.6 | Pulido: empty states, errores accionables, accesibilidad básica | D4.1, D4.2, D4.3 | C-S4-DA-05 |
| D4.7 | Ensayo de demo ×2 (jueves y viernes) | L4.5 | E-S4-DA-04 |
| D4.8 | Checklist G1 en cliente final (sin caché de privados) | D2.8 | C-S4-DA-05 |

### Definition of Done (puerta de salida S4)

**Funcional:** envío real de prueba observado con cuenta autorizada; dedupe/DST/entrega
incierta probados; dashboard completo + Ctrl+K; demo final corriendo en el despliegue de
referencia.

**Pruebas en verde (PLAN_TESTS §4):** suite completa de las 3 apps; carga `L-S4-*`
(G1 bajo carga, TTLs); provider `P-S4-JF-07` (envío real controlado); G8 cerrada con
evidencia.

**Documentación:** runbooks publicados; tabla de costos; informe de calidad final;
checklist §9 marcada; spec/plan consistentes; bitácoras cerradas con evidencia.

## 6. Interfaces congeladas por semana

Cambiar algo de esta tabla dentro de la semana exige aprobación del líder y actualizar
los consumidores el mismo día.

| Semana | Congelado |
|---|---|
| S1 | Sesión/cookie y CSRF; `POST /audios` + `PUT` binario; outbox shape; estados de ingesta; SSE de progreso |
| S2 | Chunks/embeddings; eventos SSE del chat; formato de citas `[Materia · Clase · mm:ss]`; `/search` |
| S3 | `tasks` + revisiones; `calendar_events`; tools de chat de lectura; prompts v1 |
| S4 | Preferencias/notificaciones; dedupe de recordatorios; rutas finales de UI |

### 6a. Topología de dependencias (por área)

El flujo es acíclico: el proveedor de una interfaz la congela el lunes y entrega código a
más tardar el día 3; el consumidor construye contra el contrato congelado.

```text
API/BD (sesión, esquema, endpoints) ──> Infra (compose, workers) ──> todos (entorno)
API/BD (esquema/endpoints) ──> RAG (índice/chat), Web (UI), Infra (workers)
Ingesta (límites/transcript) ──> API/BD (capabilities), RAG (chunking), Web (estados)
RAG (prompts/citas/tools) ──> Web (UI), API/BD (tasks), Infra (jobs)
Infra (outbox/jobs/runner) ──> RAG (analyze), API/BD (consumo outbox), Web (estados sync)
```

Cruces aparentes resueltos (el patrón: primero el **contrato** congelado, después el código):

| Cruce aparente | Por qué NO es ciclo |
|---|---|
| API/BD necesita outbox (Infra) e Infra necesita tablas (API/BD) | API/BD define el shape de outbox el lunes S1; la tabla se crea en S1; el dispatcher lo implementa Infra en S2. |
| RAG necesita `tasks` (API/BD) y API/BD valida spans de RAG | RAG entrega el esquema Pydantic + validadores el día 1–2 (corre sin BD); API/BD construye la tabla y luego ejecuta esa validación (A3.6). |
| Web necesita endpoints (API/BD) y API/BD prueba E2E (Web) | Web construye contra el contrato congelado con mock local; el E2E cruzado es del miércoles. |
| Infra necesita job `analyze` (RAG) y RAG necesita runner (Infra) | RAG entrega la función; Infra solo la encapsula en el worker sobre el runner genérico idempotente de S2. |
| Ingesta necesita imagen con ffmpeg (Infra) e Infra necesita datos del spike (Ingesta) | Infra entrega imagen base el día 2; los límites medidos alimentan capabilities en S2, no en S1. |

### 6b. Qué NO es dependencia dentro de la misma semana

- **API/BD** no espera a Ingesta en S1: el `PUT` binario es esqueleto; la recepción real
  llega en S2 sobre el contrato congelado.
- **Ingesta** no espera a API/BD en S2: consume el shape congelado y usa su prototipo F0.2.
- **RAG** no espera a Ingesta en S2: chunking/índice se desarrollan con transcripciones
  sintéticas/redactadas; los transcripts reales solo alimentan la medición final.
- **Web** no espera a nadie: mock local contra contratos congelados.
- **Infra** no espera al índice de RAG en S2: `index(audio_id)` se prueba con un stub que
  registra la llamada; la lógica real se conecta el día 4–5.
- **Nadie** espera a Google/NVIDIA: los tickets con proveedor pagado (`P-`) usan mocks para
  desarrollar y contrato congelado para conmutar.

## 7. Rutinas semanales

- **Lunes (45 min, todo el equipo):** plan del sprint, congelación de interfaces y
  **reclamo de tickets** mediante PR en `sprints/CLAIMS.md` (la bitácora solo recoge la
  evidencia posterior).
- **Daily (15 min):** hecho/ayer, hoy, bloqueos; el líder desbloquea.
- **Miércoles (30 min):** integración cruzada — cada uno prueba el trabajo de otro en su
  entorno; conmutación de mocks a código real.
- **Viernes (45 min):** demo, verificación del DoD del sprint, retro y cierre de bitácoras.
- **Regla anti-bloqueo:** quien espera código de otro sigue sobre el mock del contrato
  congelado y lo reporta en la daily; el líder decide entre aceptar el mock para la demo o
  reasignar tickets.
- **Definition of done por ticket:** código + tests + documento/contrato actualizado +
  demostrable en compose local + evidencia en la bitácora.

## 8. Gestión de riesgos

| Riesgo | Señal temprana | Mitigación |
|---|---|---|
| Riva no entrega timestamps fiables | Spike S1 con `none` | Decidir D6 **en S1**: producto textual aceptado o alineación efímera; la UI nunca inventa minutos |
| Límite de memoria/3 h no cabe | Benchmark S1 | Bajar límites efectivos en capabilities con aprobación del líder; jamás introducir disco |
| Retención de proveedores sin garantía (D3) | Revisión contractual S1 | Bloquea clases reales; E2E con audio sintético en RAM; decidir con el usuario |
| Ticket sin responsable al cierre del lunes | Backlog con huecos | El líder reasigna, divide o baja el alcance del DoD de forma explícita y visible |
| Sobrecarga de una persona | Tickets sin avanzar en daily | Redistribución el día 2–3; un ticket con prueba `P-*` requiere elegibilidad verificada en `CLAIMS.md` |
| Scope creep en F2/F3 | Tickets que cruzan carpetas | El líder rechaza PRs fuera del claiming; nuevas features → backlog F4 |
| Landing sin diseño vigente | D1.8 sin dirección visual | Decidir la dirección mínima (tokens + hero) antes del claiming; la propuesta anterior de front quedó descartada y no obliga |
| Google Testing caduca tokens a 7 días | Reconnect frecuente en S3 | Probar reconexión desde S3; registrar límites en docs |
| Baja de un miembro | — | Las áreas tienen pares naturales (API/BD↔Ingesta, Infra↔Web, RAG cubre decisiones); el líder redistribuye el backlog |

## 9. Reglas no negociables (de la especificación)

1. Cero audio durable: nada en disco, S3, Redis, logs, trazas, cachés ni backups. Perder
   audio = reupload.
2. Idioma obligatorio elegido antes de subir; sin `multi`, sin traducción, sin fallback
   silencioso.
3. Escritura a Calendar solo con confirmación humana explícita; el chat nunca escribe ni
   envía correo.
4. Identidad siempre desde la sesión del backend, nunca del payload ni del LLM; todo
   filtrado por `user_id`.
5. Citas solo hacia IDs validados en BD; el LLM no genera timestamps ni fuentes.
6. `NVIDIA_API_KEY` y tokens solo en el backend; jamás en frontend, logs o commits.

## 10. Enlaces

- [ESPECIFICACION.md](ESPECIFICACION.md) — contratos M1–M9, modelo de datos, NFRs.
- [PLAN_IMPLEMENTACION.md](PLAN_IMPLEMENTACION.md) — puertas G1–G8, decisiones D1–D8, capas de prueba.
- [PLAN_TESTS.md](PLAN_TESTS.md) — catálogo de pruebas (unitarias, integración, provider, E2E, carga) por sprint.
- [DISENO_BD_API_SPRINTS.md](DISENO_BD_API_SPRINTS.md) — diseño de BD/API y secuencia sugerida por área.
- [plan/](plan/S1.md) — especificación detallada por sprint: [S1](plan/S1.md) ·
  [S2](plan/S2.md) · [S3](plan/S3.md) · [S4](plan/S4.md).
- [NVIDIA_RIVA.md](NVIDIA_RIVA.md) — parámetros de conexión auditados para el spike S1.
- [PROPUESTA_PROYECTO_FINAL.md](PROPUESTA_PROYECTO_FINAL.md) — visión y roles.
- Bitácoras: [sprints/README.md](sprints/README.md) — reglas de claiming y plantilla ·
  [sprints/jose-leonardo.md](sprints/jose-leonardo.md) · [sprints/andres.md](sprints/andres.md) ·
  [sprints/santiago.md](sprints/santiago.md) · [sprints/jefferson.md](sprints/jefferson.md) ·
  [sprints/daniel.md](sprints/daniel.md).

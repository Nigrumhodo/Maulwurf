# Maulwurf — Catálogo de pruebas por sprint

> Estado: plan operativo pendiente de ejecución, revisión 2026-09-19. Subordinado a
> [ESPECIFICACION.md](ESPECIFICACION.md) (contratos M1–M9, §6 modelo de datos) y
> [PLAN_IMPLEMENTACION.md](PLAN_IMPLEMENTACION.md) (§7 capas de prueba y variantes de puertas
> G1–G8).
> Cada ticket del backlog de [PLAN_SPRINTS.md](PLAN_SPRINTS.md) referencia aquí los tests que lo
> evidencian; quien reclama un ticket registra su ejecución en su bitácora de
> [sprints/](sprints/README.md). **Ningún test existe aún:** este documento define qué debe
> existir y cuándo.

---

## 0. Convenciones

### 0.1 Capas (de PLAN_IMPLEMENTACION §7)

| Capa | Código | Qué valida | Dónde corre | Herramienta |
|---|---|---|---|---|
| Unitaria | `U-` | Lógica pura: fechas/DST, offsets Unicode, chunking, RRF, estados, dedupe | CI (cada MR) | pytest / Vitest |
| Integración local | `I-` | Servicios reales de compose: Postgres/pgvector, Redis, ffmpeg, outbox, leases, cleanup, reinicios | CI (MR) + local | pytest contra compose |
| Provider-contract | `P-` | Contratos reales de NVIDIA Riva, LLM/embeddings, Google | **Protegida**: manual o programada, nunca en MR | scripts dedicados |
| E2E | `E-` | Navegador → proxy → servicios → BD/proveedor | CI (smoke) + demo manual | Vitest/Playwright + manual |
| Carga | `L-` | Memoria, tmpfs, latencia, concurrencia, ausencia de persistencia de audio | Local/protegida, antes de cierres | scripts dedicados |
| Componente/manual | `C-` | Componentes aislados (Vitest) o checklist manual anotada (demo, seguridad, privacidad de cliente) | CI (componentes) + manual | Vitest + checklist |
| Regresión de prompts | `R-` | Validadores/agregación contra outputs de LLM grabados (sin claves ni PII); no gasta cuota | CI (cada MR) | pytest |

### 0.2 Identificadores

`<capa>-<sprint>-<sufijo legado>-<n>`, p. ej. `I-S2-AN-05` = integración, sprint 2,
sufijo estable `AN`, caso 03. Los sufijos **JL/AN/SG/JF/DA** forman parte de IDs legados ya
referenciados: **no expresan ownership, asignación ni responsabilidad actual y no se renombran**.
La responsabilidad procede únicamente del claiming del ticket, registrado en
[sprints/CLAIMS.md](sprints/CLAIMS.md); la bitácora solo documenta la evidencia posterior.

### 0.3 Ubicación (según §3.3 de la especificación)

- Python: `apps/api/tests/unit/`, `apps/api/tests/integration/`, `apps/ingest/tests/…`,
  `workers/tests/…` — pytest, marcadores `@pytest.mark.integration` / `provider` / `load`.
- Web: `apps/web/tests/unit/`, `apps/web/tests/e2e/` — Vitest (+ Playwright cuando exista E2E).
- Scripts de carga y provider: `scripts/load/`, `scripts/provider/` (sin secretos incrustados).

### 0.4 Reglas de fixtures (no negociables, de AGENTS.md y spec)

1. **Audio sintético solo en RAM**, generado en el momento de la ejecución; nunca versionado
   ni retenido como artefacto de CI. Solo se persisten métricas no sensibles y texto esperado.
2. Fixtures de texto: **transcripciones sintéticas o redactadas** (sin voz real de nadie, sin PII).
3. Prohibido: API keys, tokens, payloads crudos de proveedores, dumps de BD, audio, en fixtures,
   snapshots, logs de test o grabaciones de CI.
4. Provider-contract usa **cuentas protegidas autorizadas + presupuesto documentado**; crea solo
   recursos de prueba y los limpia. Un mock **no es evidencia** de un contrato de proveedor.
5. Tests de fechas/DST usan **relojes inyectables**, nunca la hora del sistema.
6. Regla de decisión de suite: si un test necesita red, es `I-` o `P-`; si necesita un proveedor
   pagado, es `P-`; si necesita navegador, es `E-`; lo demás es `U-`.

### 0.5 Nomenclatura

`test_<módulo>_<comportamiento>.py` / `<módulo>.<comportamiento>.test.ts`. Un test, una aserción
compuesta máxima. Todo test de `I-`/`P-`/`E-` tiene una línea de docstring que dice **qué puerta
o contrato evidencia** (G1, G2-T/G2-X, G3, G4, G5-F1/G5-F2, G6, G7-CAL/G7-MAIL, G8,
M1–M9 o interfaz congelada §6 de [PLAN_SPRINTS.md](PLAN_SPRINTS.md)).

### 0.6 Claiming y criterio mínimo por ticket

Este catálogo se organiza por área/capacidad, no por persona. Cuando una tabla requiera indicar
responsabilidad, `Área/Responsable reclamado` remite al claiming vigente y a su evidencia en
[sprints/](sprints/README.md); el sufijo legado del ID nunca decide esa responsabilidad.

Los tickets documentales pueden cerrarse con evidencia `C-` o una checklist definida, ejecutada y
registrada con fecha, entorno y resultado. **Ningún ticket funcional puede quedar sin al menos una
prueba trazable de este catálogo**; un documento o una checklist no sustituye la validación de una
funcionalidad, infraestructura o contrato de proveedor.

### 0.7 Trazabilidad añadida para pruebas antes huérfanas

Estas asociaciones completan las referencias del backlog sin cambiar los IDs estables:

| Prueba | Tickets naturales |
|---|---|
| U-S1-SG-03 | S1.A6, S2.3 |
| P-S2-SG-12 | S2.4 |
| E-S2-JL-09 | L2.6, D2.7 |
| U-S3-JL-05 | L3.6, A3.5 |
| I-S3-JF-06 | J3.2, J3.3 |
| E-S3-DA-05 | D3.3, J3.4 |
| U-S4-JF-03 | J4.1 |

---

## 1. Sprint 1 (S1) — Fundación + spike F0

### 1.1 Ingesta/ASR — spike y prototipo de ingesta

| ID | Nivel | Qué prueba | Contrato/puerta |
|---|---|---|---|
| U-S1-SG-01 | U | Generador de audio sintético en RAM produce texto esperado conocido (voz inteligible, no tonos); determinista con semilla | F0.1, fixture rule |
| U-S1-SG-02 | U | Validador de formatos: acepta WAV PCM s16 mono; tabla de decisión de rechazo (playlist, URL, contenedor inesperado, multi-canal) | M2 |
| U-S1-SG-03 | U | Matemática de fronteras: timestamp global = offset de fragmento + tiempo local, sin duplicar solapamiento | M2/§6 |
| I-S1-SG-04 | I | Matriz de fallos completa: éxito, upload incompleto, rechazo, timeout, cancelación, error ffmpeg/Riva/BD, SIGKILL, reinicio de contenedor y de host; fallo inyectado durante y después del commit | F0.2, G1 |
| I-S1-SG-05 | I | Expiración de lease **no** marca cleanup verificado; sin evidencia de limpieza la outbox queda bloqueada | F0.2, G1 |
| I-S1-SG-06 | I | Ausencia de original/convertidos/fragmentos en disco, Redis, logs, trazas y cachés; inspección de descriptores/procesos/mounts, no solo nombres de archivo | G1 |
| P-S1-SG-07 | P | Spike de contrato NVIDIA: idiomas habilitados, formatos, sample rate, límites payload/duración, cuotas, deadlines, precisión de offsets (`word/segment/none`) | F0.1, D2/D4/D6 |
| P-S1-SG-08 | P | Latencia por duración/idioma y memoria del SDK dentro del presupuesto autorizado | F0.1, G8 |

**Criterio de suite cerrada:** P-S1-SG-07 produce el informe del contrato; sin él, S2 no arranca.

### 1.2 API/datos — autenticación y esquema núcleo

| ID | Nivel | Qué prueba | Contrato/puerta |
|---|---|---|---|
| U-S1-AN-01 | U | Hash del identificador de sesión y revocación/expiración; cookie con flags `Secure/HttpOnly/SameSite=Lax` emitida correctamente | M1 |
| U-S1-AN-02 | U | Bootstrap y rotación CSRF: `GET /me` con `no-store` entrega el token plano cuyo hash guarda la sesión; toda mutación, incluido el `PUT`, exige token y Origin same-origin; crear/rotar sesión invalida el token anterior y logout/revocación lo inutiliza | M1 |
| U-S1-AN-03 | U | Cifrado AES-GCM de tokens de Google: nonce único por cifrado, versión de clave registrada, nada plano en BD | M1/§7 |
| I-S1-AN-04 | I | Migraciones Alembic: desde vacío y desde versión anterior, con pool de conexiones | G3 |
| I-S1-AN-05 | I | Aislamiento: usuario B no ve recursos de A ni adivinando IDs (materias, sesiones, credenciales) | G3 |
| I-S1-AN-06 | I | OAuth Google con Google de prueba o doble local: `state`, PKCE, `nonce`/issuer/audience; identidad por `sub`, no email | M1, D1 |
| I-S1-AN-07 | I | CRUD materias: borrar materia con clases activas exige moverlas o confirmar | M1 |

### 1.3 Contratos de conocimiento — preparación para S2

| ID | Nivel | Qué prueba | Contrato/puerta |
|---|---|---|---|
| U-S1-JL-01 | U | Borrador Pydantic §M4: `due_date`/`due_at` mutuamente excluyentes, `confidence_score` en [0,1] nullable, `date_status` enum | M4 |
| U-S1-JL-02 | U | Borrador Pydantic §M6: chunk con offsets Unicode `[inicio, fin)` válidos y enlaces a `chunk_segments` | M6 |
| U-S1-JL-03 | U | Dataset G4: validador de formato (pregunta, respondible sí/no, evidencia esperada); 15 no respondibles por idioma obligatorias | G4 |

### 1.4 Infraestructura/CI — base operativa

| ID | Nivel | Qué prueba | Contrato/puerta |
|---|---|---|---|
| I-S1-JF-01 | I | Smoke de compose: 8 servicios arriba; `/healthz` por proceso; `/readyz` de API (Postgres/Redis) y de ingest (BD, temporales seguros, capacidad) **sin** invocar proveedores pagados | G8 |
| I-S1-JF-02 | I | No-buffering del proxy: carga de archivo grande vía Caddy mientras se verifica que no aparece ningún archivo en ningún filesystem | G1 |
| I-S1-JF-03 | I | Redis contiene solo IDs/estado; ninguna clave con payload > umbral documentado | G1 |
| U-S1-JF-04 | U | Política de outbox por tipo: `index_requested`/`analyze_requested` quedan bloqueados por `cleanup_pending` hasta cleanup verificado; Calendar, notificaciones y borrados nacen habilitados en su propia transacción tras validar autorización, tombstone y versión | M3/§6/G5-F1/G5-F2 |

### 1.5 Web/UX — cliente y componentes base

| ID | Nivel | Qué prueba | Contrato/puerta |
|---|---|---|---|
| U-S1-DA-01 | U | Cliente HTTP: obtiene el CSRF mediante `GET /me` no cacheable, lo inyecta en toda mutación y reemplaza el valor al rotar la sesión; interceptores 401→login, 403→mensaje, 429→reintento diferido | M1/M9 |
| U-S1-DA-02 | U | Formulario de materias: validación de campos requeridos y errores de API renderizados | M1 |
| C-S1-DA-03 | C | Componentes base: badges de estado y empty states muestran los valores del contrato de estados | M9 |

---

## 2. Sprint 2 (S2) — Conocimiento: corte vertical F1

### 2.1 Ingesta/ASR — pipeline real

| ID | Nivel | Qué prueba | Contrato/puerta |
|---|---|---|---|
| U-S2-SG-01 | U | `/ingestion/capabilities` refleja los límites medidos en el spike (no constantes duras) | D2/D4 |
| U-S2-SG-02 | U | Validación ffprobe: tabla de decisión completa (contenedor, codecs, duración, canales; playlists/URLs/malformados rechazados) | M2 |
| U-S2-SG-03 | U | Invocación ffmpeg: argv construido sin interpolación de shell; límites de CPU/memoria/salida/tiempo presentes | M2/seguridad |
| U-S2-SG-04 | U | Reconciliación de fronteras: palabras/texto duplicados del overlap eliminados; orden estable; offsets globales correctos | M2 |
| U-S2-SG-05 | U | Clasificación de reintentos: gRPC transitorio reintenta (máx 3, backoff+jitter); auth/idioma/formato inválido **no** reintenta | M2 |
| I-S2-SG-06 | I | Commit atómico transcript+segmentos en una transacción; `not_requested` ≠ éxito; respuesta vacía = resultado sin texto utilizable, no relleno | M2/§6 |
| I-S2-SG-07 | I | Cleanup en `finally` ante éxito y cada error de S1; `cleanup_status=pending\|verified\|failed` separado del ASR; el camino normal no llega a `succeeded` sin verificación y un fallo detiene admisión y bloquea outbox de texto | G1/G5-F1 |
| I-S2-SG-08 | I | Reconciliador post-commit: proceso muerto tras el commit deja `transcript_committed_cleanup_pending`; solo la verificación en el mismo host/namespace habilita outbox **sin retranscribir** | F0.2/G1/G5-F1 |
| I-S2-SG-09 | I | Fencing: propietario vencido no publica transcript (token obsoleto rechazado) | M2 |
| I-S2-SG-10 | I | Cancelación: desconexión durante recepción cancela+limpia; tras 202 continúa bajo lease; cerrar pestaña no cancela; cancelar tras commit → 409 | M2 |
| E-S2-SG-11 | E | Clase sintética de 1 h (generada en RAM) transcrita de punta a punta vía proxy; demuestra `word\|segment` para G2-T o `none` con UI/SRT temporal deshabilitada para G2-X | G1/G2-T o G2-X/F1 |
| P-S2-SG-12 | P | Regresión mínima contra Riva real tras cambios del cliente bloqueante: contrato, cancelación, deadlines y clasificación de errores con presupuesto fijo y suite protegida | F0.1/G2-T o G2-X |

### 2.2 API/datos — esquema y endpoints M2

| ID | Nivel | Qué prueba | Contrato/puerta |
|---|---|---|---|
| U-S2-AN-01 | U | Validación previa a bytes: idioma obligatorio en allowlist, sin `multi`; consentimiento con `privacy_notice_version` vigente, aceptación cloud y reconocimiento de voces de terceros; ausencia/versión obsoleta rechaza antes de reservar capacidad | M2/reglas 2 y 7 |
| U-S2-AN-02 | U | Dedupe se decide en el `PUT` por hash+idioma+materia+fecha+zona; exacto devuelve canónica tras cleanup; variante devuelve token opaco de un uso, corto y ligado a tenant/hash/metadatos/clase, y exige segundo `POST` + reupload | M2 |
| U-S2-AN-03 | U | Estados ASR `awaiting_upload→receiving→transcribing→transcript_committed_cleanup_pending→succeeded` o finales tipados; cleanup independiente; análisis/índice `not_requested\|pending\|running\|succeeded\|failed\|cancelled` y agregado honesto; transiciones inválidas rechazadas | M2/M3 |
| U-S2-AN-04 | U | Tombstone/fencing: token invalidado; commit tardío rechazado | M2 |
| I-S2-AN-05 | I | Dedupe concurrente en `PUT`: exactos simultáneos convergen en la canónica; variante limpia antes del 409, rechaza token expirado/reusado/ajeno o hash distinto en el segundo `PUT`, y **nunca retiene audio** esperando decisión | M2/G3 |
| I-S2-AN-06 | I | `DELETE /audios/{id}` purga derivados; borrar mientras corre ASR produce cancelación limpia sin huérfanos | G3 |
| I-S2-AN-07 | I | SSE `GET /audios/{id}/progress`: snapshot desde BD reconectable; solo owner; muestra bytes y fragmentos reales | M2/G3 |
| I-S2-AN-08 | I | Matriz `retry` vs `reupload`: retry solo para análisis/índice; reupload solo sin transcript utilizable; mensajes de error correctos en cada caso | M2 |
| I-S2-AN-09 | I | Export `transcript.txt` siempre; `transcript.srt` **solo** con tiempos válidos; `?segment=` filtra por owner | M2/M9 |
| I-S2-AN-10 | I | Outbox `index_requested`/`analyze_requested` queda deshabilitada hasta `cleanup_status=verified`; el gate no bloquea por error outbox de Calendar, notificaciones o borrados creadas por sus transacciones | M3/G5-F1/G5-F2 |
| I-S2-AN-11 | I | Chat M7: CRUD de conversaciones con modos all/subject/class aislados por tenant; `client_message_id` idempotente; cancelación de streaming deja parcial `cancelled`; borrar conversación purga fuentes | M7/G3 |

### 2.3 Recuperación/RAG — chunking, índice y chat

| ID | Nivel | Qué prueba | Contrato/puerta |
|---|---|---|---|
| U-S2-JL-01 | U | Chunking: objetivo ~800 tokens, overlap ~100, cortes en límites de frase/segmento; offsets Unicode preservados (emoji, acentos, CJK); `chunk_segments` correcto | M6 |
| U-S2-JL-02 | U | Versionado de embeddings: generación de índice nueva con activación atómica; nunca mezcla modelo/dimensión/versión | M6/D5 |
| U-S2-JL-03 | U | RRF: fusión determinista dada la misma entrada; `k` documentado; desempate por `chunk_id` (cursor estable); dedupe y diversidad aplicados antes del top-k | M6 |
| U-S2-JL-04 | U | Filtros `user_id`/versión/tombstone aplicados **en ambas ramas** (lexical y vectorial) antes del top-k | G3 |
| U-S2-JL-05 | U | Citas: solo hacia IDs validados en BD; sin offsets fiables → «segmento N»; el modelo nunca inventa fuentes | M7/regla 5 |
| I-S2-JL-06 | I | Búsqueda híbrida contra Postgres+pgvector reales con corpus de prueba (texto redactado) | M6 |
| I-S2-JL-07 | I | Pipeline de chat SSE: eventos `delta/citation/done/error` en orden; cancelación a mitad de stream limpia estado | M7 |
| I-S2-JL-08 | I | `message_sources` contiene **todas** las fuentes entregadas al modelo, no solo las citadas | M7 |
| E-S2-JL-09 | E | Pregunta desde navegador → respuesta con cita clicable que abre el lector en el segmento | G4/F1 |
| P-S2-JL-10 | P | Proveedor de embeddings/LLM: contrato real, dimensiones y cuotas (protegida) — alimenta D5 | D5 |

**Criterio G4 (semanal, script reproducible):** Recall@8 ≥ 0.85 (respondibles, con filtros de
tenant/versión activos), precisión de citas
≥ 0.95, abstención ≥ 0.90; p95 de recuperación ≤ 500 ms. Números por idioma con
numerador/denominador, dataset versionado con partición `test` congelada, fórmula y adjudicación
publicadas conforme al protocolo de PLAN_IMPLEMENTACION §7.1.

### 2.4 Trabajo durable — outbox, workers y observabilidad

| ID | Nivel | Qué prueba | Contrato/puerta |
|---|---|---|---|
| U-S2-JF-01 | U | Backoff con jitter acotado (máx 3 transitorios); casos no transitorios no reintentan | M3 |
| I-S2-JF-02 | I | Job `index(audio_id)` idempotente: doble entrega → un solo índice; sin versiones parciales activas | G5-F1 |
| I-S2-JF-03 | I | `FLUSHALL` de Redis en entorno de prueba → jobs regenerados desde BD/outbox camino completo | G5-F1 |
| I-S2-JF-04 | I | Reconciliador: intentos muertos marcados, outbox atascada re-publicada, duplicados resueltos por unicidad/lease | G5-F1 |
| I-S2-JF-05 | I | Crash inyectado antes y después del commit entre ingesta y API: resultado según contrato | G5-F1 |
| E-S2-JF-06 | E | Smoke E2E en pipeline: upload → transcript → cleanup → índice → pregunta con cita | G4/G5-F1 |
| C-S2-JF-10 | C | Observabilidad mínima: alerta redactada por cleanup fallido, panel de fallos por etapa y ausencia de payload sensible en métricas | G1/G8 |

### 2.5 Web/UX — upload, biblioteca y chat

| ID | Nivel | Qué prueba | Contrato/puerta |
|---|---|---|---|
| U-S2-DA-01 | U | Formulario: idioma sin preselección y versión vigente del aviso, aceptación cloud y reconocimiento de voces obligatorios antes de crear reserva o iniciar el `PUT`; error de versión obsoleta obliga a revisar y aceptar de nuevo | M2/M9 |
| U-S2-DA-02 | U | Cancelar la subida cancela el intento (llamada a API + liberación de `File`/object URLs) | M9/G1 |
| U-S2-DA-03 | U | Progreso: bytes reales y fragmentos completados/total; **cero** porcentajes inventados; fallback de polling activable | M9 |
| C-S2-DA-04 | C | Lector: anclaje `?segment=` resalta; timestamps solo si válidos; export condicionado; resumen vacío si `not_requested` | M9 |
| C-S2-DA-05 | C | Citas `[Materia · Clase · mm:ss]` → ruta correcta; sin tiempos → «segmento N»; Markdown sanitizado | M7/M9 |
| E-S2-DA-06 | E | Recorrido E2E del navegador de la semana, incluido estado `partial` distinguiendo la etapa fallida | F1 |
| C-S2-DA-07 | C | Barrido global: ningún reproductor ni botón de descarga de audio; sin IndexedDB/Cache Storage con datos privados (`no-store` verificado) | G1 en cliente |

---

## 3. Sprint 3 (S3) — Acción: extracción y Calendar (F2)

### 3.1 Análisis LLM — prompts, validación y fechas

| ID | Nivel | Qué prueba | Contrato/puerta |
|---|---|---|---|
| U-S3-JL-01 | U | Validación post-LLM: enums, rangos, pertenencia de segmentos a clase/tenant/versión; span Unicode fuera de rango → rechazo | M4/G6 |
| U-S3-JL-02 | U | Evidencia: comprobación **literal** de la cita textual contra el segmento reconstruido desde BD | M4/regla 5 |
| U-S3-JL-03 | U | Resolución de fechas contra fecha/zona **de la clase**: DST (hora inexistente → siguiente válida; hora repetida → una sola), «el viernes que viene», relativas, ambiguo → `needs_review`, nunca medianoche inventada | M4/DST |
| U-S3-JL-04 | U | Ventanas con overlap: dedupe de tareas por evidencia igual; `processing_runs` hace el análisis idempotente | M4 |
| U-S3-JL-05 | U | Reanálisis: publica una revisión completa nueva y crea `task_revisions` sin sobrescribir tareas editadas/confirmadas ni activar resultados parciales | M4/G5-F2 |
| R-S3-JL-06 | R | **Regresión de prompts v1**: outputs de LLM grabados (sin claves ni PII) → validadores y agregación verificados sin gastar cuota; corre en CI | G6 |
| P-S3-JL-07 | P | Ejecución real de G6 con el proveedor autorizado (protegida, presupuesto) | G6 |

**Criterio G6:** ≥50 actividades anotadas por idioma (ambigüedades/DST incluidos): precisión ≥ 0.95,
recall ≥ 0.85, fecha exacta ≥ 0.95 en resolubles, evidencia validada al 100 %. Aplica el
protocolo §7.1: unidad de emparejamiento (tipo + fecha normalizada + evidencia), partición
`test` congelada y aprobación por idioma, no por promedio global.

### 3.2 API/datos — tareas, evidencia y revisiones

| ID | Nivel | Qué prueba | Contrato/puerta |
|---|---|---|---|
| U-S3-AN-01 | U | CHECKs de `tasks`: vencimiento excluyente; bloque Calendar usa exactamente un par instante o all-day, con fin posterior al inicio; `scheduled` **no** es estado y `calendar_sync_status` va separado | M4/M5/§6 |
| U-S3-AN-02 | U | Locking optimista: PATCH con versión incorrecta → 409 | M4 |
| I-S3-AN-03 | I | Confirmación atómica promueve `revision_id` con `expected_version`, incrementa versión y crea una outbox; doble confirmación no duplica; sin integración queda confirmada + `blocked` | M4/G5-F2/G7-CAL |
| I-S3-AN-04 | I | Editar una tarea confirmada crea `task_revision(pending_confirmation)` sin mutar campos/evento activos; versión base obsoleta → 409; reanálisis no toca revisiones activadas ni tareas editadas/confirmadas | M4/G5-F2 |
| I-S3-AN-05 | I | `task_sources` tenant-aware admite cero fuentes solo para tarea manual y una o varias fuentes validadas para extracción/chat; pertenencia o span inválido se rechaza y la evidencia es reconstruible | M4/G6 |
| I-S3-AN-06 | I | Doble análisis no duplica tareas/revisiones por evidencia; doble confirmación devuelve 202 con una sola outbox y `calendar_sync_status=pending` | M4/G5-F2 |

### 3.3 Procesamiento textual — soporte de análisis

| ID | Nivel | Qué prueba | Contrato/puerta |
|---|---|---|---|
| I-S3-SG-01 | I | Backfill sobre clases F1: solo desde texto persistido, sin reupload ni retranscripción | M4 |
| U-S3-SG-02 | U | Ventanas con overlap: coste de tokens acotado y dedupe por evidencia con transcripciones reales del spike (textuales, redactadas) | M4 |
| U-S3-SG-03 | U | Sin offsets del ASR: el análisis funciona con evidencia textual; cero minutos inventados | M4/D6 |
| I-S3-SG-04 | I | `processing_runs` por versión de transcript/config: reanálisis reprocesa solo lo necesario | M4 |
| U-S3-SG-05 | U | Hardening de análisis: backoff+jitter acotado (máx 3 transitorios), errores no transitorios sin reintento; advertencias de calidad ASR (baja confianza, segmentos vacíos) marcadas para la bandeja | M4/G6 |

### 3.4 Integración — Google Calendar

| ID | Nivel | Qué prueba | Contrato/puerta |
|---|---|---|---|
| P-S3-JF-01 | P | Scopes reales (D7-CAL) con cuenta protegida: qué permite `calendar.app.created` + `calendar.events.readonly` + `calendar.calendarlist.readonly`; registro de hallazgos; **no asumir** | D7-CAL/G7-CAL |
| P-S3-JF-02 | P | Insert con timeout → consultar ID determinista; 409 → comprobar identidad y reconciliar; `Retry-After` respetado; `invalid_grant` desconecta (no reintentar) | G7-CAL |
| I-S3-JF-03 | I | Idempotencia del sync: doble clic / respuesta perdida / reintento concurrente → **exactamente un evento** | G5-F2/G7-CAL |
| I-S3-JF-04 | I | Patch con ETag: conflicto externo no se sobrescribe; completar/descartar cancela recordatorios pero conserva el evento | G7-CAL |
| I-S3-JF-05 | I | Lectura -7/+60 días: paginación completa, recurrencias expandidas, eliminaciones; publicación **atómica** en `calendar_events`; sin mezclar `syncToken` con filtros | G7-CAL |
| I-S3-JF-06 | I | Scopes ausentes o `invalid_grant` dejan sync `blocked` sin romper biblioteca/chat; al reconectar, solo versiones vigentes bloqueadas pasan atómicamente a `pending` y crean/reutilizan outbox, nunca revisiones/jobs obsoletos | G5-F2/G7-CAL |
| I-S3-JF-07 | I | Conflictos: solape horario ≠ concentración de entregas; `GET /calendar/conflicts` correcto | M5 |

### 3.5 Web/UX — bandeja de revisión y dashboard

| ID | Nivel | Qué prueba | Contrato/puerta |
|---|---|---|---|
| C-S3-DA-01 | C | Bandeja: estados de tarea, badge `date_status` (resuelta/ambigua/faltante), advertencia de calidad ASR baja | M4/M9 |
| C-S3-DA-02 | C | Edición: vencimiento separado del bloque Calendar; pares all-day/instante excluyentes, fin posterior, duración por defecto visible/editable y revisión pendiente sin mutar lo activo | M4/M5/M9 |
| C-S3-DA-03 | C | Confirmación muestra **qué se escribirá** en Calendar antes de escribirlo; 409 refresca sin perder la edición | M4/M9 |
| C-S3-DA-04 | C | `propose_task` del chat crea propuesta en bandeja, **nunca** escribe agenda directo | regla 3 |
| E-S3-DA-05 | E | E2E con cuenta Google de prueba: propuesta → revisión/versionado → preview → confirmación → un evento visible mediante job idempotente | G5-F2/G6/G7-CAL |
| C-S3-DA-06 | C | Ficha de clase completa: resumen, temas, items, estados por etapa de análisis/índice, backfill/reanálisis visibles y advertencias de calidad sin exponer audio | M4/M9 |
| C-S3-DA-07 | C | Dashboard: no dibuja dos veces la tarea y su evento vinculado; `last_synced_at` y cobertura visibles | M5/M9 |
| C-S3-DA-08 | C | Estados de sync por tarea (`pending/synced/failed/blocked`) y UI de reconexión: al reconectar solo versiones vigentes pasan a `pending` y la UI comunica la reanudación sin auto-confirmar | M5/M9 |
| C-S3-JF-08 | C | Cuenta protegida: elegibilidad, presupuesto y limpieza de recursos de prueba firmadas | D7-CAL/G7-CAL |

---

## 4. Sprint 4 (S4) — Recordatorios, cierre y demo (F3)

### 4.1 API/datos — notificaciones, borrado y cierre G3

| ID | Nivel | Qué prueba | Contrato/puerta |
|---|---|---|---|
| U-S4-AN-01 | U | Estados de `notifications` (`pending\|sending\|sent\|failed\|delivery_unknown\|cancelled`) y transiciones válidas | M8 |
| U-S4-AN-02 | U | `dedupe_key` (user, tipo, task, due_version, occurrence) previene duplicados por construcción | M8 |
| U-S4-AN-03 | U | Cambiar vencimiento/preferencias cancela **solo** ocurrencias futuras obsoletas; nunca ráfaga retrospectiva | M8 |
| I-S4-AN-04 | I | `DELETE /me` devuelve 202 + `deletion_operation_id`; tombstone, sesiones/jobs bloqueados y purga idempotente; la sesión iniciadora queda restringida a `GET /me/deletion/{id}` y logout, las demás se revocan; borrado remoto antes de revocar tokens; `GET /me/deletion/{id}` expone `pending\|completed\|completed_with_remote_failures` y solo residuo mínimo expirado | seguridad/G3 |
| I-S4-AN-05 | I | Restore de backup (D8): solo texto/metadatos cifrados; tombstones reaplicados; nada borrado reaparece | D8/G8 |
| I-S4-AN-06 | I | Rate limits básicos: `429/503` con cabeceras correctas en endpoints sensibles; ownership en API/SSE/tools/jobs | G3/NFR |
| I-S4-AN-07 | I | Sweep de huérfanos: cero chunks/embeddings/filas derivadas sin clase tras borrados | G3 |

### 4.2 Notificaciones/operación — Gmail, scheduler y despliegue

| ID | Nivel | Qué prueba | Contrato/puerta |
|---|---|---|---|
| U-S4-JF-01 | U | Escalado T-48/T-24/T-2 h (instantes) y T-3d/T-1d a hora local (all-day); tareas sin fecha sin recordatorios | M8 |
| U-S4-JF-02 | U | DST: hora inexistente → siguiente instante válido; hora repetida → una sola notificación | M8 |
| U-S4-JF-03 | U | Plantillas HTML + texto plano escapado; destinatario fijo al email verificado; ningún GET de correo muta estado | M8/seguridad |
| I-S4-JF-04 | I | Scheduler: rol único activo; reclamación atómica de filas con múltiples workers; SLA objetivo 15 min | M8 |
| I-S4-JF-05 | I | `delivery_unknown`: respuesta perdida o lease vencido → **sin reintento automático**; `POST /notifications/{id}/resend` exige `acknowledge_duplicate_risk=true`, crea una ocurrencia auditada nueva y no cambia la original a `pending` | M8/G7-MAIL |
| I-S4-JF-06 | I | Digests diario 07:00 configurable y semanal (domingo) por fecha local, con conflictos de la semana | M8 |
| P-S4-JF-07 | P | Envío real de Gmail observado de punta a punta con cuenta autorizada + limpieza posterior (una vez, controlada) | G7-MAIL/G8 |
| L-S4-JF-08 | L | Restore comprobado en entorno limpio; rollback de imágenes probado; migración Alembic como job único | G8 |
| E-S4-JF-09 | E | G8 completo: builds/tests de las 3 apps; recordatorios dentro del objetivo con dependencias saludables | G8 |
| C-S4-JF-10 | C | Runbooks ejecutados tal cual en entorno de referencia con evidencia redactada | G1/G8 |

### 4.3 Web/UX — dashboard final, Ctrl+K y privacidad de cliente

| ID | Nivel | Qué prueba | Contrato/puerta |
|---|---|---|---|
| C-S4-DA-01 | C | Preferencias de notificaciones: opt-in Gmail, hora de digest, umbrales visibles | M8/M9 |
| C-S4-DA-02 | C | Panel de entregas: `delivery_unknown` explicado; reexpedición exige reconocimiento explícito del riesgo y conserva visible la entrega original | M8/M9/G7-MAIL |
| C-S4-DA-03 | C | Ctrl+K: snippet + materia + clase + timestamp válido; accesible en cualquier pantalla | M6/M9 |
| E-S4-DA-04 | E | Ensayo de demo completo ×2 (jueves y viernes) con guion versionado del sprint | demo |
| C-S4-DA-05 | C | Checklist G1 en cliente final: sin caché de datos privados en ninguna ruta; service worker (si existe) excluye upload/API/transcripts/chat/SSE | G1 |
| C-S4-DA-06 | C | Estadísticas por materia: clases transcritas, duración original y temas cubiertos desde metadatos de texto, sin exponer audio | M9 |

### 4.4 Ingesta/ASR — carga y hardening final

| ID | Nivel | Qué prueba | Contrato/puerta |
|---|---|---|---|
| L-S4-SG-01 | L | Re-verificación G1 bajo carga: uploads concurrentes + fallos inyectados → cero persistencia, cero temporales huérfanos, RAM dentro de reserva por slot | G1 |
| L-S4-SG-02 | L | Benchmark de TTLs (propuesta 10/30/60 min) publicado en capabilities con datos detrás | M2 |
| I-S4-SG-03 | I | Runbook ejecutado tal cual: cleanup fallido → admisión detenida + alerta; pérdida de host → restauración según documento | G1/G8 |
| C-S4-SG-04 | C | Tabla de costos reales por hora de clase (ASR, RAM, CPU ffmpeg) con mediciones reproducibles | G8 |

### 4.5 Calidad/seguridad — cierre

| ID | Nivel | Qué prueba | Contrato/puerta |
|---|---|---|---|
| R-S4-JL-01 | R | G4 y G6 finales publicadas por idioma con slices difíciles documentados | G4/G6 |
| C-S4-JL-02 | C | Revisión de seguridad ejecutada: aislamiento tenant en SSE/tools/jobs, evidencia validada, ninguna escritura sin confirmación, secretos fuera de logs/trazas | seguridad |
| C-S4-JL-03 | C | Checklist §9 de PLAN_IMPLEMENTACION marcada ítem por ítem con evidencia | cierre |
| C-S4-JL-04 | C | Retro final ejecutada y backlog F4 priorizado y registrado (acta o documento enlazado) | cierre |

---

## 5. Cobertura de puertas y variantes G1–G8

| Puerta | Definición breve | Evidencia principal planificada | Área/Responsable reclamado |
|---|---|---|---|
| G1 | No persistencia de audio + recovery F0/F1 | I-S1-SG-04/05/06, I-S1-JF-02/03, I-S2-SG-06/07/08/09/10, E-S2-SG-11, C-S2-DA-07, L-S4-SG-01 | Ingesta y privacidad / según claiming |
| G2-T | ASR con offsets temporales fiables y en rango | U-S1-SG-03, P-S1-SG-07, U-S2-SG-04, I-S2-SG-06, E-S2-SG-11, I-S2-AN-09 | Contrato ASR temporal / según claiming |
| G2-X | Producto textual aprobado, sin tiempos inventados ni SRT/enlaces temporales | P-S1-SG-07, I-S2-SG-06, I-S2-AN-09, U-S2-JL-05, C-S2-DA-04/05, E-S2-SG-11 | Contrato ASR textual / según claiming |
| G3 | Aislamiento tenant completo | I-S1-AN-05, U-S2-JL-04, I-S2-AN-05/06/07, I-S4-AN-04/06/07 | API, datos y seguridad / según claiming |
| G4 | Calidad RAG (métricas publicadas) | U-S2-JL-01/03/04/05, I-S2-JL-06/07/08, E-S2-JL-09, R-S4-JL-01 | Recuperación/RAG / según claiming |
| G5-F1 | Recuperación durable de transcript, índice y chat | U-S1-JF-04, I-S2-AN-10, I-S2-JF-02/03/04/05, E-S2-JF-06 | Trabajo durable F1 / según claiming |
| G5-F2 | Recuperación durable de análisis, revisiones y outbox Calendar | U-S3-JL-05, I-S3-AN-03/04/06, I-S3-JF-03/06, E-S3-DA-05 | Trabajo durable F2 / según claiming |
| G6 | Extracción con evidencia validada | U-S3-JL-01/02/03/05, U-S3-SG-05, R-S3-JL-06, P-S3-JL-07, I-S3-AN-05 | Análisis/evidencia / según claiming |
| G7-CAL | Calendar idempotente, reconciliación, scopes y reconnect | I-S3-AN-03, P-S3-JF-01/02, I-S3-JF-03/04/05/06, E-S3-DA-05, C-S3-DA-08 | Integración Calendar / según claiming |
| G7-MAIL | Gmail opt-in, entrega incierta y reenvío manual advertido | U-S4-JF-03, I-S4-JF-04/05, P-S4-JF-07, C-S4-DA-01/02 | Gmail/notificaciones / según claiming |
| G8 | Entrega: builds, restore, despliegue, costos | I-S1-JF-01, P-S1-SG-08, L-S4-JF-08, E-S4-JF-09, C-S4-SG-04, I-S4-AN-05 | Operación/entrega / según claiming |

---

## 6. Ejecución por semana (qué corre dónde)

| Momento | Qué se ejecuta | Área/Responsable reclamado |
|---|---|---|
| Cada commit/MR | `U-` + `I-` + smoke `E-` + regresión de prompts `R-` (con fixtures grabados) | CI/infraestructura / según claiming |
| Antes de cada demo del viernes | Suite completa local en compose + checklist manual de la semana | Áreas de los tickets / responsables reclamados |
| Manual protegida, con presupuesto | `P-` (Riva, LLM/embeddings, Google) — registro de hallazgos en `docs/` | Integración afectada / responsable reclamado |
| Antes de cierres de semana/del proyecto | `L-` (carga, RAM/tmpfs, G1 bajo carga) | Ingesta e infraestructura / según claiming |
| Semanal | Métricas G4/G6 reproducibles publicadas con numerador/denominador | Recuperación y análisis / según claiming |

> Regla honesta: un mock valida lógica, no contratos. Ninguna puerta G se cierra con mocks como
> única evidencia; las `P-` requieren cuenta protegida autorizada y presupuesto, y sus hallazgos
> se documentan (capacidades y límites observados, no supuestos).

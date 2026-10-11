# Maulwurf — Plan de implementación y validación

> Estado: plan normativo pendiente de ejecución. Revisión: 2026-09-19.
> Contratos funcionales y técnicos: [ESPECIFICACION.md](ESPECIFICACION.md). Este plan define dependencias, entregables y puertas de salida; no sustituye esos contratos.

## 1. Objetivo y decisiones invariables

Construir una web Next.js con backend FastAPI para convertir clases en conocimiento consultable y propuestas de actividades, sin conservar audio de forma persistente.

- Audio únicamente en memoria/tmpfs durante recepción, validación, conversión y ASR; eliminación comprobada antes de publicar trabajos de texto. Incluye copias, fragmentos, buffers, proxy, host y observabilidad.
- Idioma elegido explícitamente antes de subir; allowlist validada con NVIDIA. Sin autodetección `multi`, traducción ni fallback oculto.
- ASR: `nvidia-riva-client`, Whisper large-v3, gRPC TLS a `grpc.nvcf.nvidia.com:443`, function-id `b702f636-f60c-4a3d-a6f4-f3568c13bd7d`. Bearer solo en servidor. Valores sujetos a prueba del endpoint, nunca credenciales en frontend.
- Perder audio antes de persistir un transcript completo requiere nueva subida. ARQ solo reintenta análisis/índice e integraciones desde datos durables, nunca ASR desde una ruta temporal.
- Citas a texto, no reproductor. Tiempos únicamente provenientes de offsets comprobados; una cita correcta no demuestra exactitud acústica de ASR.
- Confirmación humana antes de toda escritura de agenda, incluidas revisiones de tareas; chat no escribe Calendar ni envía correo.
- Retención cloud de audio/texto requiere revisión contractual. El borrado propio no garantiza borrado en NVIDIA, LLM, Google ni dispositivos del usuario.

## 2. Situación de partida y alcance por entrega

El repositorio contiene documentación y un esqueleto mínimo: API FastAPI con health checks, paquetes de ingesta/web y manifests, pero no compose, migraciones, funcionalidades ni suites de tests. Las fases y sus casillas están **pendientes**; un placeholder o contrato escrito no demuestra implementación.

| Entrega | Alcance utilizable | Dependencias |
|---|---|---|
| F0 | Viabilidad, contratos, base técnica segura | Credenciales de prueba, infraestructura RAM segura y decisiones de privacidad |
| F1 | Biblioteca, transcript, búsqueda híbrida y chat con citas | Todas las puertas F0; timestamps validados o cambio explícitamente aceptado |
| F2 | Extracción, revisión humana, Calendar, agenda y tools limitadas | F1; contrato Google y evaluación de extracción |
| F3 | Gmail opt-in, recordatorios/digests, dashboard completo y Ctrl+K | F2; contrato Gmail y política de entrega incierta |
| F4 | Backlog exploratorio sin compromiso | Priorización posterior independiente |

F1 es la primera entrega de conocimiento, no el producto de acción completo. MVP de conocimiento + acción = F1/F2; v1 añade F3. PWA instalable y capacidades exploratorias de §13 no se activan por aparecer en el stack.

### Reglas de coordinación

1. La especificación es la fuente de contratos; cambios en fases, estados, API o garantías actualizan ambos documentos en la misma revisión.
2. Cada puerta exige evidencia: comando real, versión, fecha, entorno, resultado y métricas redactadas. Un mock no valida proveedor, ni un test unitario valida infraestructura.
3. Bloqueo de proveedor/herramientas se registra como **NO VERIFICADO**, nunca como aprobado. No usar clases reales mientras privacidad esté bloqueada.
4. S1–S4 son una secuencia de planificación y no una promesa de calendario. Solo el sprint activo se abre a claiming; el siguiente se abre después de revisar capacidad, puerta previa y disponibilidad del equipo. Se puede construir auth/esquema en paralelo al spike, pero no habilitar ingesta real antes de F0.

## 3. F0 — Viabilidad y fundación

### F0.1 Contrato NVIDIA y privacidad (bloqueante)

- [ ] Revisar términos de retención/tratamiento, regiones, subprocesadores y consentimiento para voces de terceros. Resolver D3-Audio antes de clases reales; registrar D3-Texto por separado para cada proveedor/modelo de embeddings o LLM antes de enviar transcripts reales.
- [ ] Crear spike mínimo con cliente oficial y audio sintético generado en RAM, con texto esperado conocido. La generación debe producir voz inteligible, no solo tonos para probar ASR. No conservar audio ni publicarlo como artefacto CI.
- [ ] Ejecutar transcripción, no traducción, con cada idioma candidato; probar código inválido, ausencia de idioma e idioma incorrecto. No inferir soporte de español ni de otros idiomas del ejemplo `en`/`fr`.
- [ ] Verificar formato normalizado mono PCM 16-bit WAV y sample rate efectivo. Probar formatos UI con ffprobe/ffmpeg reales, distinguiéndolos de lo que recibe Riva.
- [ ] Medir límites efectivos de payload/duración, cuotas, concurrencia, deadlines, cancelación y errores gRPC dentro del presupuesto autorizado. No hacer pruebas de saturación del proveedor sin autorización.
- [ ] Pedir offsets y verificar semántica/unidades/orden/rango y precisión en frases conocidas, silencios y fronteras. Elegir `word|segment|none` según evidencia; no asumir alineación forzada disponible.
- [ ] Medir latencia, memoria del SDK, expansión PCM, overlap y costo. Fijar tamaño de fragmento, solapamiento, límites de entrada y deadlines a partir de resultados.

**Entregable:** informe de contrato con versiones, llamadas/esquemas redactados, idiomas habilitados, formatos, límites medidos, timestamps y restricciones conocidas. No publicar API keys, metadata Authorization, voz ni transcripciones privadas.

**Salida:** D2, D3-Audio y D4 resueltas para el despliegue seleccionado. Si no hay timestamps fiables, aprobar explícitamente G2-X/D6 (producto textual) o validar alineación efímera para G2-T; actualizar spec, API y UI en la misma revisión. Sin fallback silencioso.

### F0.2 Prueba de no persistencia y recuperación (bloqueante)

- [ ] Un proceso de ingesta con filesystem raíz read-only, tmpfs acotado, usuario no root, sin swap/core dumps/snapshots/hibernación persistentes. Verificar configuración del host, no solo Compose.
- [ ] Proxy real sin buffering/caché/captura de bodies a disco; ruta binaria directa a FastAPI, sin Server Actions ni spooling por defecto de `UploadFile`.
- [ ] Medir reserva RAM por slot: original + fragmentos activos + buffers SDK/ffmpeg + margen de seguridad. Objetivo 200 MiB/3 h no es límite aprobado hasta superar prueba; reducir y publicar capacidad si no cabe.
- [ ] Prototipo de supervisor, lease, fencing y limpieza independiente del proceso ASR; comprobar descriptores/procesos/mount, no solo borrar nombres de archivo.
- [ ] Probar éxito, upload incompleto, rechazo, timeout, cancelación, error de ffmpeg/Riva/BD, SIGKILL del proceso y reinicio de contenedor/host. Inyectar fallo durante y después del commit del transcript.
- [ ] Verificar original, convertidos y fragmentos ausentes; sin bytes de audio en disco, Redis, logs, trazas, cachés ni artefactos. Persistir evidencia de inspección sin audio.
- [ ] Comprobar que expiración de lease no marca cleanup verificado. Ante falta de evidencia, bloquear outbox; ante fallo de limpieza, detener admisión local y alertar.

**Salida:** limpieza normal inmediata y recuperación por supervisor demostradas; límites/TTL publicados. Sweeper objetivo cada 60 s, no promesa de borrado instantáneo ante host inaccesible. Cualquier persistencia detectada bloquea la entrega.

### F0.3 Base de aplicación

- [ ] Monorepo de §3, versiones soportadas fijadas y lockfiles. FastAPI/Pydantic/SQLAlchemy async, Next.js/TypeScript, PostgreSQL/pgvector, Redis/ARQ y ffmpeg.
- [ ] Servicios `web`, `api`, `ingest`, `worker`, `scheduler`, `postgres`, `redis`, `caddy`; sin almacenamiento de audio. Ingesta inicial de una instancia; escalado con afinidad de reserva queda pendiente de diseño/prueba.
- [ ] OAuth Google en backend, sesión opaca, scopes incrementales, cifrado/rotación, `GET /me` como bootstrap CSRF no-cacheable, CSRF/Origin en toda mutación y cierre/revocación. No condicionar chat a scopes Calendar/Gmail.
- [ ] Migraciones tenant-aware y CRUD de materias. RLS, FKs compuestas y pruebas de pool/worker. Modelo completo para intentos, transcript/segmentos, versiones de índice/análisis y outbox; tablas posteriores pueden entregarse en su fase.
- [ ] CI: lint, tipos, tests, integración y builds reales según §9. Tests cloud protegidos, separados de MRs no confiables. Añadir instrucciones reproducibles al README.

**Salida:** sesión/aislamiento y CRUD funcionan, migraciones desde vacío y actualización pasan, tests y builds observados en entorno de referencia. Nada de esto se considera implementado por existir este plan.

## 4. F1 — Conocimiento: corte vertical completo

### F1.1 Ingesta, texto y biblioteca

- [ ] Implementar `/ingestion/capabilities` con idioma/formatos/límites/TTL efectivos. Formulario sin idioma preseleccionado, metadatos requeridos y consentimiento.
- [ ] `POST /audios` registra consentimiento versionado y reserva slot + intento; no promete dedupe antes de conocer el hash. `PUT` autenticado calcula SHA-256 y responde 202 solo tras recepción/admisión, o 200/409 de dedupe siempre después de cleanup. Compensar reservas si falla BD.
- [ ] Deduplicación por identidad exacta en el `PUT`; una variante devuelve token opaco de un solo uso, exige segundo `POST` y reupload, y verifica nuevamente el hash. No conservar audio mientras se espera confirmación. Probar concurrencia, expiración/reuso del token y separación entre usuarios.
- [ ] Transcribir fragmentos secuenciales con backpressure; reconciliar fronteras y offsets sin duplicar overlap. Empty/silencio y texto de mala calidad no se presentan como transcripción fiable.
- [ ] Commit atómico de transcript completo/segmentos/outbox deshabilitada; cleanup comprobado; habilitar **solo índice en F1**. Recuperación después del commit no pide retranscripción.
- [ ] Cancelación, tombstone, fencing y protección contra commits tardíos. Cerrar pestaña tras 202 no cancela; desconexión durante recepción sí. Reupload solo sin transcript utilizable.
- [ ] Biblioteca con lector, export TXT y SRT condicionado a offsets válidos; progreso SSE GET con snapshot/polling. No reproductor, descarga de audio ni caché privada del service worker.

### F1.2 Recuperación y chat

- [ ] Chunking por tokens y spans trazables; embeddings versionados, HNSW coseno y FTS por idioma con RRF. Filtros tenant/versión/tombstone en ambas ramas.
- [ ] Publicar índice completo atómicamente; reintentos idempotentes desde texto y reconstrucción por outbox si Redis se pierde. Evaluar recall de HNSW con filtros selectivos frente a búsqueda exacta.
- [ ] Chat `fetch` + SSE POST con snapshot, IDs monotónicos y un terminal `done|error`; presupuesto de contexto, `client_message_id` idempotente, cancelación y estados parciales. Reconectar consulta el resultado persistido y no reejecuta. Citas de registro permitido, Markdown seguro y enlaces solo tras validación.
- [ ] Persistir procedencia de todas las fuentes del contexto e historial; borrar/invalidate respuestas derivadas y bloquear publicación tardía cuando se elimina una fuente.
- [ ] Dataset textual versionado por idioma habilitado: preguntas respondibles/no respondibles, materias similares, fechas, términos técnicos y ataques de instrucciones incrustadas.

**Salida:** E2E real a través del proxy y servicios de una clase sintética de 1 h y del límite efectivo publicado. Si no se demuestra al menos 1 h, se requiere aprobación explícita para reducir el caso de uso. Audio ausente antes de indexación; lector/chat funcionan sin análisis ni resumen (`not_requested`). Pasan G1, una variante de G2 (G2-T o G2-X), G3, G4 y G5-F1. Integración ASR real sigue siendo obligatoria aunque los escenarios deterministas usen servidor gRPC de prueba.

## 5. F2 — Acción: extracción y Calendar

- [ ] Análisis por ventanas, esquema Pydantic, agregación/dedupe y revisión activa atómica. Extracción basada en fecha/zona de clase; spans Unicode por segmento y comprobación literal de evidencia.
- [ ] Nuevas clases requieren análisis e índice independientes. Backfill explícito sobre texto para clases F1; no reupload. Fallo de análisis permite chat si índice válido, y viceversa para revisión de tareas.
- [ ] Bandeja de revisión y resúmenes/temas; fechas ambiguas o calidad dudosa obligan a revisión. Nada de auto-confirmación por score. Reanálisis no sobrescribe tareas editadas/confirmadas.
- [ ] Separar vencimiento de intervalo Calendar, all-day de instantes, `task_sources`, revisión pendiente de cambios y estado de sincronización del ciclo de vida de tarea. Una edición confirmada no muta campos activos; confirmar promueve `revision_id` con control de versión y outbox atómicos.
- [ ] Validar scopes reales y creación del calendario secundario; reconexión/refresh coordinados. Probar rechazo de scopes sin romper biblioteca/chat.
- [ ] ID determinista de evento, timeout/409 reconciliados, jobs obsoletos descartados y ETag para conflictos externos. Edición local requiere reconfirmar antes de escribir Google. Reconectar Calendar reactiva solo versiones vigentes `blocked` y crea/reutiliza la outbox.
- [ ] Lectura paginada completa de ventana -7/+60 días, recurrencias/eliminaciones, publicación atómica y cobertura/frescura visibles. No combinar filtros incompatibles con syncToken.
- [ ] Dashboard de tareas/agenda sin duplicar evento vinculado; tools de lectura y `propose_task`, nunca escritura directa. Conflicto horario distinto de concentración de entregas por día.
- [ ] Completar/descartar cancela recordatorios futuros; evento conservado salvo eliminación explícita. Operaciones de borrado remoto sobreviven a la purga local sin perder autorización ni IDs necesarios.

**Salida:** E2E con una cuenta Google de prueba: propuesta → revisión → confirmación → un evento; lectura y edición confirmada. Evidencia de ninguna escritura previa a confirmar. Pruebas de doble clic, fallo de respuesta, reintentos concurrentes, DST/all-day, revocación y cambios externos; G5-F2, G6 y G7-CAL aprobadas. Pruebas remotas crean solo recursos autorizados y los limpian al finalizar.

## 6. F3 — Recordatorios y cierre de v1

- [ ] Gmail incremental/opt-in; destinatario fijo al email verificado. Templates escapados con enlaces autenticados; ningún GET de correo muta tareas.
- [ ] Scheduler ARQ único con reclamo atómico/leases y dedupe persistente. T-48/T-24/T-2 h para instantes; T-3d/T-1d a hora local para all-day; sin ráfaga retrospectiva.
- [ ] Digests por fecha local; DST, cambios de vencimiento/preferencias y cancelación de ocurrencias obsoletas. Confirmar borrado/completado de tarea inmediatamente antes de reclamar un envío.
- [ ] Fallo ambiguo de Gmail o lease `sending` vencido → `delivery_unknown`, no reintento ciego. Informar que no puede retirarse un correo ya enviado/en vuelo al borrar una tarea.
- [ ] Panel de entregas/sincronizaciones fallidas con reenvío manual advertido, preferencias, estadísticas y Ctrl+K. Re-ranker solo mediante experimento comparativo con calidad/costo/latencia.
- [ ] Runbooks de cleanup, caída de proveedor, presupuesto, tokens revocados, restauración y borrado de cuenta. D8 ya debe estar aprobada antes de datos no sintéticos; F3 comprueba restore, expiración y reaplicación de borrados.

**Salida:** envío real de prueba observado y G7-MAIL/G8 aprobadas; entrega incierta visible, no garantía de exactamente una entrega. Restauración comprobada sin reintroducir fuentes borradas. Revisión de seguridad y costos antes de producción.

## 7. Matriz de aceptación y evidencia

Los umbrales siguientes son **puertas propuestas**, no resultados. Acordarlos antes de ejecutar evaluación; no rebajarlos a posteriori para ocultar un fallo. Publicar numerador/denominador por idioma y slices difíciles, no solo un promedio global.

| ID | Prueba/criterio | Entorno mínimo |
|---|---|---|
| G1 Privacidad | Cero persistencia de audio observada en todos los caminos de F0.2; cleanup confirmado antes de salida de jobs de texto; incidentes bloquean admisión | Proxy, ffmpeg, procesos, contenedores y host reales; inspección de escritura/logs/Redis/cachés |
| G2-T ASR temporal | Idioma/formato/límites probados; offsets solicitados ordenados, en rango y con precisión aceptada; cero tiempos inventados | Riva real + voz sintética por idioma; frases, silencios y fronteras anotadas |
| G2-X ASR textual | Decisión D6 aprobada; `timestamp_precision=none`, tiempos null, SRT y enlaces temporales deshabilitados; citas solo por segmento | Riva real para texto + API/UI/E2E que demuestran ausencia de tiempos inventados |
| G3 Aislamiento | Cero accesos cruzados, incluidos IDs adivinados, búsqueda híbrida, SSE, tools, jobs y borrado concurrente | Dos usuarios mínimo, BD/cola reales y E2E |
| G4 RAG | Al menos 50 preguntas por idioma (mínimo 15 sin respuesta); Recall@8 de evidencia ≥ 0.85 en respondibles; precisión de citas revisadas ≥ 0.95; abstención correcta ≥ 0.90 en no respondibles; cero IDs de cita no autorizados | Embeddings/LLM seleccionados reales, dataset anotado y revisión humana; registrar tamaño pequeño y limitación estadística |
| G5-F1 Recuperación de conocimiento | Sin transcripts/índices/mensajes parciales activos ni duplicados; borrar fuente impide respuestas/jobs tardíos; crash antes/después de commit se recupera | Fault injection con Postgres/Redis/procesos y E2E F1 |
| G5-F2 Recuperación de acción | Sin análisis/revisiones/outbox Calendar parciales; versiones obsoletas no publican ni se reactivan | Fault injection sobre análisis, confirmación, reconnect y Calendar F2 |
| G6 Extracción | Al menos 50 actividades anotadas por idioma con ambigüedades/DST; precisión de propuestas ≥ 0.95 y recall ≥ 0.85; fecha normalizada exacta ≥ 0.95 en resolubles; todos los casos ambiguos del set van a revisión; evidencia validada en 100% de propuestas publicadas | LLM real, fixtures temporales deterministas y evaluación humana; score del modelo no sustituye estas métricas |
| G7-CAL Calendar | Ninguna escritura sin confirmación; evento único ante retries; all-day/instantes, scopes, ETag, reconnect y reconciliación comprobados | Calendar real en cuenta protegida + fallos deterministas |
| G7-MAIL Gmail | Opt-in y destinatario verificado; `delivery_unknown` visible y sin reintento ciego; reenvío manual advertido | Gmail real en cuenta protegida + fallos deterministas |
| G8 Operación | Builds/tests de API/ingest/web aprobados; restore probado; recordatorios reclamados/enviados dentro de objetivo 15 min con dependencias saludables; presupuesto/alertas activos | Despliegue de referencia y carga documentada |

### 7.1 Protocolo reproducible de métricas

G4 y G6 usan datasets versionados con particiones `development` y `test` congelado. Antes de ejecutar se publica: unidad de emparejamiento, fórmula, micro/macro promedio, tolerancias, tratamiento de múltiples respuestas válidas y procedimiento de adjudicación humana. Cada idioma habilitado debe aprobar por separado; un promedio global no compensa un idioma fallido y no se ajustan prompts contra `test`. Una cita cuenta como correcta solo si su fuente pertenece al tenant/versión, el span reconstruye texto literal relevante y responde la afirmación asociada. Una propuesta se empareja por tipo, fecha normalizada y evidencia, no solo por similitud de título.

Performance: medir recuperación p95 ≤ 500 ms y primer token de chat p95 ≤ 5 s con corpus, concurrencia y condiciones frías/calientes publicados. Son objetivos de §8, no garantías mientras falte benchmark. Para ASR no fijar SLA ni límite productivo sin F0. No asumir precisión acústica a partir de pruebas con voz sintética: antes de ampliar uso real, evaluar material representativo consentido con anotaciones y audio igualmente efímero; una evaluación solo sintética deja esa generalización no verificada.

### Capas de pruebas (todas necesarias)

1. **Unidad:** fechas/DST, offsets Unicode (incluidos emoji/acentos), overlap, evidencia, chunking, RRF, estados, dedupe y recordatorios.
2. **Integración local:** Postgres/pgvector/Redis y ffmpeg reales, proxy, auth/CSRF, outbox, tombstone/fencing, reinicios y pérdida de respuestas. Dobles de proveedor solo para fallos controlados.
3. **Contrato cloud:** Riva, LLM/embeddings y Google reales con permisos/presupuesto de prueba. Registrar capacidades observadas, no afirmar soporte basándose en documentación o mocks.
4. **E2E y carga:** navegador → proxy → servicios → BD/proveedor, con fallos por etapa, combinaciones de filtros/contexto y límites efectivos. Medir recursos y no persistencia también bajo carga.
5. **Build/entrega:** lint, tipos, suites y builds de todas las apps/imágenes; migraciones y restore. Registrar comandos exactos una vez exista el scaffold, sin inventar comandos que hoy no existen.

## 8. Riesgos y decisiones de cierre

### 8.1 Matriz de cierre D1–D8

| Decisión | Límite | Gate/artefacto requerido |
|---|---|---|
| D1 Audiencia/despliegue | Antes de abrir acceso a terceros | Revisión de privacidad, OAuth consent y modelo tenant aprobados |
| D2 Contrato Riva | F0 | Informe provider-contract versionado |
| D3-Audio | Antes de audio real | Términos, región, retención y consentimiento aprobados |
| D3-Texto | Antes de transcript real a cada LLM/embedding | Ficha por proveedor/modelo; cambiar proveedor reabre la decisión |
| D4 Capacidad | F0 | Benchmark RAM/tmpfs, límites y deadlines publicados |
| D5 Calidad/costo | RAG en F1; extracción en F2 | G4 y G6 por idioma + presupuesto |
| D6 Temporalidad | F0/F1 | Aprobar exactamente G2-T o G2-X y reflejarlo en API/UI |
| D7-CAL | Antes de F2 | Contrato Calendar y scopes reales |
| D7-MAIL | Antes de F3 | Contrato Gmail, opt-in y entrega incierta |
| D8 Retención local | Antes de datos no sintéticos | Política activa/backups/borrado; restore se valida en F3 |

Una decisión se registra con estado `pending|approved|blocked`, responsable del cierre, fecha,
evidencia y consecuencias. `blocked` no equivale a aprobado y detiene el uso real afectado.

| Riesgo | Respuesta / bloqueo |
|---|---|
| NVIDIA sin garantía de retención requerida | Bloquear audio real; decidir proveedor/despliegue con el usuario (D3) |
| Timestamps insuficientes | Bloquear promesa temporal; decidir alineación efímera o cambio de producto (D6) |
| 200 MiB/3 h supera memoria/deadline/cuota | Reducir límites efectivos con aprobación y UI coherente; no introducir disco como workaround (D4) |
| Host perdido sin evidencia de cleanup | Estado pendiente, outbox bloqueada y procedimiento de infraestructura; TTL no equivale a borrado |
| ASR confunde fechas o términos técnicos | Evidencia visible, revisión humana, evaluación por idioma y advertencias; no confianza automática |
| API cloud cambia, token Testing caduca o cuota se agota | Contratos programados, reconexión y errores tipados; no prometer proveedor intercambiable sin pruebas |
| Texto eliminado reaparece vía historial, índice o backup | Procedencia transitiva, tombstone, control de versión y borrados reaplicados al restaurar |
| Ambigüedad en Gmail | `delivery_unknown`; no exactamente-una-vez ni reintento automático |

## 9. Checklist de consistencia para cada revisión

- [ ] ESPECIFICACION y PLAN enlazan archivos existentes; fases F0–F3, variantes de gates y decisiones D1–D8 coinciden.
- [ ] Ningún flujo nuevo requiere audio tras cleanup, ni introduce storage/reproductor/retranscripción sin reupload.
- [ ] Idioma obligatorio antes de bytes; formatos/límites UI iguales a capabilities validadas.
- [ ] Estados ASR, cleanup, análisis, índice, tarea, Calendar y notificación siguen separados; F1 no depende de análisis F2.
- [ ] Esquema soporta cada contrato API: dedupe decidido en PUT, consentimiento versionado, spans/task_sources, agenda inicio/fin, revisiones, versiones y borrado rastreable.
- [ ] Consentimiento, aislamiento y confirmación humana se aplican también a jobs, tools y cambios posteriores.
- [ ] Todas las puertas pendientes o bloqueadas se reportan; ninguna prueba documental se presenta como validación de aplicación/proveedor.

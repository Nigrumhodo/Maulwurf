# Maulwurf — Plan de implementación y validación

> Estado: propuesta documental, no implementación verificada. Revisión: 2026-09-14.
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

El repositorio contiene documentación, no una aplicación ejecutable ni suites de tests/build. Las fases y sus casillas están **pendientes**; ninguna describe trabajo ya realizado.

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
4. No estimar semanas hasta medir ASR/capacidad y acordar equipo. Se puede construir auth/esquema en paralelo al spike, pero no habilitar ingesta real antes de F0.

## 3. F0 — Viabilidad y fundación

### F0.1 Contrato NVIDIA y privacidad (bloqueante)

- [ ] Revisar términos de retención/tratamiento, regiones, subprocesadores y consentimiento para voces de terceros. Resolver D3 antes de clases reales; documentar por separado proveedores de texto.
- [ ] Crear spike mínimo con cliente oficial y audio sintético generado en RAM, con texto esperado conocido. La generación debe producir voz inteligible, no solo tonos para probar ASR. No conservar audio ni publicarlo como artefacto CI.
- [ ] Ejecutar transcripción, no traducción, con cada idioma candidato; probar código inválido, ausencia de idioma e idioma incorrecto. No inferir soporte de español ni de otros idiomas del ejemplo `en`/`fr`.
- [ ] Verificar formato normalizado mono PCM 16-bit WAV y sample rate efectivo. Probar formatos UI con ffprobe/ffmpeg reales, distinguiéndolos de lo que recibe Riva.
- [ ] Medir límites efectivos de payload/duración, cuotas, concurrencia, deadlines, cancelación y errores gRPC dentro del presupuesto autorizado. No hacer pruebas de saturación del proveedor sin autorización.
- [ ] Pedir offsets y verificar semántica/unidades/orden/rango y precisión en frases conocidas, silencios y fronteras. Elegir `word|segment|none` según evidencia; no asumir alineación forzada disponible.
- [ ] Medir latencia, memoria del SDK, expansión PCM, overlap y costo. Fijar tamaño de fragmento, solapamiento, límites de entrada y deadlines a partir de resultados.

**Entregable:** informe de contrato con versiones, llamadas/esquemas redactados, idiomas habilitados, formatos, límites medidos, timestamps y restricciones conocidas. No publicar API keys, metadata Authorization, voz ni transcripciones privadas.

**Salida:** D2/D3/D4 resueltas para el despliegue seleccionado. Si no hay timestamps fiables, D6 bloquea el alcance temporal: proponer una prueba de alineación efímera o solicitar aceptación del producto textual y actualizar ambos documentos. Sin fallback silencioso.

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
- [ ] OAuth Google en backend, sesión opaca, scopes incrementales, cifrado/rotación, CSRF/Origin y cierre/revocación. No condicionar chat a scopes Calendar/Gmail.
- [ ] Migraciones tenant-aware y CRUD de materias. RLS, FKs compuestas y pruebas de pool/worker. Modelo completo para intentos, transcript/segmentos, versiones de índice/análisis y outbox; tablas posteriores pueden entregarse en su fase.
- [ ] CI: lint, tipos, tests, integración y builds reales según §9. Tests cloud protegidos, separados de MRs no confiables. Añadir instrucciones reproducibles al README.

**Salida:** sesión/aislamiento y CRUD funcionan, migraciones desde vacío y actualización pasan, tests y builds observados en entorno de referencia. Nada de esto se considera implementado por existir este plan.

## 4. F1 — Conocimiento: corte vertical completo

### F1.1 Ingesta, texto y biblioteca

- [ ] Implementar `/ingestion/capabilities` con idioma/formatos/límites/TTL efectivos. Formulario sin idioma preseleccionado, metadatos requeridos y consentimiento.
- [ ] `POST /audios` reserva slot + intento; `PUT` binario autenticado responde 202 solo tras recepción/admisión. Compensar reservas si falla BD. Rechazar sobrecarga antes del body siempre que sea posible y cortar streaming al superar límite.
- [ ] Deduplicación por identidad exacta y confirmación explícita de variantes según M2/§6; no conservar audio mientras se espera confirmación. Probar concurrencia, reupload fallido y separación entre usuarios.
- [ ] Transcribir fragmentos secuenciales con backpressure; reconciliar fronteras y offsets sin duplicar overlap. Empty/silencio y texto de mala calidad no se presentan como transcripción fiable.
- [ ] Commit atómico de transcript completo/segmentos/outbox deshabilitada; cleanup comprobado; habilitar **solo índice en F1**. Recuperación después del commit no pide retranscripción.
- [ ] Cancelación, tombstone, fencing y protección contra commits tardíos. Cerrar pestaña tras 202 no cancela; desconexión durante recepción sí. Reupload solo sin transcript utilizable.
- [ ] Biblioteca con lector, export TXT y SRT condicionado a offsets válidos; progreso SSE GET con snapshot/polling. No reproductor, descarga de audio ni caché privada del service worker.

### F1.2 Recuperación y chat

- [ ] Chunking por tokens y spans trazables; embeddings versionados, HNSW coseno y FTS por idioma con RRF. Filtros tenant/versión/tombstone en ambas ramas.
- [ ] Publicar índice completo atómicamente; reintentos idempotentes desde texto y reconstrucción por outbox si Redis se pierde. Evaluar recall de HNSW con filtros selectivos frente a búsqueda exacta.
- [ ] Chat `fetch` + SSE POST, presupuesto de contexto, idempotencia/cancelación y estados parciales. Citas de registro permitido, Markdown seguro y enlaces solo tras validación.
- [ ] Persistir procedencia de todas las fuentes del contexto e historial; borrar/invalidate respuestas derivadas y bloquear publicación tardía cuando se elimina una fuente.
- [ ] Dataset textual versionado por idioma habilitado: preguntas respondibles/no respondibles, materias similares, fechas, términos técnicos y ataques de instrucciones incrustadas.

**Salida:** E2E real a través del proxy y servicios de una clase sintética de 1 h y del límite efectivo publicado. Si no se demuestra al menos 1 h, se requiere aprobación explícita para reducir el caso de uso. Audio ausente antes de indexación; lector/chat funcionan sin análisis ni resumen (estado `not_requested`). Pasan G1–G5 de §7. Integración ASR real sigue siendo obligatoria aunque los escenarios deterministas usen servidor gRPC de prueba.

## 5. F2 — Acción: extracción y Calendar

- [ ] Análisis por ventanas, esquema Pydantic, agregación/dedupe y revisión activa atómica. Extracción basada en fecha/zona de clase; spans Unicode por segmento y comprobación literal de evidencia.
- [ ] Nuevas clases requieren análisis e índice independientes. Backfill explícito sobre texto para clases F1; no reupload. Fallo de análisis permite chat si índice válido, y viceversa para revisión de tareas.
- [ ] Bandeja de revisión y resúmenes/temas; fechas ambiguas o calidad dudosa obligan a revisión. Nada de auto-confirmación por score. Reanálisis no sobrescribe tareas editadas/confirmadas.
- [ ] Separar vencimiento de intervalo Calendar, all-day de instantes, revisión pendiente de cambios y estado de sincronización del ciclo de vida de tarea. Confirmación con control de versión y outbox atómicos.
- [ ] Validar scopes reales y creación del calendario secundario; reconexión/refresh coordinados. Probar rechazo de scopes sin romper biblioteca/chat.
- [ ] ID determinista de evento, timeout/409 reconciliados, jobs obsoletos descartados y ETag para conflictos externos. Edición local requiere reconfirmar antes de escribir Google.
- [ ] Lectura paginada completa de ventana -7/+60 días, recurrencias/eliminaciones, publicación atómica y cobertura/frescura visibles. No combinar filtros incompatibles con syncToken.
- [ ] Dashboard de tareas/agenda sin duplicar evento vinculado; tools de lectura y `propose_task`, nunca escritura directa. Conflicto horario distinto de concentración de entregas por día.
- [ ] Completar/descartar cancela recordatorios futuros; evento conservado salvo eliminación explícita. Operaciones de borrado remoto sobreviven a la purga local sin perder autorización ni IDs necesarios.

**Salida:** E2E con una cuenta Google de prueba: propuesta → revisión → confirmación → un evento; lectura y edición confirmada. Evidencia de ninguna escritura previa a confirmar. Pruebas de doble clic, fallo de respuesta, reintentos concurrentes, DST/all-day, revocación y cambios externos; G6/G7 aprobadas. Pruebas remotas crean solo recursos de prueba autorizados y los limpian al finalizar.

## 6. F3 — Recordatorios y cierre de v1

- [ ] Gmail incremental/opt-in; destinatario fijo al email verificado. Templates escapados con enlaces autenticados; ningún GET de correo muta tareas.
- [ ] Scheduler ARQ único con reclamo atómico/leases y dedupe persistente. T-48/T-24/T-2 h para instantes; T-3d/T-1d a hora local para all-day; sin ráfaga retrospectiva.
- [ ] Digests por fecha local; DST, cambios de vencimiento/preferencias y cancelación de ocurrencias obsoletas. Confirmar borrado/completado de tarea inmediatamente antes de reclamar un envío.
- [ ] Fallo ambiguo de Gmail o lease `sending` vencido → `delivery_unknown`, no reintento ciego. Informar que no puede retirarse un correo ya enviado/en vuelo al borrar una tarea.
- [ ] Panel de entregas/sincronizaciones fallidas, preferencias, dashboard completo y Ctrl+K. Re-ranker solo mediante experimento comparativo con calidad/costo/latencia.
- [ ] Runbooks de cleanup, caída de proveedor, presupuesto, tokens revocados, restauración y borrado de cuenta. Confirmar política de texto/chat/backups D8 y reaplicación de borrados tras restore.

**Salida:** envío real de prueba observado y G7/G8 aprobadas; entrega incierta visible, no garantía de exactamente una entrega. Restauración de backup comprobada sin reintroducir fuentes borradas. Revisión de seguridad y costos antes de producción.

## 7. Matriz de aceptación y evidencia

Los umbrales siguientes son **puertas propuestas**, no resultados. Acordarlos antes de ejecutar evaluación; no rebajarlos a posteriori para ocultar un fallo. Publicar numerador/denominador por idioma y slices difíciles, no solo un promedio global.

| ID | Prueba/criterio | Entorno mínimo |
|---|---|---|
| G1 Privacidad | Cero persistencia de audio observada en todos los caminos de F0.2; cleanup confirmado antes de salida de jobs de texto; incidentes bloquean admisión | Proxy, ffmpeg, procesos, contenedores y host reales; inspección de escritura/logs/Redis/cachés |
| G2 ASR | Idioma solicitado respetado; contrato de formatos/límites probado; offsets ordenados y en rango; cero tiempos inventados | Riva real + voz sintética por idioma; comparar términos, números/fechas y fronteras con texto esperado |
| G3 Aislamiento | Cero accesos cruzados, incluidos IDs adivinados, búsqueda híbrida, SSE, tools, jobs y borrado concurrente | Dos usuarios mínimo, BD/cola reales y E2E |
| G4 RAG | Al menos 50 preguntas por idioma (mínimo 15 sin respuesta); Recall@8 de evidencia ≥ 0.85 en respondibles; precisión de citas revisadas ≥ 0.95; abstención correcta ≥ 0.90 en no respondibles; cero IDs de cita no autorizados | Embeddings/LLM seleccionados reales, dataset anotado y revisión humana; registrar tamaño pequeño y limitación estadística |
| G5 Recuperación | Sin versiones parciales activas ni duplicados tras retries; borrar fuente impide respuestas/jobs tardíos; crash antes/después de commit resuelto según contrato | Fault injection con Postgres/Redis y procesos reales, E2E de estados F1/F2 |
| G6 Extracción | Al menos 50 actividades anotadas por idioma con ambigüedades/DST; precisión de propuestas ≥ 0.95 y recall ≥ 0.85; fecha normalizada exacta ≥ 0.95 en resolubles; todos los casos ambiguos del set van a revisión; evidencia validada en 100% de propuestas publicadas | LLM real, fixtures temporales deterministas y evaluación humana; score del modelo no sustituye estas métricas |
| G7 Google | Ninguna escritura sin confirmación; evento único ante retries; all-day/instantes y scopes comprobados; ninguna entrega incierta reintentada automáticamente | Contratos Calendar/Gmail reales en cuenta de prueba + fallos deterministas en integración |
| G8 Operación | Builds/tests de API/ingest/web aprobados; restore probado; recordatorios reclamados/enviados dentro de objetivo 15 min con dependencias saludables; presupuesto/alertas activos | Despliegue de referencia y carga documentada |

Performance: medir recuperación p95 ≤ 500 ms y primer token de chat p95 ≤ 5 s con corpus, concurrencia y condiciones frías/calientes publicados. Son objetivos de §8, no garantías mientras falte benchmark. Para ASR no fijar SLA ni límite productivo sin F0. No asumir precisión acústica a partir de pruebas con voz sintética: antes de ampliar uso real, evaluar material representativo consentido con anotaciones y audio igualmente efímero; una evaluación solo sintética deja esa generalización no verificada.

### Capas de pruebas (todas necesarias)

1. **Unidad:** fechas/DST, offsets Unicode (incluidos emoji/acentos), overlap, evidencia, chunking, RRF, estados, dedupe y recordatorios.
2. **Integración local:** Postgres/pgvector/Redis y ffmpeg reales, proxy, auth/CSRF, outbox, tombstone/fencing, reinicios y pérdida de respuestas. Dobles de proveedor solo para fallos controlados.
3. **Contrato cloud:** Riva, LLM/embeddings y Google reales con permisos/presupuesto de prueba. Registrar capacidades observadas, no afirmar soporte basándose en documentación o mocks.
4. **E2E y carga:** navegador → proxy → servicios → BD/proveedor, con fallos por etapa, combinaciones de filtros/contexto y límites efectivos. Medir recursos y no persistencia también bajo carga.
5. **Build/entrega:** lint, tipos, suites y builds de todas las apps/imágenes; migraciones y restore. Registrar comandos exactos una vez exista el scaffold, sin inventar comandos que hoy no existen.

## 8. Riesgos y decisiones de cierre

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

- [ ] ESPECIFICACION y PLAN enlazan archivos existentes; fases F0–F3 y decisiones D1–D8 coinciden.
- [ ] Ningún flujo nuevo requiere audio tras cleanup, ni introduce storage/reproductor/retranscripción sin reupload.
- [ ] Idioma obligatorio antes de bytes; formatos/límites UI iguales a capabilities validadas.
- [ ] Estados ASR, cleanup, análisis, índice, tarea, Calendar y notificación siguen separados; F1 no depende de análisis F2.
- [ ] Esquema soporta cada contrato API: dedupe por identidad, spans, agenda inicio/fin, revisiones, versiones y borrado rastreable.
- [ ] Consentimiento, aislamiento y confirmación humana se aplican también a jobs, tools y cambios posteriores.
- [ ] Todas las puertas pendientes o bloqueadas se reportan; ninguna prueba documental se presenta como validación de aplicación/proveedor.

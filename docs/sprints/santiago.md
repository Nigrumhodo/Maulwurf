# Santiago Montealegre — Audio · Transcripción

> Ficha personal de sprints. Plan maestro: [../PLAN_SPRINTS.md](../PLAN_SPRINTS.md) ·
> Contratos: [../ESPECIFICACION.md](../ESPECIFICACION.md) (§M2, §M3) ·
> Documento técnico: [../NVIDIA_RIVA.md](../NVIDIA_RIVA.md) ·
> Puertas: [../PLAN_IMPLEMENTACION.md](../PLAN_IMPLEMENTACION.md) (F0.1, F0.2, G1, G2) ·
> Pruebas: [../PLAN_TESTS.md](../PLAN_TESTS.md) (§1.1, §2.1, §3.3, §4.4)

## Resumen del rol

- Eres dueño de: servicio `ingest`, cliente Riva, ffmpeg/ffprobe, tmpfs, supervisor/lease/fencing.
- Tu semana 1 es la **más crítica del proyecto**: el spike F0 bloquea todo lo demás.
- Regla de oro: cero audio durable. Si algo de tu parte persiste audio (disco, Redis, logs, caché), bloquea la entrega entera.

## Semana 1 (S1) — Spike F0 (bloqueante)

### Objetivo
Probar el contrato real de NVIDIA Riva y demostrar ingesta efímera segura; sin esto no se habilita ingesta real.

### Tareas — parte A: spike de contrato (F0.1)
- [ ] S1.A1 — Generador de audio sintético **en RAM**: voz inteligible con texto esperado conocido (no solo tonos); nunca versionado ni como artefacto CI. **Día 1.**
- [ ] S1.A2 — Primera llamada real con `nvidia-riva-client==2.27.0`: TLS a `grpc.nvcf.nvidia.com:443`, function-id `b702f636-...`, Bearer solo desde backend. Usar el comando de referencia de [../NVIDIA_RIVA.md](../NVIDIA_RIVA.md). **Día 1.**
- [ ] S1.A3 — Probar idiomas candidatos uno por uno (español incluido); probar código inválido, ausencia de idioma e idioma incorrecto; **sin** `task:translate` y sin fallback a `multi`. **Días 1–2.**
- [ ] S1.A4 — Verificar formato de entrada: WAV PCM signed 16-bit mono; medir sample rate efectivo (16 kHz es candidato, no garantía). Probar con ffprobe/ffmpeg los formatos UI (mp3, m4a, wav, ogg, opus, flac, webm). **Día 2.**
- [ ] S1.A5 — Medir límites: payload máximo aceptado, duración máxima, cuotas, concurrencia, deadlines, cancelación y errores gRPC dentro del presupuesto autorizado. **No saturar el proveedor.** **Días 2–3.**
- [ ] S1.A6 — Pedir offsets (palabras/segmentos) y verificar unidades, orden, rango y precisión sobre frases conocidas, silencios y fronteras. Decidir `timestamp_precision = word | segment | none`. **Días 2–3.**
- [ ] S1.A7 — Medir latencia por duración/idioma, memoria del SDK, expansión PCM y costo. **Días 3–4.**
- [ ] S1.A8 — **Informe del contrato**: versiones, llamadas/esquemas redactados, idiomas habilitados, formatos, límites medidos, timestamps, restricciones. Sin API keys, sin metadata Authorization, sin voz ni transcripciones privadas. **Día 4.**
- [ ] S1.A9 — Presentar el informe el viernes; con José y el equipo cerrar D2/D4/D6. **Viernes.**

### Tareas — parte B: prototipo de ingesta efímera (F0.2)
- [ ] S1.B1 — Proceso de ingesta con root filesystem read-only, `tmpfs` acotado, usuario no root, sin swap/core dumps/snapshots; verificar configuración del **host**, no solo Compose. **Días 2–3.**
- [ ] S1.B2 — Reserva de RAM por slot: original + fragmentos activos + buffers SDK/ffmpeg + margen. Objetivo 200 MiB/3 h no aprobado hasta superar la prueba. **Días 3–4.**
- [ ] S1.B3 — Prototipo de supervisor, lease, fencing y limpieza independiente del proceso ASR; comprobar descriptores/procesos/mount, no solo nombres de archivo. **Días 3–4.**
- [ ] S1.B4 — Matriz de fallos probados: éxito, upload incompleto, rechazo, timeout, cancelación, error ffmpeg/Riva/BD, SIGKILL, reinicio de contenedor y de host; inyectar fallo durante y después del commit del transcript. **Día 4–5.**
- [ ] S1.B5 — Verificar ausencia de original/convertidos/fragmentos en disco, Redis, logs, trazas y cachés; persistir evidencia de inspección **sin audio**. **Día 5.**
- [ ] S1.B6 — Comprobar que expiración de lease no marca cleanup verificado; sin evidencia → outbox bloqueada; fallo de limpieza → detener admisión local + alerta. **Día 5.**

### Entregables verificables
- Informe de contrato NVIDIA — el equipo lo revisa el viernes; cierra o bloquea S2.
- Prototipo con matriz de fallos demostrada — grabación y logs de inspección sin audio.
- Decisiones D2/D4/D6 documentadas — en el plan/spec si cambian contratos.

### Alcance
**Dentro:** spike de contrato NVIDIA (F0.1), prototipo de ingesta efímera con matriz de fallos
(F0.2), informe del contrato y propuesta de D2/D4/D6.
**Fuera:** endpoints de API (Andres); compose e imágenes (Jefferson — tú consumes su imagen base);
UI de upload (Daniel); no decides D2/D4/D6: los propones con datos medidos.

### Criterios de aceptación (cierre de la semana)
- [ ] Informe publicado el día 4 con todos los campos del protocolo de José (idiomas, formatos,
      límites, timestamps, latencia, costo).
- [ ] Matriz de fallos S1.B4 demostrada completa (los 9 escenarios).
- [ ] Verificación de ausencia de audio (S1.B5) con evidencia sin audio en el repo.
- [ ] D2/D4/D6 cerradas el viernes con el equipo o bloqueadas con propuesta concreta.

### Pruebas requeridas
- Inyección de fallos real: SIGKILL, reinicio de contenedor y de host, fallo durante y tras el
  commit — ejecutadas, no simuladas.
- Inspección de filesystem/mounts/descriptores/Redis/logs — comandos y salidas guardadas sin audio.

### Riesgos y plan B
- Credenciales/presupuesto NVIDIA no listos el día 1 → aviso inmediato a José → paralelizar la
  parte B (no depende del proveedor) y reservar llamadas mínimas para el spike.
- Sin timestamps utilizables → señal: martes → decidir D6 el martes (`none` → evidencia textual),
  no arrastrarlo a S2.

### Interfaces que provee
- Límites efectivos → `GET /ingestion/capabilities` (Andres) y formulario (Daniel).
- Estados de ingesta `awaiting_upload → receiving → transcribing → succeeded | requires_reupload | rejected | cancelled` — consumidores: Andres (API), Daniel (UI).
- Contrato del transcript: segmentos, offsets, `timestamp_precision` — consumidor: José (RAG).

### Dependencias y bloqueos
- Necesitas de José: `NVIDIA_API_KEY` de prueba autorizada + presupuesto de llamadas; protocolo del spike.
- Necesitas de Jefferson: imagen base con ffmpeg en compose para la parte B.
- **Si el spike falla (p. ej. sin timestamps):** avisar el martes, no el viernes; D6 se decide en S1.

## Semana 2 (S2) — Pipeline de ingesta real (F1)

### Objetivo
Clase de 1 h transcrita de punta a punta sin persistir audio, con recovery demostrado.

### Tareas
- [ ] S2.1 — Servicio de ingesta real: `/ingestion/capabilities` con límites del spike; recepción streaming a tmpfs privado por intento; SHA-256 incremental; validación con ffprobe (contenedor, codecs, duración, canales); rechazo de playlists/URLs/malformados. **Días 1–2.**
- [ ] S2.2 — ffmpeg sin red, sin interpolación shell, con límites de CPU/memoria/salida/tiempo; normalización a WAV PCM 16-bit mono al sample rate validado; fragmentación según límites reales de Riva (**no asumir 10 min**), cortes en silencios con solapamiento configurable probado. **Días 2–3.**
- [ ] S2.3 — Procesamiento secuencial con backpressure (sin decodificar 3 h a RAM); reconciliación de fronteras: texto y palabras duplicadas, timestamps globales = offset fragmento + tiempo local, sin duplicar el overlap. **Días 3–4.**
- [ ] S2.4 — Cliente Riva bloqueante aislado del event loop, con cancelación/deadlines probados; reintentos acotados para errores transitorios gRPC dentro del intento; auth/idioma/formato inválido no se reintenta. **Días 3–4.**
- [ ] S2.5 — Commit atómico transcript+segmentos en una transacción (con Andres): idioma solicitado, modelo/config, versión, `timestamp_precision`; respuesta vacía/silencio = resultado sin texto utilizable, no relleno; calidad baja → advertencias. **Día 4.**
- [ ] S2.6 — Cleanup en `finally` al terminar ASR (éxito o error), cerrando buffers/archivos/cliente; `cleanup_status` separado del ASR; el intento pasa a `succeeded` solo tras commit **y** cleanup comprobado; habilitar outbox solo entonces. **Días 4–5.**
- [ ] S2.7 — Reconciliador post-commit: si el proceso cae tras el commit, verificar cleanup y habilitar outbox sin retranscribir; fencing token para que un propietario vencido no publique. **Día 5.**
- [ ] S2.8 — Cancelación: desconexión durante recepción cancela y limpia; tras el 202 continúa bajo lease; cerrar pestaña no cancela; cancelar después del commit devuelve conflicto. **Día 5.**
- [ ] S2.9 — E2E con clase sintética de 1 h y el límite efectivo publicado. **Viernes.**

### Entregables verificables
- Clase de 1 h transcrita — E2E a través del proxy; `timestamp_precision` real del spike.
- Matriz de recovery demostrada — SIGKILL/reinicio resueltos según contrato, sin pedir reupload si hay texto confirmado.
- Cero bytes de audio en disco/Redis/logs — verificación G1 con evidencia.

### Alcance
**Dentro:** servicio de ingesta real, normalización ffmpeg, fragmentación, reconciliación de
fronteras, cliente Riva, commit atómico, cleanup y reconciliador post-commit, cancelación, E2E 1 h.
**Fuera:** tablas `audios`/`ingestion_attempts` (Andres); UI de progreso (Daniel); dispatcher de
outbox (Jefferson); chunking e índice (José).

### Criterios de aceptación (cierre de la semana)
- [ ] Clase de 1 h transcrita E2E a través del proxy, con el `timestamp_precision` real del spike.
- [ ] Recovery demostrado: SIGKILL y reinicio tras el commit no fuerzan reupload.
- [ ] G1 verificada: cero bytes de audio en disco/Redis/logs con uploads concurrentes.
- [ ] `succeeded` solo ocurre con commit + cleanup verificado (test en rojo si no).

### Pruebas requeridas
- Matriz de fallos S1 extendida al pipeline real — pytest + fault injection con Jefferson (J2.7).
- E2E con clase sintética de 1 h generada en RAM en el momento — sin artefactos de audio versionados.

### Riesgos y plan B
- Límites reales de Riva menores que los supuestos → fragmentar más agresivamente y re-medir el
  día 3; el límite publicado en capabilities manda, no la suposición inicial.
- Cliente bloqueante degrada healthz → aislarlo en executor/hilos y medir latencia de `/healthz`
  con ASR activo; si supera el presupuesto, escalar a Jefferson el mismo día.

### Interfaces que consume
- `POST /audios` + `PUT` binario y estados — dueño: Andres.
- Outbox — shape congelado S1; dispatcher de Jefferson.

### Interfaces que provee
- Transcript/segmentos/offsets para chunking — consumidor: José.
- Estados y SSE de progreso (bytes reales, fragmentos completados/total) — consumidor: Daniel.

## Semana 3 (S3) — Soporte de análisis (F2)

### Objetivo
El texto persistido alimenta análisis y backfill de forma eficiente y trazable.

### Tareas
- [ ] S3.1 — Backfill explícito de análisis para clases F1: solo desde texto persistido, nunca reupload. **Días 1–2.**
- [ ] S3.2 — Ventanas de análisis con overlap eficiente y dedupe por evidencia; verificar coste de tokens y latencia con transcripciones reales del spike. **Días 2–3.**
- [ ] S3.3 — Robustez cuando ASR no entregó offsets: el análisis funciona igual (la evidencia es textual); sin minutos inventados en la UI. **Día 3.**
- [ ] S3.4 — `processing_runs` por versión de transcript/config: reanálisis solo re-procesa lo necesario. **Días 3–4.**
- [ ] S3.5 — Medición de costos de ASR (minutos efectivos × tarifa) y de RAM/tmpfs bajo carga; publicar números. **Día 4.**
- [ ] S3.6 — Hardening: reintentos con backoff con jitter (máx 3 transitorios), advertencias de calidad ASR visibles para la bandeja de revisión. **Día 5.**

### Entregables verificables
- Backfill funciona sobre clases F1 — demo sin reupload.
- Números de costo/RAM — al plan de costos de Jefferson (G8).

### Alcance
**Dentro:** backfill de análisis para clases F1, ventanas con overlap eficientes, robustez sin
offsets, `processing_runs` por versión, medición de costos/RAM, reintentos con backoff y jitter.
**Fuera:** runner del job (Jefferson); prompts y evaluación (José); UI de backfill (Daniel);
endpoints de reanálisis (Andres).

### Criterios de aceptación (cierre de la semana)
- [ ] Backfill demostrado sobre una clase F1 sin reupload ni retranscripción.
- [ ] Números de costo (minutos ASR × tarifa) y RAM/tmpfs publicados para Jefferson (G8).
- [ ] Clases sin offsets producen análisis con evidencia textual, sin minutos inventados en UI.

### Pruebas requeridas
- Procesamiento con transcripciones reales del spike (duraciones variadas) — medición de tokens y
  latencia guardada sin PII.

### Riesgos y plan B
- Coste de tokens por clase más alto de lo previsto → señal: medición del día 3 → ajustar tamaño
  de ventanas con José antes de que Jefferson fije el presupuesto de G8.

### Interfaces que consume
- Job `analyze(audio_id)` — dueño: Jefferson; prompts: José.

### Interfaces que provee
- Calidad ASR/advertencias por clase — consumidor: Daniel (bandeja marca revisión).

## Semana 4 (S4) — Hardening y cierre

### Objetivo
Ingesta a prueba de fallos bajo carga y documentada para operación.

### Tareas
- [ ] S4.1 — Runbook de cleanup y recuperación: cleanup fallido bloquea admisión, sweeper cada 60 s, pérdida de host, restauración. **Días 1–2.**
- [ ] S4.2 — Hardening final del supervisor: leases/heartbeat, fencing, `audio_deleted_at` solo tras comprobar ausencia de temporales. **Días 2–3.**
- [ ] S4.3 — Re-verificar G1 bajo carga: cero persistencia con uploads concurrentes y fallos inyectados. **Día 3.**
- [ ] S4.4 — TTLs efectivos (propuesta: 10 min para iniciar upload, 30 min recepción, 60 min ASR; ajustar con benchmark) publicados en capabilities. **Día 4.**
- [ ] S4.5 — Tabla de costos reales por hora de clase (ASR, RAM, CPU ffmpeg). **Día 4.**
- [ ] S4.6 — Soporte en ensayo de demo. **Viernes.**

### Entregables verificables
- Runbook publicado — revisado por Jefferson para el despliegue.
- G1 re-verificada bajo carga — evidencia sin audio.

### Alcance
**Dentro:** runbook de cleanup/recuperación, hardening final del supervisor, re-verificación G1
bajo carga, TTLs efectivos, tabla de costos reales, soporte en el ensayo de demo.
**Fuera:** despliegue y alertas (Jefferson); borrado de cuenta (Andres); PWA/dashboard (Daniel).

### Criterios de aceptación (cierre de la semana)
- [ ] Runbook publicado y revisado por Jefferson para el despliegue.
- [ ] G1 re-verificada bajo carga con fallos inyectados (evidencia sin audio).
- [ ] TTLs efectivos publicados en `/ingestion/capabilities` con benchmark detrás.

### Pruebas requeridas
- Carga — uploads concurrentes con fallos inyectados — verificar ausencia de temporales huérfanos
  y RAM dentro de la reserva por slot.

### Riesgos y plan B
- La carga concurrente revela fuga de tmpfs → reducir slots y reforzar backpressure; documentar en
  el runbook y ajustar capabilities antes de la demo.

## Dependencias semanales — qué NO te bloquea

Regla anti-ciclo (§5a del plan maestro): consumes el **contrato congelado el lunes**; el código
real de quien te provee llega a más tardar el día 3; si tarda, construyes con mock y conmutas el
miércoles/jueves. Tu mapa por semana:

| Semana | No esperes a | Consumes desde el lunes | Código real disponible |
|---|---|---|---|
| S1 | Jefferson (imagen endurecida) ni Andres (endpoints) | Contrato de `POST /audios`+`PUT` congelado | Imagen base con ffmpeg día 2 (J1.8); tu prototipo F0.2 corre en docker local efímero; lo endurecido cierra S1/S2 |
| S2 | Andres (tablas M2) | Shape de `audios`/`ingestion_attempts` congelado S1 | Endpoints de Andres día 2–3; tu pipeline se prueba contra BD real de compose con commit simulado primero |
| S3 | Jefferson (runner de jobs) | Runner idempotente S2 (J2.2) | El job `analyze` lo encapsula Jefferson con tu función |
| S4 | Jefferson (despliegue) | Compose (disponible desde S1) | Tu runbook y re-verificación G1 corren en compose; el despliegue es de Jefferson |

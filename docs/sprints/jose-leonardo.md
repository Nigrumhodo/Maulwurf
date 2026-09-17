# José Leonardo Pinilla Zamora — Líder técnico · RAG

> Ficha personal de sprints. Plan maestro: [../PLAN_SPRINTS.md](../PLAN_SPRINTS.md) ·
> Contratos: [../ESPECIFICACION.md](../ESPECIFICACION.md) (§4.4 M4, §M6, §M7) ·
> Puertas: [../PLAN_IMPLEMENTACION.md](../PLAN_IMPLEMENTACION.md) (G4, G6) ·
> Pruebas: [../PLAN_TESTS.md](../PLAN_TESTS.md) (§1.3, §2.3, §3.1, §4.5)

## Resumen del rol

- Coordinas el equipo y las interfaces congeladas; desbloqueas a diario.
- Eres dueño de: `apps/api/prompts/`, datasets de evaluación, ADRs.
- Tu vertical técnica: chunking → embeddings → RRF → chat con citas → prompts de extracción → evaluación de calidad (G4/G6).

## Semana 1 (S1) — Fundación + spike

### Objetivo
Equipo organizado, monorepo decidido y protocolo de evaluación listo para S2.

### Tareas
- [ ] L1.1 — Crear estructura del monorepo (§3.3) y repos; fijar versiones soportadas (Python, Node, Postgres 16, pgvector) y lockfiles. **Día 1.**
- [ ] L1.2 — Escribir ADRs cortos: estructura del monorepo, versiones, política de lockfiles, formato de commit/PR. **Día 1–2.**
- [ ] L1.3 — Preparar el protocolo del spike de Santiago: qué medir (idiomas, formatos, sample rate, límites payload/duración, cuotas, deadlines, offsets), formato del informe, criterios de decisión D2/D4/D6. **Día 2.**
- [ ] L1.4 — Definir el esquema Pydantic preliminar del análisis (§M4) y del chunk (§M6) como borrador de congelación. **Día 3.**
- [ ] L1.5 — Esbozar el dataset de evaluación G4: formato (pregunta, respable/no respable, evidencia esperada), ≥50 preguntas/idioma, 15 no respondibles. **Día 3–4.**
- [ ] L1.6 — Congelar interfaces S1 en el plan maestro (§5): sesión/cookie, POST /audios + PUT binario, outbox, estados de ingesta, SSE progreso. **Lunes.**
- [ ] L1.7 — Revisar todos los PRs de la semana; mantener el tablero de tickets. **Continuo.**

### Entregables verificables
- Monorepo con estructura §3.3 creada y versiones fijadas — `ls apps/ infra/` + lockfiles en el repo.
- ADRs en `docs/` — revisados por el equipo el viernes.
- Protocolo del spike — Santiago ejecuta contra él; informe S1 sigue su formato.
- Dataset G4 en formato anotado — listado de preguntas sin respuestas del LLM (solo el formato, las ejecuciones son S2).

### Alcance
**Dentro:** monorepo y ADRs; protocolo del spike; borradores de esquema (chunk §M6, análisis §M4);
formato del dataset G4; congelación de interfaces S1; coordinación y revisión de PRs.
**Fuera:** no tocas routers/models de `apps/api` (Andres), `ingest/` (Santiago), `infra/` y
`workers/` (Jefferson) ni `apps/web` (Daniel); no ejecutas el spike de NVIDIA (Santiago);
no cierras D2/D4/D6 en solitario (decisión del equipo con datos del spike).

### Criterios de aceptación (cierre de la semana)
- [ ] ADRs de estructura/versiones/lockfiles/commits revisados y aceptados por el equipo.
- [ ] Protocolo del spike publicado antes del día 2 y usado como único formato del informe.
- [ ] Dataset G4 anotado: ≥50 preguntas/idioma con 15 no respondibles, cada una con evidencia esperada.
- [ ] Congelaciones S1 (§5) sin cambios durante toda la semana.

### Pruebas requeridas
- Unit — borrador Pydantic: exclusión mutua `due_date`/`due_at` y rangos de `confidence_score` —
  pytest; Andres los formaliza en S2/S3.
- Revisión cruzada — Santiago ejecuta el protocolo el día 2 y reporta huecos del formato.

### Riesgos y plan B
- El spike falla (p. ej. sin timestamps) → señal: aviso de Santiago el martes, no el viernes →
  reunión extraordinaria el martes y decidir D6 (citas por «segmento N») antes de que S2 dependa.
- Equipo bloqueado por compose/CI → señal: daily del día 2 → pair debugging contigo y Jefferson.

### Interfaces que provee
- Congelaciones de la semana — todos los consumidores.

### Dependencias y bloqueos
- Necesitas de Santiago: resultados del spike (informe) para cerrar D2/D4/D6 el viernes.
- Necesitas de Jefferson: CI básico corriendo para que los ADRs/PRs pasen checks.

## Semana 2 (S2) — Conocimiento (F1)

### Objetivo
Pipeline RAG completo funcionando: transcript → índice híbrido → chat con citas, con métricas G4 medibles.

### Tareas
- [ ] L2.1 — Implementar chunking: objetivo ~800 tokens con overlap ~100, cortes en límites de frase/segmento, offsets de caracteres Unicode preservados, `chunk_segments` para evidencia. **Días 1–2.**
- [ ] L2.2 — Embeddings batch versionados (`index_version`, modelo/dimensión/versión) con generación de índice nueva y activación atómica; HNSW coseno. **Día 2–3.**
- [ ] L2.3 — Búsqueda híbrida: `tsvector` con configuración por idioma (spanish/english/simple) + pgvector coseno, fusión RRF; filtros `user_id`/versión/tombstone **en ambas ramas** antes de top-k. **Días 3–4.**
- [ ] L2.4 — `SearchService` reutilizable + endpoint `GET /search` (con Andres). **Día 4.**
- [ ] L2.5 — Pipeline de chat SSE: embedding de pregunta → 30 candidatos/rama → RRF → dedupe/diversidad → ~8 chunks bajo presupuesto de tokens; streaming `delta/citation/done/error`. **Días 3–5.**
- [ ] L2.6 — Citas `[Materia · Clase · mm:ss]` solo hacia IDs validados en BD; sin offsets fiables mostrar «segmento N»; `message_sources` con todas las fuentes entregadas al modelo. **Día 5.**
- [ ] L2.7 — Ejecutar evaluación G4 sobre el dataset S1: Recall@8 ≥ 0.85 (respondibles), precisión de citas ≥ 0.95, abstención ≥ 0.90 (no respondibles). **Viernes.**
- [ ] L2.8 — Medir p95 de recuperación ≤ 500 ms en corpus de referencia. **Viernes.**

### Entregables verificables
- `SearchService` + chunking/embeddings — tests de unidad (offsets Unicode, RRF) + integración con Postgres real.
- Chat con citas — E2E del navegador; ninguna cita hacia un ID no autorizado.
- Números G4 publicados — numerador/denominador por idioma en el informe del viernes, no solo promedio.

### Alcance
**Dentro:** chunking, embeddings versionados, fusión RRF, `SearchService`/`GET /search`, pipeline
de chat SSE, citas validadas, evaluación G4 y medición de p95.
**Fuera:** esquema/migraciones de chunks y embeddings (Andres); UI de chat y lector (Daniel);
dispatcher de outbox y job runner (Jefferson); correcciones del pipeline ASR (Santiago).

### Criterios de aceptación (cierre de la semana)
- [ ] G4 publicada por idioma: Recall@8 ≥ 0.85 (respondibles), precisión de citas ≥ 0.95,
      abstención ≥ 0.90, con numerador/denominador visibles.
- [ ] p95 de recuperación ≤ 500 ms medido sobre el corpus de referencia (no estimado).
- [ ] Ninguna cita resuelve a un ID no autorizado (test en rojo si ocurre).
- [ ] Un transcript índexado responde con ≥1 cita a todas las preguntas respondibles del dataset.

### Pruebas requeridas
- Unit — offsets Unicode del chunking y fusión RRF (overlap, segmentos vacíos, idiomas) — pytest.
- Integración — índice + búsqueda contra Postgres/pgvector real con índice de prueba — pytest.
- E2E — pregunta desde el navegador con cita clicable — demo del viernes.

### Riesgos y plan B
- Corpus insuficiente para medir G4 con rigor → señal: jueves con <3 clases reales → generar
  transcripciones sintéticas de clase (solo texto, nunca audio) anotadas para el dataset.
- RRF degrada recall frente a una sola rama → ajustar k/top-k y re-evaluar; si sigue bajo,
  publicar números honestos y abrir ticket de mejora, no maquillar.

### Interfaces que consume
- Estados de ingesta/transcript — dueño: Santiago/Andres.
- Tablas chunks/embeddings — dueño: Andres.

### Interfaces que provee
- Contrato de citas y eventos SSE del chat — consumidor: Daniel (UI) y Jefferson (workers).
- Shape de `/search` — consumidor: Daniel (Ctrl+K en S4).

## Semana 3 (S3) — Acción (F2)

### Objetivo
Extracción LLM con evidencia validada y prompts versionados, superando G6.

### Tareas
- [ ] L3.1 — Crear `apps/api/prompts/v1/` con system/user prompts de extracción y tests de regresión por versión; registrar `prompt_version` en resultados. **Días 1–2.**
- [ ] L3.2 — Esquema Pydantic final §M4: tipo/título/detalle, `due_date`/`due_at` mutuamente excluyentes, `date_status`, `confidence_score`, spans Unicode `[inicio, fin)` y cita textual. **Día 1.**
- [ ] L3.3 — Reglas deterministas post-LLM: enums, rangos, pertenencia de segmentos a clase/tenant/versión, reconstrucción de evidencia desde BD, comprobación literal de la cita contra el segmento. **Días 2–3.**
- [ ] L3.4 — Resolución de fechas contra fecha/zona **de la clase** (no upload, no «hoy» del servidor): DST, «el viernes que viene», horas inexistentes/duplicadas; ambiguo → `needs_review`; nunca medianoche inventada. **Días 3–4.**
- [ ] L3.5 — Análisis por ventanas con overlap y agregación/dedupe por evidencia; idempotencia con `processing_runs`. **Día 4.**
- [ ] L3.6 — Job `analyze(audio_id)` (con Jefferson en `workers/`); reanálisis crea revisión nueva sin sobrescribir tareas editadas/confirmadas. **Días 4–5.**
- [ ] L3.7 — Evaluación G6: ≥50 actividades anotadas por idioma (ambigüedades/DST incluidos): precisión ≥ 0.95, recall ≥ 0.85, fecha exacta ≥ 0.95 en resolubles, evidencia validada al 100%. **Viernes.**
- [ ] L3.8 — Resumen (3–5 frases) y temas versionados. **Día 5.**

### Entregables verificables
- `prompts/v1/` + tests de regresión — un cambio de prompt sin pasar tests no sube.
- Números G6 publicados por idioma — informe del viernes con slices difíciles.
- Tareas con evidencia reconstruida desde BD — demo: ninguna acepta un span inventado.

### Alcance
**Dentro:** prompts v1 + regresión, esquema de extracción, validación determinista post-LLM,
resolución de fechas (DST), ventanas con overlap y dedupe, job `analyze`, evaluación G6, resumen.
**Fuera:** tabla `tasks` y endpoints M4 (Andres); bandeja de revisión (Daniel); sync de Calendar
(Jefferson); backfill desde texto persistido (Santiago).

### Criterios de aceptación (cierre de la semana)
- [ ] G6 publicada por idioma: precisión ≥ 0.95, recall ≥ 0.85, fecha exacta ≥ 0.95 en
      resolubles, evidencia validada al 100%.
- [ ] `prompts/v1/` con tests de regresión en CI; un cambio de prompt sin pasar tests no se mergea.
- [ ] Ninguna propuesta publicada con span no reconstruible desde BD (test en rojo si ocurre).
- [ ] Casos DST/ambiguos del dataset terminan en `needs_review`, nunca en fecha inventada.

### Pruebas requeridas
- Unit — resolución de fechas contra la zona de la clase: DST, «el viernes que viene», horas
  inexistentes/duplicadas — pytest.
- Regresión — outputs del LLM grabados (sin claves ni PII) para validar reglas deterministas sin
  gastar cuota — pytest.
- Integración — validación de spans contra segmentos reales de BD — con Andres (A3.6).

### Riesgos y plan B
- Sin acceso a LLM (cuota/clave) → señal: día 1 → desarrollar validadores y agregación en modo
  replay con outputs guardados; la G6 real se ejecuta en cuanto haya clave autorizada.
- El modelo alucina fechas o tareas → endurecer post-validación y marcar `needs_review` más
  agresivo; documentar la tasa en el informe, no ocultarla.

### Interfaces que consume
- Transcript/segmentos con offsets — dueño: Santiago.
- Tabla `tasks` y endpoints — dueño: Andres.
- Job worker — dueño: Jefferson.

### Interfaces que provee
- Contrato de prompts v1 + schema de extracción — consumidores: Andres (tabla tasks), Daniel (bandeja de revisión), Jefferson (job analyze).

## Semana 4 (S4) — Cierre

### Objetivo
Calidad certificada con números finales y proyecto listo para demo.

### Tareas
- [ ] L4.1 — Cerrar G4/G6 con números finales y slices difíciles documentados. **Días 1–2.**
- [ ] L4.2 — Revisión cruzada de seguridad: aislamiento tenant en SSE/tools/jobs, evidencia validada, ninguna escritura sin confirmación, secretos fuera de logs/trazas. **Días 2–3.**
- [ ] L4.3 — Checklist de consistencia §9 del plan de implementación, marcada con evidencia. **Día 3.**
- [ ] L4.4 — Si algo cambió de contrato durante el proyecto: spec + plan actualizados en la misma revisión. **Continuo.**
- [ ] L4.5 — Go/no-go de demo y guion de demostración (flujo: upload → transcript → chat con cita → tarea → confirmación → evento → recordatorio). **Día 4.**
- [ ] L4.6 — Retro final y propuesta de backlog F4 priorizado. **Viernes.**

### Entregables verificables
- Informe de calidad final (G4/G6 + seguridad) — compartido con el equipo antes de la demo.
- Guion de demo — ensayado el jueves con Daniel.

### Alcance
**Dentro:** cierre de G4/G6, revisión cruzada de seguridad, checklist §9, sincronización
spec/plan si cambió algún contrato, go/no-go y guion de demo, retro y backlog F4.
**Fuera:** despliegue y runbooks de infra (Jefferson); dashboard final y Ctrl+K (Daniel);
runbook de cleanup ASR (Santiago); borrado de cuenta (Andres).

### Criterios de aceptación (cierre de la semana)
- [ ] Informe de calidad final compartido antes de la demo.
- [ ] Checklist §9 del plan de implementación marcada ítem por ítem con evidencia.
- [ ] Go/no-go de demo decidido y guion ensayado con Daniel el jueves.
- [ ] Spec y plan actualizados en la misma revisión si algún contrato cambió.

### Pruebas requeridas
- Revisión de seguridad ejecutada: aislamiento tenant en SSE/tools/jobs, evidencia validada,
  ninguna escritura sin confirmación, secretos fuera de logs/trazas — checklist firmada.
- Suite completa en verde en el MR de cierre — CI de Jefferson.

### Riesgos y plan B
- Métricas G4/G6 bajo umbral al cierre → documentar la limitación con números honestos y backlog
  priorizado; la demo muestra lo que sí cumple; no maquillar resultados.

## Dependencias semanales — qué NO te bloquea

Regla anti-ciclo (§5a del plan maestro): consumes el **contrato congelado el lunes**; el código
real de quien te provee llega a más tardar el día 3; si tarda, construyes con mock y conmutas el
miércoles/jueves. Tu mapa por semana:

| Semana | No esperes a | Consumes desde el lunes | Código real disponible |
|---|---|---|---|
| S1 | Santiago (spike) ni Jefferson (CI) | Nada externo: tú publicas protocolo y contratos | Tu protocolo día 2; CI de Jefferson día 2–4 |
| S2 | Santiago (transcripts) ni Andres (tablas) | Shape de chunks/embeddings y estados congelados | Tablas de Andres día 2; transcripts reales solo para medir el viernes |
| S3 | Andres (`tasks`) ni Jefferson (runner) | Borrador Pydantic §M4 congelado + runner S2 de Jefferson | Schema `tasks` de Andres día 2; tus prompts/validadores salen día 1–2 (antes que nadie) |
| S4 | Nadie: consumes los números de todos | Congelaciones S4 | El informe final es tuyo |

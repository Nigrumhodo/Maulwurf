# Daniel Samaca — Frontend · UX

> Ficha personal de sprints. Plan maestro: [../PLAN_SPRINTS.md](../PLAN_SPRINTS.md) ·
> Contratos: [../ESPECIFICACION.md](../ESPECIFICACION.md) (§M9 y UI de M1–M8) ·
> Puertas: [../PLAN_IMPLEMENTACION.md](../PLAN_IMPLEMENTACION.md) (G1, G3, G4 en la capa UI) ·
> Pruebas: [../PLAN_TESTS.md](../PLAN_TESTS.md) (§1.5, §2.5, §3.5, §4.3)

## Resumen del rol

- Eres dueño de: `apps/web` (Next.js App Router, TypeScript, Tailwind, shadcn/ui).
- Tu responsabilidad transversal: **el navegador nunca ve secretos ni audio** — sin cachear datos privados, sin reproductor, sin descargar audio, sin confiar en payloads del LLM.
- Tu palabra clave: cada estado del backend tiene una UI honesta — progreso real, estados `partial/failed/not_requested` visibles, y ningún minuto inventado cuando no hay timestamps.

## Semana 1 (S1) — Scaffold web

### Objetivo
Next.js corriendo contra la API local con login funcional y design system mínimo.

### Tareas
- [ ] D1.1 — Scaffold `apps/web`: Next.js App Router + TypeScript + Tailwind + shadcn/ui; versiones fijadas en lockfile. **Día 1.**
- [ ] D1.2 — Rutas base: `/` (dashboard placeholder), `/biblioteca`, `/chat`, `/materias`, `/settings`; layout con navegación. **Días 1–2.**
- [ ] D1.3 — Flujo de login con Google: redirección a `GET /auth/google/start`, manejo de callback, estados cargando/error; logout. **Días 2–3.**
- [ ] D1.4 — Cliente HTTP de la app: cookies de sesión same-origin, manejo de CSRF en mutaciones, interceptores de 401/403/429. **Día 3.**
- [ ] D1.5 — Componentes base: inputs de fecha/zona horaria/idioma, badges de estado, empty states, toasts de error. **Días 3–4.**
- [ ] D1.6 — CRUD de materias: `GET/POST /subjects`, `PATCH/DELETE`; color por materia. **Día 4.**
- [ ] D1.7 — Borradores estáticos del formulario de upload y la biblioteca (sin wiring) para validar UX temprano con el equipo. **Día 5.**

### Entregables verificables
- Login/logout funcionando en navegador — demo el viernes.
- Materias creables/editables contra la API de Andres.
- Design system mínimo documentado (componentes shadcn elegidos).

### Alcance
**Dentro:** scaffold Next.js, rutas base, login con Google, cliente HTTP con CSRF, componentes
base, CRUD de materias, borradores estáticos de upload/biblioteca.
**Fuera:** endpoints de auth/materias (Andres); servicio `web` en compose (Jefferson); no consumes
endpoints de upload/SSE hasta S2; no implementas lógica de negocio en el cliente.

### Criterios de aceptación (cierre de la semana)
- [ ] Login/logout funcionando en navegador vía Caddy (demo del viernes).
- [ ] Materias creables/editables/eliminables contra la API real de Andres.
- [ ] Mutaciones llevan CSRF; 401/403/429 redirigen o informan sin romper la app.
- [ ] Borradores de upload/biblioteca revisados por el equipo para validar UX antes de S2.

### Pruebas requeridas
- Componente — formularios de materias y estados de error — Vitest.
- Manual — flujo de login en Chrome y Firefox (mínimo) — checklist anotada.

### Riesgos y plan B
- Endpoints de Andres se retrasan → señal: daily del día 3 → construir contra el contrato
  congelado con un mock server local (sin datos reales) y cambiar de mock a API real el jueves;
  el contrato no se negocia en solitario.

### Dependencias y bloqueos
- Necesitas de Andres: endpoints de auth y materias (día 3–4).
- Necesitas de Jefferson: servicio `web` en compose con proxy.

### Interfaces que consume
- Sesión/cookie/CSRF — dueño: Andres.
- CRUD materias — dueño: Andres.

## Semana 2 (S2) — Upload, biblioteca y chat (F1)

### Objetivo
El flujo completo de conocimiento usable en navegador: subir → ver progreso → leer → chatear con citas.

### Tareas
- [ ] D2.1 — Formulario de upload según M2: materia, fecha de clase, zona horaria, **idioma sin preselección** (allowlist de capabilities), título/profesor opcionales; consentimiento visible (borrado, tratamiento cloud, sin reproducción/retranscripción). **Días 1–2.**
- [ ] D2.2 — Upload binario: `PUT` a la URL relativa devuelta por `POST /audios` con `fetch` stream; progreso real de bytes; cancelar la subida cancela el intento; sin `Server Actions`. **Días 2–3.**
- [ ] D2.3 — Progreso: SSE `GET /audios/{id}/progress` con `EventSource` + **polling como fallback**; mostrar bytes recibidos y fragmentos completados/total; nunca porcentajes inventados; snapshot al reconectar. **Días 3–4.**
- [ ] D2.4 — Biblioteca `/biblioteca`: lista por materia con estado agregado (`processing/ready/partial/failed`) y estados por etapa independientes; aviso permanente «audio no conservado»; «volver a subir» (ASR perdido) distinto de «reintentar análisis/índice». **Días 3–4.**
- [ ] D2.5 — `TranscriptReader` en `/biblioteca/[audio_id]`: segmentos enlazables/resaltables (`?segment=`), timestamps solo si son válidos; export TXT/SRT condicionado; resumen vacío si `not_requested`. **Días 4–5.**
- [ ] D2.6 — UI de chat `/chat`: composer con selector de modo (todas/materia/clase), consumo SSE con `fetch` (no `EventSource`), eventos `delta/citation/done/error`, cancelación, estados `streaming/completed/failed/cancelled`. **Días 4–5.**
- [ ] D2.7 — Citas clicables `[Materia · Clase · mm:ss]` → `/biblioteca/{audio_id}?segment=...` resaltando el texto; «segmento N» si no hay tiempos; Markdown sanitizado. **Día 5.**
- [ ] D2.8 — Requisitos de privacidad del navegador: `no-store` en contenido privado, liberar `File`/object URLs al terminar o cancelar, sin IndexedDB/Cache Storage para datos privados. **Continuo.**

### Entregables verificables
- E2E en navegador: upload de clase de 1 h → progreso → lector → pregunta con cita — se demuestra el viernes.
- Ningún reproductor ni botón de descarga de audio en toda la app.
- UI de estados parcial/fallido honesta — el estado `partial` distingue qué etapa falló.

### Alcance
**Dentro:** formulario de upload con idioma obligatorio, upload binario streaming, progreso SSE+
polling, biblioteca con estados honestos, `TranscriptReader`, UI de chat SSE con citas clicables,
higiene de privacidad del navegador.
**Fuera:** endpoints M2 (Andres); pipeline SSE del chat (José); estados de ingesta (Santiago);
nada de reproductor de audio ni descarga en ningún flujo — es contrato, no decisión de UX.

### Criterios de aceptación (cierre de la semana)
- [ ] E2E navegador: upload de 1 h → progreso real → lector → pregunta con cita clicable.
- [ ] Idioma sin preselección; consentimiento visible antes de aceptar bytes.
- [ ] Progreso: bytes reales y fragmentos completados/total; cero porcentajes inventados.
- [ ] Cero bytes de datos privados en IndexedDB/Cache Storage (verificación G1 en cliente).
- [ ] Cita sin timestamps muestra «segmento N», nunca minutos falsos.

### Pruebas requeridas
- Componente — estados de progreso y errores del upload (con mock de SSE) — Vitest.
- E2E — flujo completo contra compose — con Jefferson (smoke J2.6) + demo manual del viernes.

### Riesgos y plan B
- SSE inestable en el navegador → el fallback por polling ya está en el diseño (D2.3); activarlo
  por defecto si el jueves sigue inestable y reportar a José/Andres.
- Upload de 1 h falla por red local → probar primero con clase corta; la cancelación y reupload
  deben funcionar impecable antes de intentar la demo larga.

### Interfaces que consume
- Endpoints M2 completos — dueño: Andres.
- Contrato de citas y SSE del chat — dueño: José.
- Estados de ingesta — dueño: Santiago.

## Semana 3 (S3) — Bandeja de revisión y dashboard (F2)

### Objetivo
El humano en el loop: revisar evidencia, editar fechas y confirmar hacia Calendar sin ambigüedad.

### Tareas
- [ ] D3.1 — Bandeja «para confirmar» en el dashboard: tareas por estado (`pending/needs_review/confirmed/dismissed/completed`), evidencia textual con cita, badge de `date_status` (resuelta/ambigua/faltante) y advertencia si la calidad ASR es baja. **Días 1–2.**
- [ ] D3.2 — Edición de propuesta: título, detalle, fecha/hora/zona, all-day vs instante, y **bloque de agenda separado del vencimiento** (inicio/fin con duración por defecto visible y editable). **Días 2–3.**
- [ ] D3.3 — Botones confirmar/descartar/completar con control de versión: detectar 409 de edición concurrente y refrescar; confirmación muestra qué se escribirá en Calendar. **Día 3.**
- [ ] D3.4 — «Ver segmento de transcripción» desde la tarea — abre `TranscriptReader` con el segmento resaltado; timestamp solo si existe. **Día 3.**
- [ ] D3.5 — Ficha de clase completa: resumen, temas, items detectados, estados de análisis/índice; backfill/reanálisis visibles cuando apliquen. **Día 4.**
- [ ] D3.6 — Dashboard con agenda: `CalendarView` (FullCalendar) con eventos Google + tareas Maulwurf coloreadas por materia, **sin dibujar dos veces la tarea y su evento vinculado**; frescura de sincronización visible. **Días 4–5.**
- [ ] D3.7 — Estados de sincronización Calendar por tarea (`pending/synced/failed/blocked`) y UI de reconexión Google; sin auto-escritura en ningún flujo. **Día 5.**
- [ ] D3.8 — Herramientas del chat solo lectura: respuestas que usan agenda/tasks distinguen la fuente; `propose_task` crea propuesta en bandeja, nunca agenda directo. **Día 5.**

### Entregables verificables
- Flujo E2E con cuenta Google de prueba: propuesta → revisión → confirmación → evento visible — demo del viernes (G6/G7 desde la UI).
- Ningún flujo de UI escribe Calendar sin botón de confirmación explícito.
- Conflicto horario vs concentración de entregas diferenciados visualmente.

### Alcance
**Dentro:** bandeja de revisión con evidencia, edición con bloques separados vencimiento/agenda,
confirmación con control de versión, ficha de clase, dashboard con agenda, estados de sync, tools
de chat solo lectura.
**Fuera:** endpoints M4 y locking (Andres); job de Calendar (Jefferson); tools (José). Ningún
flujo UI escribe Calendar sin botón explícito — cualquier atajo se rechaza en revisión.

### Criterios de aceptación (cierre de la semana)
- [ ] E2E con cuenta de prueba: propuesta → revisión → confirmación → evento visible (G6/G7 desde UI).
- [ ] Confirmación muestra exactamente qué se escribirá en Calendar antes de escribirlo.
- [ ] 409 de edición concurrente se detecta y refresca sin perder la edición del usuario.
- [ ] `propose_task` desde chat crea propuesta en bandeja, nunca escribe agenda.

### Pruebas requeridas
- Componente — bandeja: estados, badges `date_status`, advertencia de calidad ASR — Vitest.
- E2E — flujo de revisión/confirmación contra compose — con Jefferson + demo manual.

### Riesgos y plan B
- Los estados de sync de Jefferson llegan tarde → renderizar `pending/synced/failed/blocked` desde
  el contrato congelado; los valores reales se conectan cuando el job esté vivo.
- FullCalendar pesa/conflicta con el build → caer a una vista de lista semanal propia si el día 4
  no está integrado; el dashboard no se bloquea por el widget.

### Interfaces que consume
- Endpoints M4 y `tasks` — dueño: Andres.
- Contrato de `calendar_events` y sync — dueño: Jefferson.

## Semana 4 (S4) — Dashboard final, Ctrl+K y demo (F3)

### Objetivo
Producto cerrado: notificaciones configurables, búsqueda global y demo impecable.

### Tareas
- [ ] D4.1 — Preferencias de notificaciones en `/settings`: opt-in Gmail, hora del digest diario/día semanal, umbrales de recordatorio visibles. **Día 1.**
- [ ] D4.2 — Panel de entregas/sincronizaciones fallidas: `delivery_unknown` explicado, reexpedición manual con advertencia de duplicado. **Día 2.**
- [ ] D4.3 — Ctrl+K global con `GET /search`: resultados con snippet + materia + clase + timestamp válido; accesible en cualquier pantalla. **Días 2–3.**
- [ ] D4.4 — Estadísticas por materia: clases transcritas, duración original, temas cubiertos — sin exponer audio. **Día 3.**
- [ ] D4.5 — PWA instalable solo si queda margen: manifest + service worker que **excluye** upload, API, transcripts/chat y SSE de cualquier caché. **Día 4 (opcional).**
- [ ] D4.6 — Pulido final: empty states, mensajes de error accionables, loading states, accesibilidad básica (labels, focus, contraste). **Días 3–4.**
- [ ] D4.7 — Ensayo de demo con José: guion completo upload → chat con cita → tarea → confirmación → evento → recordatorio. **Día 4.**
- [ ] D4.8 — Revisión final de privacidad del navegador con José (checklist G1 en cliente). **Día 5.**

### Entregables verificables
- Demo completa sin puntos muertos — ensayada el jueves.
- Ctrl+K funcional contra `/search` — consumidor final de la API de José.
- Service worker (si existe) verificado sin caché de datos privados.

### Alcance
**Dentro:** preferencias de notificaciones, panel de entregas fallidas, Ctrl+K, estadísticas por
materia, pulido/accesibilidad, ensayo de demo, checklist de privacidad de cliente.
**Fuera:** envío real de Gmail y scheduler (Jefferson); PWA solo si sobra margen (D4.5) y nunca
cacheando upload/API/transcripts/chat/SSE; no añades funcionalidad nueva en la semana de cierre.

### Criterios de aceptación (cierre de la semana)
- [ ] Demo completa sin puntos muertos, ensayada el jueves con José.
- [ ] Ctrl+K funcional contra `GET /search` en cualquier pantalla.
- [ ] `delivery_unknown` explicado en UI; reexpedición manual advierte de duplicado.
- [ ] Checklist G1 en cliente verificada: sin caché de datos privados en ninguna ruta.

### Pruebas requeridas
- Componente — Ctrl+K, panel de entregas, preferencias — Vitest.
- Manual — guion de demo completo dos veces (una el jueves, una el viernes) — checklist anotada.

### Riesgos y plan B
- La demo larga falla por red → tener grabada una ejecución exitosa (sin datos privados reales, con
cuenta de prueba) como respaldo y una clase de prueba corta preparada.

## Dependencias semanales — qué NO te bloquea

Regla anti-ciclo (§5a del plan maestro): consumes el **contrato congelado el lunes**; el código
real de quien te provee llega a más tardar el día 3; si tarda, construyes con mock y conmutas el
miércoles/jueves. Tu mapa por semana:

| Semana | No esperes a | Consumes desde el lunes | Código real disponible |
|---|---|---|---|
| S1 | Andres (auth/materias) | Contrato de sesión y CRUD de materias | Mock local; API real de Andres día 3–4; conmutas jueves |
| S2 | Santiago (SSE) ni José (chat) | Eventos `delta/citation/done/error` y formato de citas | Endpoints M2 día 3–5; chat de José día 4–5; componentes con mocks hasta entonces |
| S3 | Jefferson (sync Calendar) ni Andres (M4) | Estados de sync y contrato `tasks` congelados | Job Calendar día 4–5; bandeja funciona con `pending` hasta entonces |
| S4 | Jefferson (Gmail real) | Contrato de notificaciones/preferencias | Scheduler real día 2–3; panel funciona con estados simulados hasta el ensayo |

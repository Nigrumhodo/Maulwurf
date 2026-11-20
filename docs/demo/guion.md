# Guion de Demostración y Criterios Go/No-Go (`L4.5` / `D4.7`)

- **Versión:** `2026-11-19`
- **Entornos aplicables:**
  - **Modo A (Ensayo local / Compose):** `infra/docker-compose.yml` con dobles/proveedores locales configurados en desarrollo.
  - **Modo B (Despliegue de referencia):** `infra/docker-compose.prod.yml` + `infra/deploy/Caddyfile.prod` siguiendo `docs/runbooks/deploy.md`.

---

## 1. Pre-vuelo (Go / No-Go antes de empezar)

### A. Obligatorio para ensayo local completo (Modo A)
- [ ] Contenedores levantados y saludables (`/healthz` y `/readyz` en `200 OK`).
- [ ] Migraciones aplicadas hasta `apps/api/alembic/versions/0022_s3_chat_tool_calls.py` sin errores.
- [ ] `infra/Caddyfile` validado (`scripts/ci/check_caddy_limits.py` en verde).
- [ ] Navegador limpio (sin sesiones previas) apuntando al origen servido por Caddy.

### B. Adicional obligatorio para demo pública en entorno de referencia (Modo B — **OPEN / EXTERNAL** hasta cierre en host real)
- [ ] Cuentas y cuotas reales verificadas: NVIDIA Riva (`D2`, `D3-Audio`, `D4`, `J4.10`), LLM/Embeddings (`D3-Text`), Google OAuth Verification + Calendar (`J3.1`, `J3.9`) y Gmail `gmail.send` (`D6`, `J4.9`).
- [ ] Evaluaciones humanas `G4`/`G6` cerradas (`L2.7`, `L4.1`) y firma par en `docs/reviews/security-cross-review.md` (`L4.2`).
- [ ] Dos ensayos completos en host de referencia (`D4.7`) + simulacro de rollback probado (`A4.8`, `S4.6`, `docs/runbooks/deploy.md`).

> **Regla Go / No-Go:** Sin las casillas de la sección **A**, la demo local es **NO-GO**. Sin las casillas de la sección **B**, **NO se debe presentar el despliegue público como validado con datos/cuentas de producción**; solo debe demostrarse el flujo técnico en Modo A dejando constancia explícita de los gates externos pendientes.

---

## 2. Guion paso a paso (12 minutos)

### Paso 1 — Landing pública sin fugas (`/` — `D1.8`)
1. Abrir `/` sin sesión activa (`apps/web/src/pages/LandingPage.tsx`).
2. Confirmar que el panel visual se carga sin errores en consola ni llamadas privadas (`/me`, `/audios`, `/tasks`, `/notifications`).
3. Pulsar **Iniciar sesión con Google** (`/login`).

### Paso 2 — Materias y estadísticas agregadas (`/gestion/materias` — `D4.4`)
1. Crear o seleccionar una materia académica (ej. *Sistemas Distribuidos*).
2. Mostrar `SubjectStats` (`apps/web/src/components/subjects/SubjectStats.tsx`): sesiones transcritas, duración agregada y temas tratados — destacando explícitamente que **no existe archivo de audio ni reproductor**.

### Paso 3 — Subida con idioma obligatorio y progreso SSE (`/gestion/subir` — `D2.2–D2.4`)
1. Seleccionar un audio de clase de prueba y **elegir idioma explícitamente** antes de habilitar el envío (`apps/web/src/components/upload/UploadForm.tsx`).
2. Iniciar la subida binaria PUT (`apps/web/src/components/upload/AudioUploadPanel.tsx`) y observar el avance en tiempo real por SSE (`apps/web/src/components/IngestProgress.tsx`).
3. Subrayar la invariante **G1**: el audio se borra tras la transcripción y nunca se almacena en navegador ni persistencia de objeto.

### Paso 4 — Biblioteca y lector sincronizado (`/biblioteca`, `/biblioteca/:audioId` — `D2.5`)
1. Abrir la clase recién procesada desde la biblioteca (`apps/web/src/pages/BibliotecaPage.tsx`).
2. Revisar resumen estructurado, temas y segmentos transcritos en `apps/web/src/components/TranscriptReader.tsx`.
3. Probar la exportación `.txt`/`.srt` (`apps/web/src/lib/reader.ts`), verificando que solo genera texto y revoca el Blob temporal.

### Paso 5 — Búsqueda global `Ctrl+K` / `Cmd+K` (`D4.3`) y Chat RAG con citas (`/chat` — `D2.6–D2.7`)
1. Pulsar `Ctrl+K` (`Cmd+K`) desde cualquier pantalla para abrir `apps/web/src/components/search/CommandPalette.tsx`, buscar un término de la clase y navegar con teclado hasta el segmento citado.
2. Ir a `/chat` y formular una pregunta sobre fechas o entregables de la clase; observar la respuesta en streaming SSE con citas verificables por offset (`apps/web/src/components/chat/ChatPanel.tsx`, `Citation.tsx`) y herramientas de lectura.

### Paso 6 — Bandeja de tareas y confirmación humana (`/gestion/tareas` — `D3.1–D3.5`)
1. Mostrar las propuestas extraídas automáticamente con sus citas de evidencia (`TaskInbox.tsx`).
2. Editar o revisar una fecha propuesta y **confirmar explícitamente** la tarea; mostrar que **nunca se escribe en Google Calendar sin confirmación humana**.

### Paso 7 — Vista de Agenda y sincronización Calendar (`/gestion/agenda` — `D3.6–D3.8`)
1. Abrir el calendario de entregas y revisar estado de sincronización y detección de conflictos (`CalendarView.tsx`, `SyncStatus.tsx`).

### Paso 8 — Preferencias, Gmail Opt-in, bitácora de envíos y `DELETE /me` (`/settings` — `D4.1–D4.2`)
1. Abrir `/settings` (`apps/web/src/pages/SettingsPage.tsx`):
   - Configurar zona horaria IANA (`PATCH /me`) y resumen diario/semanal (`NotificationPreferences.tsx`).
   - Explicar el **opt-in explícito** de `gmail.send` (destinatario fijo a la cuenta verificada).
   - Revisar `DeliveryPanel.tsx` (`GET /notifications`, explicación clara de `delivery_unknown` y reenvío manual solo tras confirmar posible duplicado).
2. Cerrar mostrando la sección `AccountDeletionPanel.tsx` (`DELETE /me`): eliminación trazable de cuenta con elección explícita respecto a eventos ya creados en Calendar (`delete_maulwurf_events=false` por defecto) y estado/reanudación observable.

---

## 3. Plan de contingencia en vivo (`S4.6`)
- **Caída temporal de proveedor ASR/LLM:** mostrar estado tipado y runbook `docs/runbooks/provider-down.md`; no reintentar ASR sin audio durable.
- **Estado `delivery_unknown` en correo:** demostrar que Maulwurf detiene el reenvío automático para evitar spam y lo deriva al panel manual en `/settings`.
- **Incidencia durante despliegue de referencia:** ejecutar rollback documentado en `docs/runbooks/deploy.md`.

---

## 4. Registro de ensayos (`D4.7`)

| Ensayo | Fecha | Entorno | Estado | Incidencias y acciones |
|---|---|---|---|---|
| Ensayo 1 (Jueves) | Pendiente | Host de referencia (`infra/docker-compose.prod.yml`) | **NOT RUN** (requiere host y credenciales live) | Pendiente de ejecución externa |
| Ensayo 2 (Viernes) | Pendiente | Host de referencia (`infra/docker-compose.prod.yml`) | **NOT RUN** (requiere host y credenciales live) | Pendiente de ejecución externa |

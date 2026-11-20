# Checklist G1 del cliente web (`D4.8` / `C-S4-DA-05`)

- Fecha de verificación: `2026-11-19`
- Ramas y suites verificadas: `apps/web/tests/unit/{g1-privacy,pwa-sw,upload,landing-page,command-palette,delivery-panel,account-deletion,subject-stats}.test.*`

## Controles verificados

1. **Sin persistencia ni reproducción de audio en el navegador (`g1-privacy.test.ts`):**
   - Cero etiquetas `<audio>` en `apps/web/src/`.
   - La única llamada a `URL.createObjectURL` vive en `lib/reader.ts` para descargar texto plano `.txt`/`.srt` y libera inmediatamente con `URL.revokeObjectURL` en `finally`.
   - La referencia `File` seleccionada en `AudioUploadPanel` se envía por streaming y se libera explícitamente tras completar, abortar o cancelar.
2. **Cero caché de datos privados en navegador (`api.ts`, `pwa-sw.test.ts`):**
   - Todas las llamadas `apiFetch` y `bootstrapCsrf` imponen `cache: "no-store"` y credenciales same-origin.
   - El token CSRF (`lib/csrf.ts`) reside exclusivamente en memoria; ningún token ni transcripción usa `localStorage`, `sessionStorage` ni `IndexedDB`.
   - `SubjectStats` (`D4.4`) agrega únicamente contadores de metadatos/texto.
3. **Exclusiones del Service Worker PWA (`apps/web/public/sw.js`):**
   - Solo cachea shell estático (`/`, `/manifest.webmanifest`, `/icon.svg`).
   - Omite métodos distintos de `GET`, peticiones cross-origin, streams `text/event-stream`, respuestas `Cache-Control: no-store` y cualquier prefijo privado (`/auth`, `/me`, `/audios`, `/subjects`, `/tasks`, `/calendar`, `/chat`, `/search`, `/notifications`, `/integrations`, `/ingestion`, `/internal`, `/metrics`, `/healthz`, `/readyz`).

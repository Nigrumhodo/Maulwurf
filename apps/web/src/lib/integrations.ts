import { apiFetch } from "./api";

// D3.3: the review dialog must show the Calendar target before confirming. The backend
// owns the readiness decision (connected + scopes + write calendar); the web never calls
// Google or infers a writable target from a calendar id alone.

export interface GoogleStatus {
  status: "connected" | "disconnected";
  scopes: string[];
  calendar_id: string | null;
  calendar_write_ready: boolean;
  token_expires_at: string | null;
  last_refresh_at: string | null;
}

export interface IntegrationsStatus {
  google: GoogleStatus;
}

export async function fetchIntegrationsStatus(): Promise<IntegrationsStatus> {
  const res = await apiFetch("/integrations/status");
  return (await res.json()) as IntegrationsStatus;
}

// D3.7 — the browser navigates to the GET variants: a `fetch` POST cannot follow the
// cross-origin 302 to Google nor render the consent screen.
export const GOOGLE_CONNECT_PATH = "/integrations/google/connect";
export const GOOGLE_RECONNECT_PATH = "/integrations/google/reconnect";

export function googleConnectHref(google: GoogleStatus | null): string {
  if (google === null) return GOOGLE_CONNECT_PATH;
  return google.status === "connected" || google.calendar_id !== null
    ? GOOGLE_RECONNECT_PATH
    : GOOGLE_CONNECT_PATH;
}

export function googleConnectLabel(google: GoogleStatus | null): string {
  return googleConnectHref(google) === GOOGLE_RECONNECT_PATH
    ? "reconectar google calendar"
    : "conectar google calendar";
}

/**
 * Per-task sync explanation (C-S3-DA-08). The raw `calendar_sync_status` stays in the
 * badge; this only adds context and never suggests a write the backend does not expose.
 * `failed` is info-only: an external edit is resolved in Google, not by an automatic retry.
 */
export function syncStatusDescription(status: string): string | null {
  switch (status) {
    case "not_requested":
      return "sin envío a Calendar; solo se escribe tras confirmar.";
    case "pending":
      return "envío a Calendar en cola; el worker lo escribirá.";
    case "synced":
      return "evento escrito en el calendario Maulwurf.";
    case "failed":
      return "el evento cambió en Google o no se pudo actualizar; revísalo en Google, no se reintenta automáticamente.";
    case "blocked":
      return "sin Calendar listo; la tarea quedó bloqueada hasta reconectar.";
    default:
      return null;
  }
}

export function calendarTargetLabel(google: GoogleStatus): string {
  if (google.calendar_write_ready && google.calendar_id !== null) {
    return `Maulwurf · ${google.calendar_id}`;
  }
  if (google.calendar_id !== null) {
    return `Maulwurf · ${google.calendar_id} (no listo; quedará bloqueada)`;
  }
  return "sin calendario conectado; la tarea quedará bloqueada";
}

// Closed reason vocabulary produced by the Calendar start/callback redirects. Unknown
// values fall back to a generic message instead of echoing the query string.
const CALENDAR_ERRORS: Record<string, string> = {
  not_configured: "La integración con Google no está configurada en el servidor.",
  not_connected: "No hay una integración de Google que reconectar.",
  rate_limited: "Demasiados intentos; espera un momento y vuelve a intentar.",
  capacity_unavailable: "El servicio no está disponible; inténtalo más tarde.",
  invalid_state: "La sesión de consentimiento no es válida; vuelve a intentarlo.",
  access_denied: "No autorizaste el acceso a Calendar.",
  missing_params: "Google no devolvió los datos esperados.",
  state_mismatch: "El consentimiento pertenece a otra sesión.",
  oauth_failed: "Google rechazó el intercambio de tokens.",
  wrong_account: "La cuenta de Google no coincide con tu sesión.",
  scope_required: "Faltan permisos de Calendar; la integración no se guardó.",
  invalid_grant: "Google revocó el acceso; reconecta la integración.",
  calendar_unavailable: "Google Calendar no respondió; inténtalo más tarde.",
  calendar_error: "Google Calendar devolvió un error al preparar el calendario.",
};

export function calendarErrorCopy(reason: string | null): string {
  if (reason === null) return "No se pudo conectar Google Calendar.";
  return CALENDAR_ERRORS[reason] ?? "No se pudo conectar Google Calendar.";
}

// D1.3/D1.4 — Helpers de autenticación con Google OAuth y bootstrap de CSRF.
//
// El flujo OAuth lo resuelve el backend (A1.3): el frontend solo redirige a
// `/auth/google/start` y deja que FastAPI genere `state` + PKCE, valide el
// `nonce`/issuer/audience y use el `sub` de Google como identidad (M1).
// Por eso el navegador nunca ve el client secret ni tokens de Google.
//
// El perfil + CSRF se obtienen con `fetchMe()` → `bootstrapCsrf()` (D1.4): el
// token vive solo en memoria y toda mutación lo inyecta vía `apiFetch`.
//
// Regla transversal (ADR-0006): la identidad la fija el estado autenticado del
// backend (cookie opaca `mw_session`), nunca un payload del cliente.
import { apiFetch, bootstrapCsrf } from "./api";
import { isRecord, readJson } from "./json";

/** Endpoint backend que inicia el login con Google (contrato M1). */
export const GOOGLE_AUTH_START_URL = "/auth/google/start";

/** Redirige al navegador al flujo de autorización de Google. */
export function startGoogleAuth(): void {
  window.location.assign(GOOGLE_AUTH_START_URL);
}

/** Perfil + bootstrap CSRF que entrega `GET /me` (A1.4/A1.8). */
export interface Me {
  id: string;
  email: string;
  display_name: string | null;
  timezone: string;
  csrf_token: string;
}

export type AccountDeletionState =
  | "pending"
  | "running"
  | "completed"
  | "completed_with_remote_failures";

export interface DeletionOperation {
  id: string;
  status: AccountDeletionState;
  delete_maulwurf_events: boolean;
  created_at: string;
  completed_at: string | null;
  expires_at: string;
  error_summary: string | null;
}

/** 401 is anonymous. Anything else that is not a profile is an outage, not a logout. */
export type SessionProbe =
  | { status: "authenticated"; me: Me }
  | { status: "anonymous" }
  | { status: "unavailable" };

/**
 * Consulta `GET /me` same-origin (cookie `mw_session`) y guarda el token CSRF en
 * memoria. No lanza: un 401 es `anonymous`; un fallo de red, un 5xx o un cuerpo
 * ilegible es `unavailable`. El `Cache-Control: no-store` lo garantiza el backend;
 * aquí además se fuerza `cache: "no-store"`.
 */
export async function fetchMe(): Promise<SessionProbe> {
  return bootstrapCsrf();
}

function isDeletionOperation(value: unknown): value is DeletionOperation {
  if (!isRecord(value)) return false;
  return (
    typeof value.id === "string" &&
    (value.status === "pending" ||
      value.status === "running" ||
      value.status === "completed" ||
      value.status === "completed_with_remote_failures") &&
    typeof value.delete_maulwurf_events === "boolean" &&
    typeof value.created_at === "string" &&
    (value.completed_at === null || typeof value.completed_at === "string") &&
    typeof value.expires_at === "string" &&
    (value.error_summary === null || typeof value.error_summary === "string")
  );
}

/** `PATCH /me` only updates IANA timezone (`MeUpdate` enforces `extra="forbid"`). */
export async function updateTimezone(timezone: string): Promise<Me> {
  const res = await apiFetch("/me", {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ timezone }),
    retries: 0,
  });
  const payload = await readJson(res);
  if (
    !isRecord(payload) ||
    typeof payload.id !== "string" ||
    typeof payload.email !== "string" ||
    typeof payload.timezone !== "string"
  ) {
    throw new Error("La respuesta de perfil no coincide con el contrato.");
  }
  return {
    id: payload.id,
    email: payload.email,
    display_name: typeof payload.display_name === "string" ? payload.display_name : null,
    timezone: payload.timezone,
    csrf_token: typeof payload.csrf_token === "string" ? payload.csrf_token : "",
  };
}

/** `DELETE /me` creates/returns an observable account deletion operation (202). */
export async function requestAccountDeletion(
  deleteMaulwurfEvents = false,
): Promise<{ deletion_operation_id: string }> {
  const res = await apiFetch("/me", {
    method: "DELETE",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ delete_maulwurf_events: deleteMaulwurfEvents }),
    retries: 0,
  });
  const payload = await readJson(res);
  if (
    !isRecord(payload) ||
    typeof payload.deletion_operation_id !== "string" ||
    payload.deletion_operation_id === ""
  ) {
    throw new Error("La respuesta de borrado de cuenta no coincide con el contrato.");
  }
  return { deletion_operation_id: payload.deletion_operation_id };
}

/** `GET /me/deletion/{id}` permitted for the restricted deletion session. */
export async function fetchDeletionStatus(deletionId: string): Promise<DeletionOperation> {
  const res = await apiFetch(`/me/deletion/${encodeURIComponent(deletionId)}`);
  const payload = await readJson(res);
  if (!isDeletionOperation(payload)) {
    throw new Error("El estado del borrado de cuenta no coincide con el contrato.");
  }
  return payload;
}

/** `POST /me/deletion/{id}/resume` retries a `pending`/`running` deletion. */
export async function resumeAccountDeletion(deletionId: string): Promise<DeletionOperation> {
  const res = await apiFetch(`/me/deletion/${encodeURIComponent(deletionId)}/resume`, {
    method: "POST",
    retries: 0,
  });
  const payload = await readJson(res);
  if (!isDeletionOperation(payload)) {
    throw new Error("El estado reanudado del borrado no coincide con el contrato.");
  }
  return payload;
}

/**
 * Revoca la sesión en el backend. Es una mutación: `apiFetch` inyecta
 * `X-CSRF-Token` (A1.4). El estado local lo limpia el `AuthProvider`.
 */
export async function postLogout(): Promise<void> {
  await apiFetch("/auth/logout", { method: "POST" });
}

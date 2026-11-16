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

/**
 * Revoca la sesión en el backend. Es una mutación: `apiFetch` inyecta
 * `X-CSRF-Token` (A1.4). El estado local lo limpia el `AuthProvider`.
 */
export async function postLogout(): Promise<void> {
  await apiFetch("/auth/logout", { method: "POST" });
}

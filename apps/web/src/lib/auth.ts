// D1.3 — Helpers de autenticación con Google OAuth.
//
// El flujo OAuth lo resuelve el backend (A1.3): el frontend solo redirige a
// `/auth/google/start` y deja que FastAPI genere `state` + PKCE, valide el
// `nonce`/issuer/audience y use el `sub` de Google como identidad (M1).
// Por eso el navegador nunca ve el client secret ni tokens de Google.
//
// Regla transversal (ADR-0006): la identidad la fija el estado autenticado del
// backend (cookie opaca `mw_session`), nunca un payload del cliente.

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

/**
 * Consulta `GET /me` same-origin (cookie `mw_session`). Devuelve `null` si no
 * hay sesión (401), si el endpoint no responde JSON o ante fallo de red: nunca
 * lanza para casos de autenticación. El `Cache-Control: no-store` lo garantiza
 * el backend; aquí además no se cachea.
 */
export async function fetchMe(): Promise<Me | null> {
  let res: Response;
  try {
    res = await fetch("/me", { cache: "no-store", credentials: "include" });
  } catch {
    return null;
  }
  if (!res.ok) return null;
  const contentType = res.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) return null;
  return (await res.json()) as Me;
}

/**
 * Revoca la sesión en el backend (mutación: exige `X-CSRF-Token` y Origin
 * same-origin, A1.4). El estado local lo limpia el `AuthProvider`.
 */
export async function postLogout(csrfToken: string | null): Promise<void> {
  await fetch("/auth/logout", {
    method: "POST",
    credentials: "include",
    headers: csrfToken ? { "X-CSRF-Token": csrfToken } : undefined,
  });
}

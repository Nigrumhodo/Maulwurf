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

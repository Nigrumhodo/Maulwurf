// D1.1 — Base del cliente HTTP. El contenido completo (CSRF en mutaciones,
// interceptores 401/403/429) es el ticket D1.4; aquí solo queda la superficie
// mínima para no acoplar el scaffold a una URL de API configurable.
//
// Regla transversal: el navegador nunca ve secretos ni audio. La identidad la
// fija el estado autenticado del backend (cookie opaca), nunca un payload del
// cliente ni la salida de un LLM (ADR-0006).

/** Mismo origen: Caddy enruta /auth/*, /me*, /subjects* y /audios* a la API. */
const SAME_ORIGIN = true;

export function apiUrl(path: string): string {
  if (!path.startsWith("/")) {
    throw new Error(`La ruta de API debe empezar por '/': ${path}`);
  }
  return SAME_ORIGIN ? path : path;
}

// D1.4 — Store de CSRF en memoria (U-S1-DA-01).
//
// El token CSRF se obtiene con `GET /me` (`cache: "no-store"`) y vive SOLO en
// memoria de la sesión: nunca en localStorage/sessionStorage (regla D1.4/D2.8,
// ADR-0006). `setCsrfToken` reemplaza el valor al rotar la sesión; el cierre de
// sesión lo borra con `clearCsrfToken`. Un valor obsoleto da 403 `csrf_invalid`.

let token: string | null = null;

export function getCsrfToken(): string | null {
  return token;
}

export function setCsrfToken(next: string | null): void {
  token = next;
}

export function clearCsrfToken(): void {
  token = null;
}

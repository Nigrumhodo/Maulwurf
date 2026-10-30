// D1.4 — Cliente HTTP same-origin con CSRF e interceptores (U-S1-DA-01).
//
// `apiFetch` es el único camino para hablar con la API desde el navegador:
// cookies same-origin (`credentials: "include"`), inyecta `X-CSRF-Token` en toda
// mutación y enruta los errores: `401 → redirigir a login`, `403 → mensaje de
// permisos`, `429 → reintento diferido respetando Retry-After` (acotado).
//
// El token se obtiene con `bootstrapCsrf()` (`GET /me`, `cache: "no-store"`) y se
// conserva solo en memoria (`lib/csrf.ts`). La identidad la fija el backend
// (cookie opaca), nunca un payload del cliente (ADR-0006).
import { clearCsrfToken, getCsrfToken, setCsrfToken } from "./csrf";
import type { Me } from "./auth";

/** Mismo origen: Caddy enruta /auth/*, /me, /subjects/* y /audios/* a la API. */
const SAME_ORIGIN = true;

export function apiUrl(path: string): string {
  if (!path.startsWith("/")) {
    throw new Error(`La ruta de API debe empezar por '/': ${path}`);
  }
  return SAME_ORIGIN ? path : path;
}

/** Cabecera CSRF que exige la API en toda mutación (A1.4). */
export const CSRF_HEADER = "X-CSRF-Token";

/** Métodos que mutan y, por contrato, exigen `X-CSRF-Token`. */
const MUTATING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

/** Tope defensivo del reintento: nunca esperar más de esto (guardrail 10). */
const MAX_RETRY_DELAY_MS = 30_000;

/** Reintentos extra permitidos ante 429 (además del intento original). */
const DEFAULT_RETRIES = 1;

/** Métodos que se pueden repetir sin crear un segundo efecto en el servidor. */
const RETRYABLE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

/** Techo del jitter para que un Retry-After grande no añada otra espera completa. */
const MAX_JITTER_MS = 1_000;

/** Envelope de error de la API: `{"error": {code, message, details}}` (DISENO §3.2). */
interface ApiErrorBody {
  code?: string;
  message?: string;
  details?: Record<string, unknown>;
}

/** Error tipado que lanza `apiFetch` ante una respuesta no 2xx. */
export class ApiHttpError extends Error {
  readonly status: number;
  readonly code: string | null;
  readonly details: Record<string, unknown>;

  constructor(status: number, body: ApiErrorBody | null) {
    super(body?.message ?? `Solicitud fallida (${status}).`);
    this.name = "ApiHttpError";
    this.status = status;
    this.code = body?.code ?? null;
    this.details = body?.details ?? {};
  }
}

/** Handlers de interceptor, inyectables para desacoplar el router (testeable). */
export interface ApiHandlers {
  onUnauthorized?: () => void;
  onForbidden?: (error: ApiHttpError) => void;
}

const handlers: ApiHandlers = {};

/** Configura el comportamiento de los interceptores (401/403). */
export function configureApiHandlers(next: ApiHandlers): void {
  handlers.onUnauthorized = next.onUnauthorized;
  handlers.onForbidden = next.onForbidden;
}

function redirectToLogin(): void {
  if (typeof window !== "undefined") {
    window.location.assign("/login");
  }
}

// Mensaje de permisos visible por defecto; D1.5 lo sustituye por un toast vía
// `configureApiHandlers`. En tests (sin `window`) cae al log.
function showForbiddenMessage(error: ApiHttpError): void {
  if (typeof window !== "undefined") {
    window.alert(error.message);
  } else {
    console.error(error);
  }
}

async function readErrorBody(res: Response): Promise<ApiErrorBody | null> {
  try {
    const contentType = res.headers.get("content-type") ?? "";
    if (!contentType.includes("application/json")) return null;
    const payload = (await res.json()) as { error?: ApiErrorBody };
    return payload.error ?? null;
  } catch {
    return null;
  }
}

/** Convierte `Retry-After` (segundos o HTTP-date) a milisegundos, o `null`. */
export function parseRetryAfter(value: string | null): number | null {
  if (value === null || value === "") return null;
  const seconds = Number(value);
  if (Number.isFinite(seconds) && seconds >= 0) return Math.ceil(seconds * 1000);
  const date = Date.parse(value);
  if (!Number.isNaN(date)) return Math.max(0, date - Date.now());
  return null;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export interface ApiRequestInit extends Omit<RequestInit, "headers"> {
  headers?: Record<string, string>;
  /** Reintentos extra ante 429 con `Retry-After` (por defecto 1). */
  retries?: number;
  /** Opt-in explícito para reintentar una mutación cuyo contrato es idempotente. */
  idempotent?: boolean;
}

/** Realiza una petición same-origin aplicando CSRF e interceptores. */
export async function apiFetch(path: string, init: ApiRequestInit = {}): Promise<Response> {
  const retries = init.retries ?? DEFAULT_RETRIES;
  const method = (init.method ?? "GET").toUpperCase();
  // Repeating a POST can create a second reservation: only retry reads unless the
  // caller proves the mutation is idempotent.
  const mayRetry = RETRYABLE_METHODS.has(method) || init.idempotent === true;
  const headers = new Headers(init.headers);
  if (MUTATING_METHODS.has(method)) {
    const token = getCsrfToken();
    if (token !== null) headers.set(CSRF_HEADER, token);
  }

  const res = await fetch(apiUrl(path), { ...init, headers, credentials: "include" });

  if (res.status === 429 && mayRetry) {
    const retryAfter = parseRetryAfter(res.headers.get("retry-after"));
    if (retryAfter !== null && retries > 0) {
      // Release the unread body before sleeping; otherwise the connection stays
      // allocated until the garbage collector runs.
      await res.body?.cancel();
      const base = Math.min(retryAfter, MAX_RETRY_DELAY_MS);
      // Jitter keeps every client from retrying in the same millisecond.
      const delay = base + Math.random() * Math.min(base * 0.2, MAX_JITTER_MS);
      if (delay > 0) await sleep(delay);
      return apiFetch(path, { ...init, retries: retries - 1 });
    }
  }

  if (res.ok) return res;

  const error = new ApiHttpError(res.status, await readErrorBody(res));

  if (res.status === 401) {
    clearCsrfToken();
    if (handlers.onUnauthorized) handlers.onUnauthorized();
    else redirectToLogin();
  } else if (res.status === 403) {
    if (handlers.onForbidden) handlers.onForbidden(error);
    else showForbiddenMessage(error);
  }

  throw error;
}

/**
 * Obtiene perfil + CSRF vía `GET /me` (`cache: "no-store"`) y guarda el token en
 * memoria. Devuelve `null` sin lanzar si no hay sesión (401), si la respuesta no
 * es JSON o ante fallo de red.
 */
export async function bootstrapCsrf(): Promise<Me | null> {
  let res: Response;
  try {
    res = await fetch("/me", { cache: "no-store", credentials: "include" });
  } catch {
    clearCsrfToken();
    return null;
  }
  if (!res.ok) {
    clearCsrfToken();
    return null;
  }
  const contentType = res.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) {
    clearCsrfToken();
    return null;
  }
  const me = (await res.json()) as Me;
  setCsrfToken(me.csrf_token);
  return me;
}

// D1.4 — Cliente HTTP same-origin con CSRF e interceptores (U-S1-DA-01).
//
// `apiFetch` es el único camino para hablar con la API desde el navegador:
// cookies same-origin (`credentials: "include"`), inyecta `X-CSRF-Token` en toda
// mutación y enruta los errores: `401` redirige a login, `403` se lanza para que
// lo muestre quien hizo la llamada (una sola vez), `429` reintenta lecturas
// respetando `Retry-After` y `AbortSignal`.
//
// El token se obtiene con `bootstrapCsrf()` (`GET /me`, `cache: "no-store"`) y se
// conserva solo en memoria (`lib/csrf.ts`). La identidad la fija el backend
// (cookie opaca), nunca un payload del cliente (ADR-0006).
import i18n from "@/i18n";
import { clearCsrfToken, getCsrfToken, setCsrfToken } from "./csrf";
import { isRecord } from "./json";
import type { Me, SessionProbe } from "./auth";

const EXTERNAL_SCHEME = /^[a-zA-Z][a-zA-Z0-9+.-]*:/;

/**
 * Same-origin relative path only. A single leading slash is not enough:
 * `//host` and `/\host` are cross-origin, and a scheme would send the CSRF
 * header off-origin. Callers must pass a path, never a URL.
 */
export function isSameOriginApiPath(path: string): boolean {
  if (!path.startsWith("/")) return false;
  if (path.startsWith("//")) return false;
  if (path.includes("\\")) return false;
  if (EXTERNAL_SCHEME.test(path)) return false;
  return true;
}

export function apiUrl(path: string): string {
  if (!isSameOriginApiPath(path)) {
    throw new Error(`La ruta de API debe ser relativa del mismo origen: ${path}`);
  }
  return path;
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
export interface ParsedApiError {
  code?: string;
  message?: string;
  details?: Record<string, unknown> | unknown[];
}

/** `409 duplicate_variant`: the only error that carries a one-time confirmation token. */
export interface DuplicateVariantDetails {
  existing_audio_id: string;
  variant_confirmation_token: string;
  expires_at: string;
}

/** Extrae el envelope; `null` si el cuerpo no lo tiene. Un code no-string se descarta. */
export function parseErrorEnvelope(body: unknown): ParsedApiError | null {
  if (!isRecord(body)) return null;
  const error = body.error;
  if (!isRecord(error)) return null;
  let details: ParsedApiError["details"] = undefined;
  if (isRecord(error.details) || Array.isArray(error.details)) {
    details = error.details;
  }
  return {
    code: typeof error.code === "string" ? error.code : undefined,
    message: typeof error.message === "string" ? error.message : undefined,
    details,
  };
}

/** Mensaje de un campo: string, o el primer string de un array. */
export function fieldMessage(details: unknown, field: string): string | undefined {
  if (!isRecord(details)) return undefined;
  const value = details[field];
  if (typeof value === "string") return value;
  if (Array.isArray(value) && typeof value[0] === "string") return value[0];
  return undefined;
}

function isMe(value: unknown): value is Me {
  if (!isRecord(value)) return false;
  return (
    typeof value.id === "string" &&
    typeof value.email === "string" &&
    (value.display_name === null || typeof value.display_name === "string") &&
    typeof value.timezone === "string" &&
    typeof value.csrf_token === "string" &&
    value.csrf_token !== ""
  );
}

/** Error tipado que lanza `apiFetch` ante una respuesta no 2xx. */
export class ApiHttpError extends Error {
  readonly status: number;
  readonly code: string | null;
  readonly details: Record<string, unknown>;

  constructor(status: number, body: ParsedApiError | null) {
    super(body?.message ?? i18n.t("common:errors.requestFailed", { status }));
    this.name = "ApiHttpError";
    this.status = status;
    this.code = body?.code ?? null;
    const details = body?.details;
    this.details = isRecord(details) ? details : {};
  }

  /** First `details.fields` entry whose value is a string or a list of strings. */
  firstFieldMessage(): { field: string; message: string } | null {
    const fields = this.details.fields;
    if (!isRecord(fields)) return null;
    for (const [field, value] of Object.entries(fields)) {
      const message = fieldMessage({ [field]: value }, field);
      if (message !== undefined) return { field, message };
    }
    return null;
  }

  /** `details[key]` when every element is a string. Mixed arrays are not coerced. */
  stringList(key: string): string[] | null {
    const value = this.details[key];
    if (!Array.isArray(value)) return null;
    const items: string[] = [];
    for (const item of value) {
      if (typeof item !== "string") return null;
      items.push(item);
    }
    return items;
  }

  duplicateVariant(): DuplicateVariantDetails | null {
    const existingAudioId = this.details.existing_audio_id;
    const token = this.details.variant_confirmation_token;
    const expiresAt = this.details.expires_at;
    if (
      typeof existingAudioId !== "string" ||
      existingAudioId === "" ||
      typeof token !== "string" ||
      token === "" ||
      typeof expiresAt !== "string" ||
      expiresAt === ""
    ) {
      return null;
    }
    return {
      existing_audio_id: existingAudioId,
      variant_confirmation_token: token,
      expires_at: expiresAt,
    };
  }
}

/** Handlers de interceptor, inyectables para desacoplar el router (testeable). */
export interface ApiHandlers {
  onUnauthorized?: () => void;
  /** Optional side effect. The caller that caught the error owns the message. */
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

async function readErrorBody(res: Response): Promise<ParsedApiError | null> {
  const contentType = res.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) return null;
  try {
    const payload: unknown = await res.json();
    return parseErrorEnvelope(payload);
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

/** Reason carried by `AbortSignal`, or a DOM `AbortError` when the signal has none. */
export function abortError(signal: AbortSignal): unknown {
  if (signal.reason !== undefined) return signal.reason;
  return new DOMException("The operation was aborted.", "AbortError");
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(abortError(signal));
      return;
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    function onAbort() {
      clearTimeout(timer);
      reject(
        signal === undefined
          ? new DOMException("The operation was aborted.", "AbortError")
          : abortError(signal),
      );
    }
    signal?.addEventListener("abort", onAbort, { once: true });
  });
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
  const signal = init.signal ?? undefined;

  // D2.8: every API response is tenant-private. This override is deliberate: a caller
  // must not be able to opt into browser caching for private class or chat data.
  const res = await fetch(apiUrl(path), {
    ...init,
    headers,
    credentials: "include",
    cache: "no-store",
  });

  if (res.status === 429 && mayRetry) {
    const retryAfter = parseRetryAfter(res.headers.get("retry-after"));
    if (retryAfter !== null && retries > 0) {
      // Release the unread body before sleeping; otherwise the connection stays
      // allocated until the garbage collector runs.
      await res.body?.cancel();
      const base = Math.min(retryAfter, MAX_RETRY_DELAY_MS);
      // Jitter keeps every client from retrying in the same millisecond.
      const delay = base + Math.random() * Math.min(base * 0.2, MAX_JITTER_MS);
      if (signal?.aborted) throw abortError(signal);
      if (delay > 0) await sleep(delay, signal);
      return apiFetch(path, { ...init, retries: retries - 1 });
    }
  }

  if (res.ok) return res;

  const error = new ApiHttpError(res.status, await readErrorBody(res));
  return handleApiHttpError(error);
}

/** Aplica los interceptores compartidos de 401/403 y relanza el `ApiHttpError`. */
export function handleApiHttpError(error: ApiHttpError): never {
  if (error.status === 401) {
    clearCsrfToken();
    if (handlers.onUnauthorized) handlers.onUnauthorized();
    else redirectToLogin();
  } else if (error.status === 403) {
    // The screen that issued the call renders this error. A second toast would repeat it.
    handlers.onForbidden?.(error);
  }

  throw error;
}

/**
 * `GET /me` (`cache: "no-store"`). `anonymous` is only a 401. Network failures,
 * 5xx and a body that is not a profile are `unavailable`, so the gate can finish
 * without sending a signed-in user to login.
 */
export async function bootstrapCsrf(): Promise<SessionProbe> {
  let res: Response;
  try {
    res = await fetch("/me", { cache: "no-store", credentials: "include" });
  } catch {
    clearCsrfToken();
    return { status: "unavailable" };
  }
  if (res.status === 401) {
    clearCsrfToken();
    return { status: "anonymous" };
  }
  if (!res.ok) {
    clearCsrfToken();
    return { status: "unavailable" };
  }
  const contentType = res.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) {
    clearCsrfToken();
    return { status: "unavailable" };
  }
  let payload: unknown;
  try {
    payload = await res.json();
  } catch {
    clearCsrfToken();
    return { status: "unavailable" };
  }
  if (!isMe(payload)) {
    clearCsrfToken();
    return { status: "unavailable" };
  }
  setCsrfToken(payload.csrf_token);
  return { status: "authenticated", me: payload };
}

// D4.5 / D4.8 — PWA shell-only service worker registration and G1 exclusion guard.
// Private API data, uploads, transcripts, search, chat, SSE, and auth must NEVER enter
// browser CacheStorage.

export const PRIVATE_PATH_PREFIXES = [
  "/auth",
  "/me",
  "/audios",
  "/subjects",
  "/tasks",
  "/calendar",
  "/chat",
  "/search",
  "/notifications",
  "/integrations",
  "/ingestion",
  "/internal",
  "/metrics",
  "/healthz",
  "/readyz",
] as const;

/** Exact shell URLs the service worker may cache. Keep in sync with public/sw.js. */
export const CACHEABLE_EXACT_PATHS = [
  "/",
  "/index.html",
  "/manifest.webmanifest",
  "/icon.svg",
] as const;

/** Extensions allowed under `/assets/`. Anything else stays uncached. */
export const CACHEABLE_ASSET_EXTENSIONS = [".js", ".css", ".webp", ".svg", ".png", ".woff2"] as const;

export interface CacheRequestHints {
  cache?: string;
  /** Present when the request carries an Authorization header, including an empty value. */
  authorization?: string | null;
  accept?: string | null;
}

export function isPrivateOrApiPath(pathname: string): boolean {
  return PRIVATE_PATH_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}

export function isAllowlistedShellPath(pathname: string): boolean {
  if (CACHEABLE_EXACT_PATHS.some((path) => path === pathname)) return true;
  if (!pathname.startsWith("/assets/")) return false;
  const dot = pathname.lastIndexOf(".");
  if (dot < 0) return false;
  const extension = pathname.slice(dot).toLowerCase();
  return CACHEABLE_ASSET_EXTENSIONS.some((allowed) => allowed === extension);
}

export function isCacheableRequest(
  requestUrl: string,
  method = "GET",
  origin = "https://localhost",
  hints: CacheRequestHints = {},
): boolean {
  if (method.toUpperCase() !== "GET") return false;
  if (hints.cache === "no-store") return false;
  if (hints.authorization !== undefined && hints.authorization !== null) return false;
  if ((hints.accept ?? "").toLowerCase().includes("text/event-stream")) return false;
  let parsed: URL;
  try {
    parsed = new URL(requestUrl, origin);
  } catch {
    return false;
  }
  if (parsed.origin !== origin) return false;
  if (isPrivateOrApiPath(parsed.pathname)) return false;
  return isAllowlistedShellPath(parsed.pathname);
}

/** A stored response must be a successful basic response that is neither no-store nor private. */
export function isCacheableResponse(input: {
  ok: boolean;
  type: string;
  cacheControl: string | null;
}): boolean {
  if (!input.ok || input.type !== "basic") return false;
  const cacheControl = (input.cacheControl ?? "").toLowerCase();
  return !cacheControl.includes("no-store") && !cacheControl.includes("private");
}

export async function registerServiceWorker(): Promise<ServiceWorkerRegistration | null> {
  if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) {
    return null;
  }
  try {
    return await navigator.serviceWorker.register("/sw.js", { scope: "/" });
  } catch {
    return null;
  }
}

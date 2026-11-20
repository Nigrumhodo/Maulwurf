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

export function isCacheableRequest(
  requestUrl: string,
  method = "GET",
  origin = "https://localhost",
): boolean {
  if (method.toUpperCase() !== "GET") return false;
  let parsed: URL;
  try {
    parsed = new URL(requestUrl, origin);
  } catch {
    return false;
  }
  if (parsed.origin !== origin) return false;
  const path = parsed.pathname;
  return !PRIVATE_PATH_PREFIXES.some(
    (prefix) => path === prefix || path.startsWith(`${prefix}/`),
  );
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

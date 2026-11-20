/* global self, caches, fetch, URL */
// D4.5 / D4.8 — Static-shell-only Service Worker for Maulwurf.
// Explicitly refuses to cache or intercept any private API endpoint, upload, SSE stream,
// transcript, search, chat, Calendar, or notification route (G1 / privacy invariant).

const CACHE_NAME = "maulwurf-shell-v1";
const SHELL_URLS = ["/", "/manifest.webmanifest", "/icon.svg"];

const PRIVATE_PATH_PREFIXES = [
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
];

function isPrivateOrApiPath(pathname) {
  return PRIVATE_PATH_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_URLS)).catch(() => undefined),
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;
  if (request.cache === "only-if-cached" && request.mode !== "same-origin") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (isPrivateOrApiPath(url.pathname)) return;

  const accept = request.headers.get("accept") || "";
  if (accept.includes("text/event-stream")) return;

  event.respondWith(
    fetch(request)
      .then((response) => {
        const cacheControl = (response.headers.get("cache-control") || "").toLowerCase();
        if (response.ok && response.type === "basic" && !cacheControl.includes("no-store")) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, clone)).catch(() => undefined);
        }
        return response;
      })
      .catch(() => caches.match(request).then((cached) => cached || caches.match("/"))),
  );
});

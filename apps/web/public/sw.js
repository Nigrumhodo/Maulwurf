/* global self, caches, fetch, URL */
// D4.5 / D4.8 — Static-shell-only Service Worker for Maulwurf.
// Cache only the public shell and hashed static assets (allowlist). The private
// prefix list is a second gate. Keep this aligned with src/lib/pwa.ts.
// v2 drops entries the previous denylist policy may have stored.

const CACHE_NAME = "maulwurf-shell-v2";
const PRECACHE_URLS = ["/", "/manifest.webmanifest", "/icon.svg"];
const CACHEABLE_EXACT = new Set(["/", "/index.html", "/manifest.webmanifest", "/icon.svg"]);
const CACHEABLE_ASSET_EXTENSIONS = [".js", ".css", ".webp", ".svg", ".png", ".woff2"];

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

function isAllowlistedShellPath(pathname) {
  if (CACHEABLE_EXACT.has(pathname)) return true;
  if (!pathname.startsWith("/assets/")) return false;
  const dot = pathname.lastIndexOf(".");
  if (dot < 0) return false;
  return CACHEABLE_ASSET_EXTENSIONS.includes(pathname.slice(dot).toLowerCase());
}

function responseIsCacheable(response) {
  if (!response.ok || response.type !== "basic") return false;
  const cacheControl = (response.headers.get("cache-control") || "").toLowerCase();
  return !cacheControl.includes("no-store") && !cacheControl.includes("private");
}

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) => cache.addAll(PRECACHE_URLS))
      .catch(() => undefined),
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
  // The API client sets cache: "no-store". Do not intercept or store those.
  if (request.cache === "no-store") return;
  if (request.headers.has("authorization")) return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (isPrivateOrApiPath(url.pathname)) return;
  if (!isAllowlistedShellPath(url.pathname)) return;

  const accept = request.headers.get("accept") || "";
  if (accept.includes("text/event-stream")) return;

  event.respondWith(
    fetch(request)
      .then((response) => {
        if (responseIsCacheable(response)) {
          const clone = response.clone();
          caches
            .open(CACHE_NAME)
            .then((cache) => cache.put(request, clone))
            .catch(() => undefined);
        }
        return response;
      })
      .catch(() => caches.match(request).then((cached) => cached || caches.match("/"))),
  );
});

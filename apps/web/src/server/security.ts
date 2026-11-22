// Delivery headers for the static shell. Google login is a full navigation
// (`window.location.assign` in lib/auth.ts), not `window.open`, so COOP can
// stay `same-origin` without a popup exception.

/** One year, including subdomains. No `preload` (that list is hard to leave). */
export const HSTS_VALUE = "max-age=31536000; includeSubDomains";

// FullCalendar injects runtime <style> rules (`createElement("style")` and
// `CSSStyleSheet.insertRule`). `style-src` stays `'self'`; `style-src-elem`
// carries the `'unsafe-inline'` that injection needs. `style-src-attr` is
// `'none'` because React `style={{}}` writes the CSSOM, which this directive
// does not govern. `font-src` allows `'self'` and `data:` because FullCalendar's
// icon face is an embedded data URL in that stylesheet; it does not contact a
// third-party font host.
export const CONTENT_SECURITY_POLICY = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self'",
  "style-src-elem 'self' 'unsafe-inline'",
  "style-src-attr 'none'",
  "font-src 'self' data:",
  "img-src 'self' data:",
  "connect-src 'self'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

/** First X-Forwarded-Proto hop is the client scheme (a proxy may append its own). */
export function forwardedProtoIsHttps(forwardedProto: string | undefined): boolean {
  if (forwardedProto === undefined) return false;
  const first = forwardedProto.split(",")[0]?.trim().toLowerCase();
  return first === "https";
}

/**
 * HSTS only when the client already used HTTPS, or when this process is the
 * production image. Local HTTP tests keep `NODE_ENV` unset/`test`, so they
 * do not pin the browser to HTTPS.
 */
export function shouldSendHsts(
  secure: boolean,
  forwardedProto: string | undefined,
  nodeEnv: string | undefined,
): boolean {
  return nodeEnv === "production" || secure || forwardedProtoIsHttps(forwardedProto);
}

export function securityHeaders(
  secure: boolean,
  forwardedProto: string | undefined,
  nodeEnv: string | undefined,
): Record<string, string> {
  const headers: Record<string, string> = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "camera=(), geolocation=(), microphone=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
  };
  if (shouldSendHsts(secure, forwardedProto, nodeEnv)) {
    headers["Strict-Transport-Security"] = HSTS_VALUE;
  }
  return headers;
}

export type SpaFallback = "html" | "method_not_allowed" | "not_found";

/** Missing hashed assets and `/api/*` must not be disguised as the SPA shell. */
export function spaFallback(method: string, pathname: string): SpaFallback {
  const normalized = method.toUpperCase();
  if (normalized !== "GET" && normalized !== "HEAD") return "method_not_allowed";
  if (
    pathname === "/assets" ||
    pathname.startsWith("/assets/") ||
    pathname === "/api" ||
    pathname.startsWith("/api/")
  ) {
    return "not_found";
  }
  return "html";
}

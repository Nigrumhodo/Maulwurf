import type { Server } from "node:http";
import type { AddressInfo } from "node:net";
import process from "node:process";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { createApp } from "@/server/index";
import {
  CONTENT_SECURITY_POLICY,
  HSTS_VALUE,
  securityHeaders,
  shouldSendHsts,
  spaFallback,
} from "@/server/security";

function directive(policy: string, name: string): string {
  const part = policy.split("; ").find((item) => item.startsWith(`${name} `));
  if (part === undefined) throw new Error(`missing CSP directive ${name}`);
  return part.slice(name.length).trim();
}

describe("web security policy", () => {
  it("keeps the CSP strict and off Google Fonts", () => {
    expect(CONTENT_SECURITY_POLICY).not.toContain("fonts.googleapis.com");
    expect(CONTENT_SECURITY_POLICY).not.toContain("fonts.gstatic.com");
    expect(directive(CONTENT_SECURITY_POLICY, "script-src")).toBe("'self'");
    expect(directive(CONTENT_SECURITY_POLICY, "object-src")).toBe("'none'");
    expect(directive(CONTENT_SECURITY_POLICY, "frame-ancestors")).toBe("'none'");
    expect(directive(CONTENT_SECURITY_POLICY, "base-uri")).toBe("'self'");
    expect(directive(CONTENT_SECURITY_POLICY, "form-action")).toBe("'self'");
    expect(directive(CONTENT_SECURITY_POLICY, "font-src")).toBe("'self' data:");
    expect(directive(CONTENT_SECURITY_POLICY, "style-src")).toBe("'self'");
    // FullCalendar injects runtime <style> rules, so only element styles stay inline.
    expect(directive(CONTENT_SECURITY_POLICY, "style-src-elem")).toBe("'self' 'unsafe-inline'");
    expect(directive(CONTENT_SECURITY_POLICY, "style-src-attr")).toBe("'none'");
  });

  it("emits HSTS for HTTPS, a forwarded https hop, or production", () => {
    expect(shouldSendHsts(false, undefined, "test")).toBe(false);
    expect(shouldSendHsts(false, "http", "development")).toBe(false);
    expect(shouldSendHsts(true, undefined, "test")).toBe(true);
    expect(shouldSendHsts(false, "https", "test")).toBe(true);
    expect(shouldSendHsts(false, "HTTPS", "test")).toBe(true);
    expect(shouldSendHsts(false, "https, http", "test")).toBe(true);
    expect(shouldSendHsts(false, undefined, "production")).toBe(true);

    const local = securityHeaders(false, undefined, "test");
    expect(local["Strict-Transport-Security"]).toBeUndefined();
    expect(local["Cross-Origin-Opener-Policy"]).toBe("same-origin");
    expect(local["Cross-Origin-Resource-Policy"]).toBe("same-origin");
    expect(local["Content-Security-Policy"]).toBe(CONTENT_SECURITY_POLICY);
    expect(securityHeaders(false, "https", "test")["Strict-Transport-Security"]).toBe(HSTS_VALUE);
    expect(securityHeaders(false, undefined, "production")["Strict-Transport-Security"]).toBe(
      HSTS_VALUE,
    );
  });

  it("limits the SPA fallback to GET and HEAD", () => {
    expect(spaFallback("GET", "/login")).toBe("html");
    expect(spaFallback("HEAD", "/gestion/biblioteca")).toBe("html");
    expect(spaFallback("get", "/")).toBe("html");
    expect(spaFallback("POST", "/login")).toBe("method_not_allowed");
    expect(spaFallback("PUT", "/assets/app.js")).toBe("method_not_allowed");
    expect(spaFallback("DELETE", "/api/me")).toBe("method_not_allowed");
    expect(spaFallback("GET", "/assets/missing.js")).toBe("not_found");
    expect(spaFallback("HEAD", "/assets")).toBe("not_found");
    expect(spaFallback("GET", "/api")).toBe("not_found");
    expect(spaFallback("GET", "/api/health")).toBe("not_found");
    expect(spaFallback("GET", "/assets-not-the-prefix")).toBe("html");
  });
});

describe("static server delivery", () => {
  let server: Server;
  let base = "";
  const previousNodeEnv = process.env.NODE_ENV;

  beforeAll(async () => {
    process.env.NODE_ENV = "test";
    const app = createApp();
    await new Promise<void>((resolve) => {
      server = app.listen(0, "127.0.0.1", () => resolve());
    });
    const address = server.address() as AddressInfo;
    base = `http://127.0.0.1:${address.port}`;
  });

  afterAll(async () => {
    if (previousNodeEnv === undefined) delete process.env.NODE_ENV;
    else process.env.NODE_ENV = previousNodeEnv;
    if (server === undefined) return;
    await new Promise<void>((resolve, reject) => {
      server.close((error) => (error ? reject(error) : resolve()));
    });
  });

  it("serves the shell without Google Fonts or HSTS on local HTTP", async () => {
    const res = await fetch(`${base}/`);
    const html = await res.text();
    expect(res.status).toBe(200);
    expect(html).toContain('id="root"');
    expect(html).not.toContain("fonts.googleapis.com");
    expect(html).not.toContain("fonts.gstatic.com");
    expect(res.headers.get("strict-transport-security")).toBeNull();
    expect(res.headers.get("content-security-policy")).toBe(CONTENT_SECURITY_POLICY);
    expect(res.headers.get("cross-origin-opener-policy")).toBe("same-origin");
    expect(res.headers.get("cross-origin-resource-policy")).toBe("same-origin");
    expect(res.headers.get("x-frame-options")).toBe("DENY");
    expect(res.headers.get("x-powered-by")).toBeNull();
  });

  it("emits HSTS when the proxy marks the client hop as https", async () => {
    const res = await fetch(`${base}/icon.svg`, {
      headers: { "x-forwarded-proto": "https" },
    });
    expect(res.status).toBe(200);
    expect(res.headers.get("strict-transport-security")).toBe(HSTS_VALUE);
  });

  it("emits HSTS in production even for a cleartext healthcheck", async () => {
    process.env.NODE_ENV = "production";
    try {
      const res = await fetch(`${base}/`);
      expect(res.status).toBe(200);
      expect(res.headers.get("strict-transport-security")).toBe(HSTS_VALUE);
    } finally {
      process.env.NODE_ENV = "test";
    }
  });

  it("returns 405 for other methods and 404 for missing /assets and /api paths", async () => {
    const post = await fetch(`${base}/login`, {
      method: "POST",
      headers: { accept: "text/html" },
    });
    expect(post.status).toBe(405);
    expect(post.headers.get("allow")).toBe("GET, HEAD");
    await expect(post.json()).resolves.toEqual({ error: { code: "method_not_allowed" } });

    const asset = await fetch(`${base}/assets/does-not-exist.js`, {
      headers: { accept: "text/html" },
    });
    expect(asset.status).toBe(404);
    const assetBody = await asset.text();
    expect(assetBody).not.toContain("<html");
    expect(assetBody).toContain("not_found");

    const api = await fetch(`${base}/api/health`);
    expect(api.status).toBe(404);
    await expect(api.json()).resolves.toEqual({ error: { code: "not_found" } });

    const page = await fetch(`${base}/gestion/biblioteca`);
    expect(page.status).toBe(200);
    expect(await page.text()).toContain('id="root"');

    const head = await fetch(`${base}/login`, { method: "HEAD" });
    expect(head.status).toBe(200);
  });

  it("serves robots.txt without listing private routes", async () => {
    const res = await fetch(`${base}/robots.txt`);
    const body = await res.text();
    expect(res.status).toBe(200);
    expect(body).toContain("User-agent: *");
    expect(body).toContain("Disallow: /");
    expect(body).not.toContain("/biblioteca");
    expect(body).not.toContain("/gestion");
    expect(body).not.toContain("/chat");
    expect(body).not.toContain("/settings");
    expect(body).not.toContain("/calendario");
    expect(body).not.toContain("/auth/");
  });
});

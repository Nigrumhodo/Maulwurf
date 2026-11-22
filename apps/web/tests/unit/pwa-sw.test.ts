import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  CACHEABLE_ASSET_EXTENSIONS,
  CACHEABLE_EXACT_PATHS,
  PRIVATE_PATH_PREFIXES,
  isCacheableRequest,
  isCacheableResponse,
} from "@/lib/pwa";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SW_PATH = path.resolve(HERE, "../../public/sw.js");
const MANIFEST_PATH = path.resolve(HERE, "../../public/manifest.webmanifest");

describe("PWA shell-only caching guard (D4.5 / D4.8)", () => {
  it("excludes every private and API prefix from caching", () => {
    for (const prefix of PRIVATE_PATH_PREFIXES) {
      expect(isCacheableRequest(prefix, "GET")).toBe(false);
      expect(isCacheableRequest(`${prefix}/subresource`, "GET")).toBe(false);
    }
  });

  it("rejects non-GET and cross-origin requests while allowing static shell assets", () => {
    expect(isCacheableRequest("/manifest.webmanifest", "POST")).toBe(false);
    expect(isCacheableRequest("/audios/123", "PUT")).toBe(false);
    expect(isCacheableRequest("https://evil.example/app.js", "GET")).toBe(false);

    expect(isCacheableRequest("/", "GET")).toBe(true);
    expect(isCacheableRequest("/index.html", "GET")).toBe(true);
    expect(isCacheableRequest("/manifest.webmanifest", "GET")).toBe(true);
    expect(isCacheableRequest("/icon.svg", "GET")).toBe(true);
    expect(isCacheableRequest("/assets/index-123.js", "GET")).toBe(true);
    for (const extension of CACHEABLE_ASSET_EXTENSIONS) {
      expect(isCacheableRequest(`/assets/shell${extension}`, "GET")).toBe(true);
    }
  });

  it("caches only the shell allowlist and honors no-store, Authorization, and private responses", () => {
    expect(isCacheableRequest("/documents/x", "GET")).toBe(false);
    expect(isCacheableRequest("/login", "GET")).toBe(false);
    expect(isCacheableRequest("/assets/secret.json", "GET")).toBe(false);
    expect(isCacheableRequest("/assets/app.js.map", "GET")).toBe(false);
    expect(isCacheableRequest("/", "GET", "https://localhost", { cache: "no-store" })).toBe(false);
    expect(
      isCacheableRequest("/", "GET", "https://localhost", { authorization: "Bearer secret" }),
    ).toBe(false);
    expect(isCacheableRequest("/", "GET", "https://localhost", { authorization: "" })).toBe(false);
    expect(
      isCacheableRequest("/assets/app.js", "GET", "https://localhost", {
        accept: "text/event-stream",
      }),
    ).toBe(false);

    expect(isCacheableResponse({ ok: true, type: "basic", cacheControl: "public, max-age=3600" })).toBe(
      true,
    );
    expect(isCacheableResponse({ ok: true, type: "basic", cacheControl: "no-store" })).toBe(false);
    expect(isCacheableResponse({ ok: true, type: "basic", cacheControl: "private, no-cache" })).toBe(
      false,
    );
    expect(isCacheableResponse({ ok: false, type: "basic", cacheControl: "public" })).toBe(false);
    expect(isCacheableResponse({ ok: true, type: "cors", cacheControl: "public" })).toBe(false);
  });

  it("ships sw.js and manifest.webmanifest with matching exclusion rules", () => {
    const swSource = fs.readFileSync(SW_PATH, "utf8");
    for (const prefix of PRIVATE_PATH_PREFIXES) {
      expect(swSource).toContain(`"${prefix}"`);
    }
    expect(swSource).toContain('request.cache === "no-store"');
    expect(swSource).toContain('request.headers.has("authorization")');
    expect(swSource).toContain("text/event-stream");
    expect(swSource).toContain('cacheControl.includes("no-store")');
    expect(swSource).toContain('cacheControl.includes("private")');
    for (const exact of CACHEABLE_EXACT_PATHS) {
      expect(swSource).toContain(`"${exact}"`);
    }
    for (const extension of CACHEABLE_ASSET_EXTENSIONS) {
      expect(swSource).toContain(`"${extension}"`);
    }
    expect(swSource).toContain('pathname.startsWith("/assets/")');

    const manifest = JSON.parse(fs.readFileSync(MANIFEST_PATH, "utf8")) as {
      name?: string;
      display?: string;
    };
    expect(manifest.name).toContain("Maulwurf");
    expect(manifest.display).toBe("standalone");
  });
});

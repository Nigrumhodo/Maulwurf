import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { PRIVATE_PATH_PREFIXES, isCacheableRequest } from "@/lib/pwa";

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
    expect(isCacheableRequest("/manifest.webmanifest", "GET")).toBe(true);
    expect(isCacheableRequest("/icon.svg", "GET")).toBe(true);
    expect(isCacheableRequest("/assets/index-123.js", "GET")).toBe(true);
  });

  it("ships sw.js and manifest.webmanifest with matching exclusion rules", () => {
    const swSource = fs.readFileSync(SW_PATH, "utf8");
    for (const prefix of PRIVATE_PATH_PREFIXES) {
      expect(swSource).toContain(`"${prefix}"`);
    }
    expect(swSource).toContain("no-store");
    expect(swSource).toContain("text/event-stream");

    const manifest = JSON.parse(fs.readFileSync(MANIFEST_PATH, "utf8")) as {
      name?: string;
      display?: string;
    };
    expect(manifest.name).toContain("Maulwurf");
    expect(manifest.display).toBe("standalone");
  });
});

import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const srcDir = resolve(__dirname, "../../src");
const css = readFileSync(resolve(srcDir, "index.css"), "utf8");
const fontUrls = [...css.matchAll(/url\("(\.\/fonts\/[^"]+)"\)/g)].map((m) => m[1]);

describe("self-hosted retro fonts", () => {
  it("declares VT323 and Fira Code faces backed by files in src/fonts", () => {
    expect(css).toMatch(/font-family: "VT323";/);
    expect(css).toMatch(/font-family: "Fira Code";/);
    expect(fontUrls.length).toBeGreaterThanOrEqual(2);
    for (const url of fontUrls) {
      expect(existsSync(resolve(srcDir, url)), url).toBe(true);
    }
  });

  it("puts the retro faces first with a system monospace fallback", () => {
    expect(css).toMatch(/--font-pixel: "VT323", ui-monospace,/);
    expect(css).toMatch(/--font-code: "Fira Code", ui-monospace,/);
  });

  it("never loads fonts from a third-party origin", () => {
    expect(css).not.toMatch(/@font-face[^}]*url\("?https?:/);
    expect(css).not.toContain("fonts.googleapis.com");
    expect(css).not.toContain("fonts.gstatic.com");
  });
});

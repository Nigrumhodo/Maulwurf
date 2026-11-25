import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SRC_ROOT = path.resolve(HERE, "../../src");
const SW_PATH = path.resolve(HERE, "../../public/sw.js");

function collectSourceFiles(dir: string): string[] {
  const entries = fs.readdirSync(dir, { withFileTypes: true });
  const files: string[] = [];
  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      files.push(...collectSourceFiles(full));
    } else if (/\.(ts|tsx)$/.test(entry.name)) {
      files.push(full);
    }
  }
  return files;
}

function stripComments(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/\/\/[^\n]*/g, "");
}

describe("Client G1 privacy invariants (D4.8)", () => {
  it("keeps zero audio playback, object URLs, IndexedDB, or persistent storage of private data", () => {
    const files = collectSourceFiles(SRC_ROOT);
    expect(files.length).toBeGreaterThan(20);

    for (const file of files) {
      const rel = path.relative(SRC_ROOT, file).replace(/\\/g, "/");
      const content = stripComments(fs.readFileSync(file, "utf8"));

      expect(content, `${rel} must never render an <audio> player`).not.toMatch(/<audio[\s>]/i);
      if (content.includes("URL.createObjectURL")) {
        expect(rel, "only text export helper may allocate transient Blob object URLs").toBe(
          "lib/reader.ts",
        );
        expect(content, `${rel} must always revoke created object URLs`).toContain(
          "URL.revokeObjectURL",
        );
      }
      expect(content, `${rel} must never use IndexedDB`).not.toMatch(/\bindexedDB\b/);

      // Only non-private UI preferences may persist: the theme and the UI language.
      if (rel !== "lib/theme.ts" && rel !== "i18n/index.ts") {
        expect(content, `${rel} must not persist state in localStorage`).not.toMatch(
          /\blocalStorage\b/,
        );
      }
      expect(content, `${rel} must not use sessionStorage`).not.toMatch(/\bsessionStorage\b/);
    }

    const swSource = fs.readFileSync(SW_PATH, "utf8");
    expect(swSource).toContain('"/audios"');
    expect(swSource).toContain('"/chat"');
    expect(swSource).toContain('"/search"');
    expect(swSource).toContain('"/notifications"');
    expect(swSource).toContain("no-store");
  });

  it("does not contact Google Fonts from the shell, CSS, or CSP", () => {
    const files = [
      path.resolve(HERE, "../../index.html"),
      path.resolve(HERE, "../../src/index.css"),
      path.resolve(HERE, "../../src/server/index.ts"),
      path.resolve(HERE, "../../src/server/security.ts"),
    ];
    for (const file of files) {
      const content = fs.readFileSync(file, "utf8");
      expect(content, file).not.toContain("fonts.googleapis.com");
      expect(content, file).not.toContain("fonts.gstatic.com");
    }
  });
});

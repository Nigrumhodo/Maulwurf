import { describe, expect, it } from "vitest";
import i18n, { applyLocale, DEFAULT_LOCALE, getStoredLocale, UI_LOCALES } from "@/i18n";
import { NAMESPACES, resources } from "@/i18n/resources";

function keyPaths(value: unknown, prefix = ""): string[] {
  if (value === null || typeof value !== "object") return [prefix];
  return Object.entries(value as Record<string, unknown>).flatMap(([key, child]) =>
    keyPaths(child, prefix === "" ? key : `${prefix}.${key}`),
  );
}

function leaves(value: unknown): unknown[] {
  if (value === null || typeof value !== "object") return [value];
  return Object.values(value as Record<string, unknown>).flatMap(leaves);
}

describe("i18n resources", () => {
  it("offers exactly es, en and fr with Spanish as the default", () => {
    expect(UI_LOCALES.map((locale) => locale.code)).toEqual(["es", "en", "fr"]);
    expect(DEFAULT_LOCALE).toBe("es");
    expect(i18n.language).toBe("es");
  });

  for (const ns of NAMESPACES) {
    it(`keeps the same keys in every locale for namespace "${ns}"`, () => {
      const source = keyPaths(resources.es[ns]).sort();
      for (const { code } of UI_LOCALES) {
        expect(keyPaths(resources[code][ns]).sort(), `${code}/${ns}.json`).toEqual(source);
      }
    });

    it(`has no empty strings in namespace "${ns}"`, () => {
      for (const { code } of UI_LOCALES) {
        for (const leaf of leaves(resources[code][ns])) {
          expect(typeof leaf === "string" && leaf.trim() !== "", `${code}/${ns}.json`).toBe(true);
        }
      }
    });
  }
});

describe("ui locale persistence", () => {
  it("falls back to Spanish without a stored choice and ignores unknown values", () => {
    expect(getStoredLocale()).toBe("es");
  });

  it("switches the active language and restores Spanish", async () => {
    await applyLocale("fr");
    expect(i18n.language).toBe("fr");
    await applyLocale("es");
    expect(i18n.language).toBe("es");
  });
});

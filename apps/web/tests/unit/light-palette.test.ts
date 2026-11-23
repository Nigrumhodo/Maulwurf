import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(resolve(__dirname, "../../src/index.css"), "utf8");

function block(selector: string): string {
  const start = css.indexOf(`${selector} {`);
  expect(start).toBeGreaterThanOrEqual(0);
  return css.slice(start, css.indexOf("}", start));
}

describe("dual retro palette tokens (Lavanda Nocturna + Ámbar CRT)", () => {
  it("uses the Ámbar CRT & Pergamino palette in light mode with an AA ink variant", () => {
    const light = block(":root.light");
    expect(light).toContain("--color-dark: #efe7da;");
    expect(light).toContain("--color-surface: #fcf9f2;");
    expect(light).toContain("--color-pure-black: #26180a;");
    expect(light).toContain("--color-mint: #b45309;");
    expect(light).toContain("--color-mint-ink: #9a4306;");
    expect(light).toContain("--color-on-accent: #ffffff;");
  });

  it("uses the Lavanda Nocturna palette in dark mode with an AA ink variant", () => {
    const theme = block("@theme");
    expect(theme).toContain("--color-dark: #12101a;");
    expect(theme).toContain("--color-surface: #1c1829;");
    expect(theme).toContain("--color-pure-black: #dfd6f7;");
    expect(theme).toContain("--color-mint: #c084fc;");
    expect(theme).toContain("--color-mint-ink: #cb99ff;");
    expect(theme).toContain("--color-on-accent: #140b24;");
  });

  it("no longer contains legacy cobalt or emerald accents", () => {
    const lower = css.toLowerCase();
    expect(lower).not.toContain("#0000ff");
    expect(lower).not.toContain("#0f8b8d");
  });

  it("routes text and filled accents to the ink variant", () => {
    const theme = block("@theme");
    for (const ns of ["--text-color-mint", "--background-color-mint"]) {
      expect(theme).toContain(`${ns}: var(--color-mint-ink);`);
    }
  });

  it("shows the pixel-art waves layer in both dark and light themes", () => {
    const darkWaves = block(".waves-bg");
    expect(darkWaves).toContain("waves-dark.png");
    const lightWaves = block(":root.light .waves-bg");
    expect(lightWaves).toContain("waves-light.png");
    expect(lightWaves).not.toContain("display: none");
  });
});

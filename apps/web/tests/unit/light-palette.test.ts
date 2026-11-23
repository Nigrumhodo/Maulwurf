import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(resolve(__dirname, "../../src/index.css"), "utf8");

function block(selector: string): string {
  const start = css.indexOf(`${selector} {`);
  expect(start).toBeGreaterThanOrEqual(0);
  return css.slice(start, css.indexOf("}", start));
}

describe("light palette tokens", () => {
  it("uses the violet accent with an AA ink variant", () => {
    const light = block(":root.light");
    expect(light).toContain("--color-mint: oklch(60.6% 0.25 292.717);");
    expect(light).toContain("--color-mint-ink: oklch(54% 0.25 292.717);");
  });

  it("no longer contains the old cobalt accent", () => {
    expect(css.toLowerCase()).not.toContain("#0000ff");
  });

  it("keeps the dark accent unchanged", () => {
    const theme = block("@theme");
    expect(theme).toContain("--color-mint: #0f8b8d;");
    expect(theme).toContain("--color-mint-ink: #0f8b8d;");
  });

  it("routes text and filled accents to the ink variant", () => {
    const theme = block("@theme");
    for (const ns of ["--text-color-mint", "--background-color-mint"]) {
      expect(theme).toContain(`${ns}: var(--color-mint-ink);`);
    }
  });

  it("shows the waves layer in the light theme", () => {
    const rule = block(":root.light .waves-bg");
    expect(rule).toContain("waves-light.png");
    expect(rule).not.toContain("display: none");
  });
});

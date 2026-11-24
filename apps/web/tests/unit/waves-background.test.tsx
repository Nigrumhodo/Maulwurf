// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { WavesBackground } from "@/components/motion/WavesBackground";

afterEach(cleanup);

describe("WavesBackground", () => {
  it("renders a decorative layer hidden from assistive tech", () => {
    const { container } = render(<WavesBackground />);
    const layer = container.querySelector(".waves-bg");
    expect(layer).not.toBeNull();
    expect(layer?.getAttribute("aria-hidden")).toBe("true");
  });

  it("has no interactive content or role", () => {
    const { container } = render(<WavesBackground />);
    expect(container.querySelector("a, button, input, select, textarea, [tabindex]")).toBeNull();
    expect(container.querySelector("[role]")).toBeNull();
  });

  it("renders exactly one layer regardless of theme", () => {
    document.documentElement.classList.add("light");
    const { container } = render(<WavesBackground />);
    expect(container.querySelectorAll(".waves-bg")).toHaveLength(1);
    document.documentElement.classList.remove("light");
  });

  it("stays full-bleed on viewports wider than the image (no empty band on the right)", () => {
    const css = readFileSync(resolve(__dirname, "../../src/index.css"), "utf8");
    const rule = css.match(/\n\.waves-bg \{([^}]*)\}/)?.[1] ?? "";
    expect(rule).toMatch(/\bwidth: 100%;/);
    expect(rule).toMatch(/background-size: cover;/);
    expect(rule).toMatch(/background-position: center bottom;/);
    expect(rule).toMatch(/image-rendering: pixelated;/);
  });
});

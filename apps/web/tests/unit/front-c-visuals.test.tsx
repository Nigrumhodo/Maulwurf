// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MoleCompanion } from "@/components/dashboard/MoleCompanion";
import { EmptyState } from "@/components/ui/EmptyState";
import { RetroVisual } from "@/components/ui/RetroVisual";
import { StatusBadge } from "@/components/ui/StatusBadge";

afterEach(cleanup);

describe("Front C 16-bit RPG visuals and accessibility", () => {
  it("provides a prefers-reduced-motion static source when RetroVisual receives staticSrc", () => {
    const { container } = render(
      <RetroVisual src="/anim.webp" staticSrc="/static.webp" alt="topo retro" />,
    );
    const source = container.querySelector('picture > source[media="(prefers-reduced-motion: reduce)"]');
    expect(source?.getAttribute("srcset")).toBe("/static.webp");
    expect(screen.getByAltText("topo retro").getAttribute("src")).toBe("/anim.webp");
  });

  it("renders decorative 16-bit illustrations in EmptyState while preserving copy", () => {
    const { container } = render(
      <EmptyState illustration="library" title="aún no hay clases" description="sube la primera" />,
    );
    expect(screen.getByText("aún no hay clases")).toBeTruthy();
    expect(container.querySelector("img")).not.toBeNull();
  });

  it("renders the Front C MoleCompanion with G1 RAM privacy note", () => {
    render(<MoleCompanion />);
    expect(screen.getByLabelText("compañero maulwurf")).toBeTruthy();
    expect(screen.getByText(/audio efímero en ram/i)).toBeTruthy();
  });

  it("keeps the StatusBadge success dot contrasting against the filled accent background", () => {
    const { container } = render(<StatusBadge status="ready" />);
    const dot = container.querySelector('span[aria-hidden="true"]');
    expect(dot?.className).toContain("bg-on-accent");
    expect(dot?.className).not.toContain("bg-mint");
  });
});

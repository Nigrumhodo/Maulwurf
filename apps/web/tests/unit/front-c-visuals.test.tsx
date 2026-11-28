// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { MoleCompanion } from "@/components/dashboard/MoleCompanion";
import { AppSidebar } from "@/components/layout/AppSidebar";
import { EmptyState } from "@/components/ui/EmptyState";
import { RetroVisual } from "@/components/ui/RetroVisual";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { LandingPage } from "@/pages/LandingPage";

vi.mock("@/components/auth/AuthProvider", () => ({
  useAuth: () => ({ user: { email: "ana@example.test" }, loading: false, logout: vi.fn() }),
}));

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

  it("renders paired dark and light artwork when RetroVisual receives lightSrc", () => {
    const { container } = render(
      <RetroVisual
        src="/dark-anim.webp"
        staticSrc="/dark-static.webp"
        lightSrc="/light-anim.webp"
        lightStaticSrc="/light-static.webp"
      />,
    );
    const darkImg = container.querySelector(".img-dark-only img");
    const lightImg = container.querySelector(".img-light-only img");
    const lightSource = container.querySelector(
      '.img-light-only picture > source[media="(prefers-reduced-motion: reduce)"]',
    );
    expect(darkImg?.getAttribute("src")).toBe("/dark-anim.webp");
    expect(lightImg?.getAttribute("src")).toBe("/light-anim.webp");
    expect(lightSource?.getAttribute("srcset")).toBe("/light-static.webp");
  });

  it("renders theme-paired 16-bit illustrations for every EmptyState kind while preserving copy", () => {
    for (const kind of ["library", "tasks", "error", "reading"] as const) {
      const { container } = render(
        <EmptyState illustration={kind} title={`vacío ${kind}`} description="sube la primera" />,
      );
      expect(screen.getByText(`vacío ${kind}`)).toBeTruthy();
      const darkSrc = container.querySelector(".img-dark-only img")?.getAttribute("src");
      const lightSrc = container.querySelector(".img-light-only img")?.getAttribute("src");
      expect(darkSrc).toBeTruthy();
      expect(lightSrc).toBeTruthy();
      expect(darkSrc).not.toBe(lightSrc);
      cleanup();
    }
  });

  it("renders theme-paired navigation icons for every sidebar route with an icon", () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <AppSidebar />
      </MemoryRouter>,
    );
    const nav = screen.getByRole("navigation", { name: "principal" });
    const links = Array.from(nav.querySelectorAll("a"));
    expect(links).toHaveLength(8);
    for (const link of links) {
      const darkSrc = link.querySelector(".img-dark-only img")?.getAttribute("src");
      const lightSrc = link.querySelector(".img-light-only img")?.getAttribute("src");
      expect(darkSrc).toBeTruthy();
      expect(lightSrc).toBeTruthy();
      expect(darkSrc).not.toBe(lightSrc);
    }
  });

  it("renders the Front C MoleCompanion with theme-paired G1 RAM privacy artwork", () => {
    const { container } = render(<MoleCompanion />);
    expect(screen.getByLabelText("compañero maulwurf")).toBeTruthy();
    expect(screen.getByText(/audio efímero en ram/i)).toBeTruthy();
    const darkImgs = container.querySelectorAll(".img-dark-only img");
    const lightImgs = container.querySelectorAll(".img-light-only img");
    expect(darkImgs.length).toBeGreaterThanOrEqual(2);
    expect(lightImgs.length).toBeGreaterThanOrEqual(1);
  });

  it("pairs both the hero and the privacy illustration on LandingPage across dark and light themes", () => {
    const { container } = render(
      <MemoryRouter>
        <LandingPage />
      </MemoryRouter>,
    );
    const darkImgs = Array.from(container.querySelectorAll(".img-dark-only img")).map((el) =>
      el.getAttribute("src"),
    );
    const lightImgs = Array.from(container.querySelectorAll(".img-light-only img")).map((el) =>
      el.getAttribute("src"),
    );
    expect(darkImgs).toHaveLength(2);
    expect(lightImgs).toHaveLength(2);
    expect(darkImgs[0]).not.toBe(lightImgs[0]);
    expect(darkImgs[1]).not.toBe(lightImgs[1]);
  });

  it("keeps the StatusBadge success dot contrasting against the filled accent background", () => {
    const { container } = render(<StatusBadge status="ready" />);
    const dot = container.querySelector('span[aria-hidden="true"]');
    expect(dot?.className).toContain("bg-on-accent");
    expect(dot?.className).not.toContain("bg-mint");
  });
});

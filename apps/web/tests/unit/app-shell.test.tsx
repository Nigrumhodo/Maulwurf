// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AppLayout } from "@/components/layout/AppLayout";
import { GestionLayout } from "@/components/layout/GestionLayout";
import { currentSectionLabel, gestionBreadcrumb } from "@/components/layout/gestionNav";
import { ThemeToggle } from "@/components/ui/ThemeToggle";

const logout = vi.fn(async () => {});

vi.mock("@/components/auth/AuthProvider", () => ({
  useAuth: () => ({ user: { email: "ana@example.test" }, loading: false, logout }),
}));

function renderShell(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<AppLayout />}>
          <Route path="/" element={<p>vista inicio</p>} />
          <Route path="/settings" element={<p>vista ajustes</p>} />
          <Route path="/gestion" element={<GestionLayout />}>
            <Route index element={<p>vista panel</p>} />
            <Route path="tareas" element={<p>vista tareas</p>} />
            <Route path="chat" element={<p>vista chat</p>} />
            <Route path="biblioteca/:audioId" element={<p>vista clase</p>} />
          </Route>
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

function menu() {
  return within(screen.getByRole("complementary", { name: "menú" }));
}

afterEach(() => {
  cleanup();
  logout.mockClear();
  localStorage.clear();
  document.documentElement.classList.remove("light");
});

describe("AppLayout shell (Wave 3 · W1)", () => {
  it("mounts the waves background as the first child of the layout root", () => {
    const { container } = renderShell("/");
    const root = container.firstElementChild;
    expect(root?.firstElementChild?.classList.contains("waves-bg")).toBe(true);
  });

  it("lists every section once, grouped, with only the current route marked active", () => {
    renderShell("/gestion/tareas");
    const nav = within(menu().getByRole("navigation", { name: "principal" }));
    const hrefs = nav.getAllByRole("link").map((a) => a.getAttribute("href"));
    expect(hrefs).toEqual([
      "/",
      "/gestion",
      "/gestion/subir",
      "/gestion/biblioteca",
      "/gestion/chat",
      "/gestion/materias",
      "/gestion/tareas",
      "/gestion/agenda",
    ]);
    expect(within(nav.getByRole("list", { name: "estudio" })).getAllByRole("link")).toHaveLength(3);
    expect(within(nav.getByRole("list", { name: "planificación" })).getAllByRole("link")).toHaveLength(3);

    const active = menu()
      .getAllByRole("link")
      .filter((a) => a.getAttribute("aria-current") === "page");
    expect(active.map((a) => a.getAttribute("href"))).toEqual(["/gestion/tareas"]);
    expect(menu().getByRole("link", { name: "ajustes" }).getAttribute("href")).toBe("/settings");
    expect(screen.getByText("vista tareas")).toBeTruthy();
  });

  it("marks panel active only on the hub index, not on its sections", () => {
    renderShell("/gestion");
    expect(menu().getByRole("link", { name: "panel" }).getAttribute("aria-current")).toBe("page");
    expect(menu().getByRole("link", { name: "inicio" }).getAttribute("aria-current")).toBeNull();
  });

  it("keeps the route-derived breadcrumb on gestión sections only", () => {
    renderShell("/gestion/tareas");
    const crumbs = within(screen.getByRole("navigation", { name: "ruta" }));
    expect(crumbs.getByRole("link", { name: "panel" }).getAttribute("href")).toBe("/gestion");
    expect(crumbs.getByText("tareas").getAttribute("aria-current")).toBe("page");
    cleanup();

    renderShell("/settings");
    expect(screen.queryByRole("navigation", { name: "ruta" })).toBeNull();
  });

  it("opens the command palette from the menu search entry", () => {
    renderShell("/");
    expect(screen.queryByRole("dialog", { name: /paleta de comandos/ })).toBeNull();
    fireEvent.click(menu().getByRole("button", { name: "buscar con ctrl+k" }));
    expect(screen.getByRole("dialog", { name: /paleta de comandos/ })).toBeTruthy();
  });

  it("logs out through the existing auth flow", () => {
    renderShell("/");
    fireEvent.click(menu().getByRole("button", { name: "salir" }));
    expect(logout).toHaveBeenCalledTimes(1);
  });

  it("names the current section in the mobile top bar", () => {
    renderShell("/gestion/chat");
    const header = within(screen.getByRole("banner"));
    expect(header.getByText("chat")).toBeTruthy();
  });

  it("opens the mobile drawer, moves focus into it, and returns focus on Escape", () => {
    renderShell("/");
    const toggle = screen.getByRole("button", { name: "menú" });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(toggle.getAttribute("aria-controls")).toBe("app-menu");

    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(document.activeElement).toBe(menu().getByRole("button", { name: "cerrar menú" }));

    fireEvent.keyDown(window, { key: "Escape" });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(document.activeElement).toBe(toggle);
  });

  it("closes the drawer when a menu link navigates", () => {
    renderShell("/");
    const toggle = screen.getByRole("button", { name: "menú" });
    fireEvent.click(toggle);
    fireEvent.click(menu().getByRole("link", { name: "tareas" }));
    expect(screen.getByText("vista tareas")).toBeTruthy();
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByTestId("menu-scrim")).toBeNull();
  });

  it("expands and collapses the tablet rail with its button and Escape", () => {
    renderShell("/");
    const rail = menu().getByRole("button", { name: "expandir menú" });
    expect(rail.getAttribute("aria-expanded")).toBe("false");

    fireEvent.click(rail);
    expect(rail.getAttribute("aria-expanded")).toBe("true");
    expect(rail.getAttribute("aria-label")).toBe("colapsar menú");

    fireEvent.keyDown(window, { key: "Escape" });
    expect(rail.getAttribute("aria-expanded")).toBe("false");
    expect(document.activeElement).toBe(rail);
  });
});

describe("gestionNav helpers", () => {
  it("derives the section label for the mobile bar", () => {
    expect(currentSectionLabel("/")).toBe("inicio");
    expect(currentSectionLabel("/gestion")).toBe("panel");
    expect(currentSectionLabel("/gestion/biblioteca/a-1")).toBe("biblioteca");
    expect(currentSectionLabel("/settings")).toBe("ajustes");
  });

  it("builds route-only breadcrumbs with a generic detail crumb", () => {
    expect(gestionBreadcrumb("/gestion")).toHaveLength(1);
    expect(gestionBreadcrumb("/gestion/biblioteca/a-1")).toEqual([
      { label: "panel", to: "/gestion" },
      { label: "biblioteca", to: "/gestion/biblioteca" },
      { label: "clase" },
    ]);
  });
});

describe("ThemeToggle", () => {
  it("keeps its standalone label and switches the theme class", () => {
    render(<ThemeToggle />);
    const button = screen.getByRole("button", { name: "cambiar a modo claro" });
    expect(button.textContent).toBe("☾ oscuro");

    fireEvent.click(button);
    expect(document.documentElement.classList.contains("light")).toBe(true);
    expect(localStorage.getItem("maulwurf-theme")).toBe("light");
    expect(screen.getByRole("button", { name: "cambiar a modo oscuro" }).textContent).toBe("☀ claro");
  });
});

// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AppSidebar } from "@/components/layout/AppSidebar";
import { LandingPage } from "@/pages/LandingPage";
import { LoginPage } from "@/pages/LoginPage";
import { RegisterPage } from "@/pages/RegisterPage";
import { PrivacyPage } from "@/pages/legal/PrivacyPage";
import { TermsPage } from "@/pages/legal/TermsPage";

vi.mock("@/components/auth/AuthProvider", () => ({
  useAuth: () => ({ user: { email: "ana@example.test" }, loading: false, logout: vi.fn() }),
}));

afterEach(() => {
  cleanup();
});

function hrefs(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll("a")).map((a) => a.getAttribute("href") ?? "");
}

describe("legal mounts", () => {
  it("landing footer links to the privacy policy and the terms", () => {
    const { container } = render(
      <MemoryRouter>
        <LandingPage />
      </MemoryRouter>,
    );
    const footer = container.querySelector("footer");
    expect(footer).not.toBeNull();
    const links = hrefs(footer as HTMLElement);
    expect(links).toContain("/privacidad");
    expect(links).toContain("/terminos");
  });

  it("landing shows the consent notice under the hero CTA group", () => {
    render(
      <MemoryRouter>
        <LandingPage />
      </MemoryRouter>,
    );
    const cta = screen.getByRole("link", { name: /entrar con google/i });
    const hero = cta.closest("section") as HTMLElement;
    expect(hero.textContent).toContain("al continuar aceptas los términos de uso");
  });

  it("login shows the legal links and the consent notice", () => {
    const { container } = render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>,
    );
    const links = hrefs(container);
    expect(links).toContain("/privacidad");
    expect(links).toContain("/terminos");
    expect(container.textContent).toContain("al continuar aceptas los");
  });

  it("register shows the legal links and the consent notice", () => {
    const { container } = render(
      <MemoryRouter>
        <RegisterPage />
      </MemoryRouter>,
    );
    const links = hrefs(container);
    expect(links).toContain("/privacidad");
    expect(links).toContain("/terminos");
    expect(container.textContent).toContain("al continuar aceptas los");
  });

  it("sidebar renders the legal links in the menu footer", () => {
    render(
      <MemoryRouter>
        <AppSidebar />
      </MemoryRouter>,
    );
    const menu = screen.getByRole("complementary", { name: "menú" });
    const legal = within(menu).getByRole("navigation", { name: "información legal" });
    expect(hrefs(legal)).toEqual(["/privacidad", "/terminos", "/terminos#licencia"]);
  });

  for (const [name, Page] of [
    ["privacy", PrivacyPage],
    ["terms", TermsPage],
  ] as const) {
    it(`${name} page renders the language switcher`, () => {
      render(
        <MemoryRouter>
          <Page />
        </MemoryRouter>,
      );
      expect(screen.getByRole("combobox", { name: "idioma de la interfaz" })).toBeTruthy();
    });
  }
});

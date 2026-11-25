// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { App } from "@/App";
import { LegalConsentNotice } from "@/components/legal/LegalConsentNotice";
import { LegalLayout } from "@/components/legal/LegalLayout";
import { LegalLinks } from "@/components/legal/LegalLinks";
import { PrivacyPage } from "@/pages/legal/PrivacyPage";
import { TermsPage } from "@/pages/legal/TermsPage";
import { applyLocale } from "@/i18n";
import es from "@/locales/es/legal.json";
import en from "@/locales/en/legal.json";
import fr from "@/locales/fr/legal.json";

vi.mock("@/components/auth/AuthProvider", async () => ({
  AuthProvider: ({ children }: { children: React.ReactNode }) => children,
  RequireAuth: () => {
    throw new Error("legal routes must not require a session");
  },
  useAuth: () => ({ user: null, loading: false, unavailable: false, retrySession: vi.fn() }),
}));

const locales = { es, en, fr } as const;
type Doc = "privacy" | "terms";

function headings(doc: Doc, locale: keyof typeof locales): string[] {
  const sections = locales[locale][doc].sections as Record<string, { title: string }>;
  return Object.values(sections).map((section) => section.title);
}

async function setLocale(locale: "es" | "en" | "fr") {
  await act(async () => {
    await applyLocale(locale);
  });
}

afterEach(async () => {
  cleanup();
  await setLocale("es");
  localStorage.clear();
});

const pages = [
  { doc: "privacy" as const, Page: PrivacyPage },
  { doc: "terms" as const, Page: TermsPage },
];

describe("legal pages", () => {
  for (const { doc, Page } of pages) {
    for (const locale of ["es", "en", "fr"] as const) {
      it(`${doc} renders h1 and every section heading in ${locale}`, async () => {
        await setLocale(locale);
        render(
          <MemoryRouter>
            <Page />
          </MemoryRouter>,
        );
        const h1 = screen.getByRole("heading", { level: 1 });
        expect(h1.textContent).toBe(locales[locale][doc].title);
        const h2s = screen
          .getAllByRole("heading", { level: 2 })
          .map((el) => el.textContent);
        for (const title of headings(doc, locale)) {
          expect(h2s).toContain(title);
        }
        expect(screen.getAllByRole("note")[0]?.textContent).toContain(
          locales[locale].common.draftBanner,
        );
      });
    }
  }

  it("renders unknown values as visible placeholders per locale", async () => {
    const { unmount } = render(
      <MemoryRouter>
        <PrivacyPage />
      </MemoryRouter>,
    );
    expect(screen.getAllByText(/\[completar: /).length).toBeGreaterThan(0);
    expect(document.body.textContent).not.toContain("{{");
    unmount();

    await setLocale("en");
    const second = render(
      <MemoryRouter>
        <PrivacyPage />
      </MemoryRouter>,
    );
    expect(second.container.textContent).toContain("[to complete: contact e-mail]");
    second.unmount();

    await setLocale("fr");
    render(
      <MemoryRouter>
        <PrivacyPage />
      </MemoryRouter>,
    );
    expect(document.body.textContent).toContain("[à compléter : e-mail de contact]");
  });

  it("terms link the full AGPL text, the source URL and the license anchor", () => {
    render(
      <MemoryRouter>
        <TermsPage />
      </MemoryRouter>,
    );
    const hrefs = screen.getAllByRole("link").map((a) => a.getAttribute("href"));
    expect(hrefs).toContain("https://www.gnu.org/licenses/agpl-3.0.html");
    expect(hrefs).toContain("https://github.com/Nigrumhodo/Maulwurf");
    expect(document.getElementById("licencia")).not.toBeNull();
    expect(screen.getByText(/NO VERIFICADO/)).toBeTruthy();
  });

  it("the layout exposes a slot for the language switcher", () => {
    render(
      <MemoryRouter>
        <LegalLayout title="t" sections={[]} languageSwitcher={<span>switcher-slot</span>}>
          <p>x</p>
        </LegalLayout>
      </MemoryRouter>,
    );
    expect(screen.getByText("switcher-slot")).toBeTruthy();
  });

  for (const [path, title] of [
    ["/privacidad", es.privacy.title],
    ["/terminos", es.terms.title],
  ] as const) {
    it(`route ${path} is reachable without a session`, () => {
      render(
        <MemoryRouter initialEntries={[path]}>
          <App />
        </MemoryRouter>,
      );
      expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(title);
    });
  }
});

describe("LegalLinks and LegalConsentNotice", () => {
  it("LegalLinks points at privacy, terms and the license anchor", () => {
    render(
      <MemoryRouter>
        <LegalLinks className="extra" />
      </MemoryRouter>,
    );
    const nav = within(screen.getByRole("navigation", { name: es.links.label }));
    expect(nav.getAllByRole("link").map((a) => a.getAttribute("href"))).toEqual([
      "/privacidad",
      "/terminos",
      "/terminos#licencia",
    ]);
  });

  it("LegalConsentNotice is a notice with both links and no checkbox", () => {
    const { container } = render(
      <MemoryRouter>
        <LegalConsentNotice />
      </MemoryRouter>,
    );
    expect(container.textContent).toBe(
      "al continuar aceptas los términos de uso y la política de privacidad.",
    );
    expect(screen.getByRole("link", { name: "términos de uso" }).getAttribute("href")).toBe(
      "/terminos",
    );
    expect(
      screen.getByRole("link", { name: "política de privacidad" }).getAttribute("href"),
    ).toBe("/privacidad");
    expect(screen.queryByRole("checkbox")).toBeNull();
  });
});

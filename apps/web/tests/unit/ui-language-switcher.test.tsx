// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import i18n, { applyLocale } from "@/i18n";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import { UiLanguageSwitcher } from "@/components/ui/UiLanguageSwitcher";

afterEach(async () => {
  cleanup();
  await applyLocale("es");
  localStorage.clear();
  document.documentElement.classList.remove("light");
});

function renderWithToggle() {
  return render(
    <>
      <UiLanguageSwitcher />
      <ThemeToggle />
    </>,
  );
}

describe("UiLanguageSwitcher", () => {
  it("offers the three interface languages by endonym, separate from the audio language", () => {
    renderWithToggle();
    const select = screen.getByRole("combobox", { name: "idioma de la interfaz" });
    const options = within(select).getAllByRole("option");
    expect(options.map((option) => option.getAttribute("value"))).toEqual(["es", "en", "fr"]);
    expect(options.map((option) => option.textContent)).toEqual(["español", "english", "français"]);
    expect((select as HTMLSelectElement).value).toBe("es");
  });

  it("switches the copy of translated components and persists the choice", async () => {
    renderWithToggle();
    expect(screen.getByRole("button", { name: "cambiar a modo claro" }).textContent).toBe("☾ oscuro");

    fireEvent.change(screen.getByRole("combobox", { name: "idioma de la interfaz" }), {
      target: { value: "en" },
    });

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "switch to light mode" }).textContent).toBe(
        "☾ dark",
      ),
    );
    expect(i18n.language).toBe("en");
    expect(localStorage.getItem("maulwurf-locale")).toBe("en");
    expect(document.documentElement.lang).toBe("en");
    const select = screen.getByRole("combobox", { name: "interface language" }) as HTMLSelectElement;
    expect(select.value).toBe("en");
  });

  it("shows the short code in the glyph slot for the collapsed rail", async () => {
    render(<UiLanguageSwitcher glyphClassName="glyph" labelClassName="sr-only" />);
    expect(document.querySelector(".glyph")?.textContent).toBe("ES");

    await applyLocale("fr");
    await waitFor(() => expect(document.querySelector(".glyph")?.textContent).toBe("FR"));
    expect(screen.getByRole("combobox", { name: "langue de l'interface" })).toBeTruthy();
  });
});

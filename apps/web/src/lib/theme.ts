// Tema claro/oscuro. La clase `light` se aplica sobre `<html>`; el CSS en
// `src/index.css` resuelve `--color-*` según esa clase, así que conmutar es
// solo alternar la clase y persistir la preferencia.

export type ThemeMode = "dark" | "light";

const STORAGE_KEY = "maulwurf-theme";

export function getStoredTheme(): ThemeMode {
  if (typeof window === "undefined") return "dark";
  return window.localStorage.getItem(STORAGE_KEY) === "light" ? "light" : "dark";
}

const THEME_COLOR: Record<ThemeMode, string> = {
  dark: "#12101a",
  light: "#efe7da",
};

const THEME_FAVICON: Record<ThemeMode, string> = {
  dark: "/icon.svg",
  light: "/icon-light.svg",
};

/** Aplica el tema sobre `document.documentElement` y lo persiste. */
export function applyTheme(mode: ThemeMode): void {
  document.documentElement.classList.toggle("light", mode === "light");
  const meta = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
  if (meta !== null) meta.content = THEME_COLOR[mode];
  const favicon = document.querySelector<HTMLLinkElement>('link[rel="icon"]');
  if (favicon !== null) favicon.setAttribute("href", THEME_FAVICON[mode]);
  if (typeof window !== "undefined") {
    window.localStorage.setItem(STORAGE_KEY, mode);
  }
}

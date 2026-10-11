// Tema claro/oscuro. La clase `light` se aplica sobre `<html>`; el CSS en
// `src/index.css` resuelve `--color-*` según esa clase, así que conmutar es
// solo alternar la clase y persistir la preferencia.

export type ThemeMode = "dark" | "light";

const STORAGE_KEY = "maulwurf-theme";

export function getStoredTheme(): ThemeMode {
  if (typeof window === "undefined") return "dark";
  return window.localStorage.getItem(STORAGE_KEY) === "light" ? "light" : "dark";
}

/** Aplica el tema sobre `document.documentElement` y lo persiste. */
export function applyTheme(mode: ThemeMode): void {
  document.documentElement.classList.toggle("light", mode === "light");
  if (typeof window !== "undefined") {
    window.localStorage.setItem(STORAGE_KEY, mode);
  }
}

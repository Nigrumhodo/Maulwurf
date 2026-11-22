import { useState } from "react";
import { applyTheme, getStoredTheme } from "@/lib/theme";
import type { ThemeMode } from "@/lib/theme";

const labels: Record<ThemeMode, string> = {
  dark: "☾ oscuro",
  light: "☀ claro",
};

/** Botón que conmuta entre el tema oscuro y el claro. */
export function ThemeToggle() {
  const [mode, setMode] = useState<ThemeMode>(getStoredTheme);

  function toggle() {
    const next: ThemeMode = mode === "dark" ? "light" : "dark";
    setMode(next);
    applyTheme(next);
  }

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={`cambiar a modo ${mode === "dark" ? "claro" : "oscuro"}`}
      className="capitalize font-pixel min-h-10 flex-1 whitespace-nowrap border-2 border-pure-black bg-dark px-2 py-1 sm:flex-none sm:px-3 text-lavender shadow-block-sm transition-none hover:text-mint active:translate-x-[1px] active:translate-y-[1px]"
    >
      {labels[mode]}
    </button>
  );
}

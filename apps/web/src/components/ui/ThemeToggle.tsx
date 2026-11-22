import { useState } from "react";
import { applyTheme, getStoredTheme } from "@/lib/theme";
import type { ThemeMode } from "@/lib/theme";

const labels: Record<ThemeMode, { glyph: string; text: string }> = {
  dark: { glyph: "☾", text: "oscuro" },
  light: { glyph: "☀", text: "claro" },
};

const defaultClasses =
  "capitalize font-pixel min-h-10 flex-1 whitespace-nowrap border-2 border-pure-black bg-dark px-2 py-1 sm:flex-none sm:px-3 text-lavender shadow-block-sm transition-none hover:text-mint active:translate-x-[1px] active:translate-y-[1px]";

export interface ThemeToggleProps {
  /** Replaces the standalone button styling (used by the app menu). */
  className?: string;
  /** Extra classes for the glyph, e.g. to align it with the menu glyph column. */
  glyphClassName?: string;
  /** Extra classes for the text label, e.g. to hide it in a glyph-only rail. */
  labelClassName?: string;
}

/** Botón que conmuta entre el tema oscuro y el claro. */
export function ThemeToggle({
  className,
  glyphClassName = "",
  labelClassName = "",
}: ThemeToggleProps = {}) {
  const [mode, setMode] = useState<ThemeMode>(getStoredTheme);

  function toggle() {
    const next: ThemeMode = mode === "dark" ? "light" : "dark";
    setMode(next);
    applyTheme(next);
  }

  const label = labels[mode];
  const nextLabel = mode === "dark" ? "claro" : "oscuro";

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={`cambiar a modo ${nextLabel}`}
      title={`cambiar a modo ${nextLabel}`}
      className={className ?? defaultClasses}
    >
      <span aria-hidden="true" className={glyphClassName}>
        {label.glyph}
      </span>{" "}
      <span className={labelClassName}>{label.text}</span>
    </button>
  );
}

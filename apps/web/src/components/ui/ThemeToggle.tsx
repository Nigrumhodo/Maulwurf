import { useState } from "react";
import { useTranslation } from "react-i18next";
import { applyTheme, getStoredTheme } from "@/lib/theme";
import type { ThemeMode } from "@/lib/theme";

const GLYPHS: Record<ThemeMode, string> = { dark: "☾", light: "☀" };

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
  const { t } = useTranslation("common");
  const [mode, setMode] = useState<ThemeMode>(getStoredTheme);

  const next: ThemeMode = mode === "dark" ? "light" : "dark";
  const action = t("theme.switchTo", { mode: t(`theme.${next}`) });

  function toggle() {
    setMode(next);
    applyTheme(next);
  }

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={action}
      title={action}
      className={className ?? defaultClasses}
    >
      <span aria-hidden="true" className={glyphClassName}>
        {GLYPHS[mode]}
      </span>{" "}
      <span className={labelClassName}>{t(`theme.${mode}`)}</span>
    </button>
  );
}

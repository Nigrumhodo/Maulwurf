import type { ChangeEvent } from "react";
import { useTranslation } from "react-i18next";
import { applyLocale, DEFAULT_LOCALE, isUiLocale, UI_LOCALES } from "@/i18n";

// Same vocabulary as ThemeToggle's standalone button; `has-[select:focus-visible]` draws the
// focus ring on the visible box because the native select sits invisibly on top of it.
const baseClasses =
  "capitalize font-pixel flex-1 whitespace-nowrap border-2 border-pure-black bg-dark px-2 py-1 sm:flex-none sm:px-3 text-lavender shadow-block-sm transition-none hover:text-mint";

const focusClasses =
  "has-[select:focus-visible]:outline-2 has-[select:focus-visible]:outline-offset-2 has-[select:focus-visible]:outline-mint";

export interface UiLanguageSwitcherProps {
  /** Replaces the standalone box styling (used by the app menu row). */
  className?: string;
  /** Extra classes for the short code (`ES/EN/FR`), e.g. to align it with the menu glyph column. */
  glyphClassName?: string;
  /** Extra classes for the language name, e.g. to hide it in a glyph-only rail. */
  labelClassName?: string;
  /** Standalone box with a 32px hit target instead of 40px. Ignored when `className` is set. */
  compact?: boolean;
}

/**
 * Interface-language picker (es/en/fr). A native `<select>` covers the visible box, so the
 * keyboard, screen-reader and mobile pickers are the platform ones. It never touches the
 * audio language of an upload, which is a separate per-class choice.
 */
export function UiLanguageSwitcher({
  className,
  glyphClassName = "",
  labelClassName = "",
  compact = false,
}: UiLanguageSwitcherProps = {}) {
  const { t, i18n } = useTranslation("common");
  const current = isUiLocale(i18n.resolvedLanguage) ? i18n.resolvedLanguage : DEFAULT_LOCALE;
  const currentLabel = UI_LOCALES.find((locale) => locale.code === current)?.label ?? current;
  const name = t("uiLanguage.label");

  function onChange(event: ChangeEvent<HTMLSelectElement>) {
    const next = event.target.value;
    if (isUiLocale(next)) void applyLocale(next);
  }

  const boxClasses =
    className ??
    `${baseClasses} inline-flex items-center justify-center gap-1 ${compact ? "min-h-8" : "min-h-10"}`;

  return (
    <label title={name} className={`relative cursor-pointer ${boxClasses} ${focusClasses}`}>
      <span aria-hidden="true" className={glyphClassName}>
        {current.toUpperCase()}
      </span>{" "}
      <span aria-hidden="true" className={labelClassName}>
        {currentLabel}
      </span>
      <select
        aria-label={name}
        value={current}
        onChange={onChange}
        className="absolute inset-0 h-full w-full cursor-pointer opacity-0"
      >
        {UI_LOCALES.map((locale) => (
          <option key={locale.code} value={locale.code} lang={locale.code}>
            {locale.label}
          </option>
        ))}
      </select>
    </label>
  );
}

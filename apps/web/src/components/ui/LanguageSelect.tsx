import { forwardRef } from "react";
import type { SelectHTMLAttributes } from "react";
import { LANGUAGES } from "@/lib/languages";

// D1.5 — Select de idioma del audio. SIN preselección silenciosa (M2): el
// placeholder deshabilitado obliga a elegir; solo se ofrecen los códigos de la
// allowlist de capabilities (S1.A3).
export interface LanguageSelectProps
  extends SelectHTMLAttributes<HTMLSelectElement> {
  /** label rendered above the field, in terminal-prompt style */
  label?: string;
  /** render the label with a `>` prompt prefix */
  prompt?: boolean;
  /** placeholder shown when nothing is selected */
  placeholder?: string;
  /** opciones a ofrecer; por defecto la allowlist estática (D1.5). D2.1 pasa las publicadas. */
  options?: ReadonlyArray<{ code: string; label: string }>;
}

export const LanguageSelect = forwardRef<HTMLSelectElement, LanguageSelectProps>(
  (
    {
      label,
      prompt = true,
      id,
      placeholder = "selecciona idioma",
      className = "",
      options = LANGUAGES,
      ...rest
    },
    ref,
  ) => (
    <label className="lowercase font-pixel flex flex-col gap-1">
      {label != null && (
        <span className="text-mint">
          {prompt ? "> " : ""}
          {label}
        </span>
      )}
      <select
        ref={ref}
        id={id}
        className={`bg-surface text-lavender border-2 border-lavender px-3 py-2 caret-mint focus:border-mint focus:outline-none ${className}`}
        {...rest}
      >
        <option value="" disabled>
          {placeholder}
        </option>
        {options.map((language) => (
          <option key={language.code} value={language.code}>
            {language.label}
          </option>
        ))}
      </select>
    </label>
  ),
);

LanguageSelect.displayName = "LanguageSelect";

import { forwardRef } from "react";
import type { SelectHTMLAttributes } from "react";
import { TIMEZONES } from "@/lib/timezones";

// D1.5 — Select de zona horaria (IANA). Sin preselección: muestra un placeholder
// deshabilitado hasta que el usuario elige un identificador `Area/City`.
export interface TimezoneSelectProps
  extends SelectHTMLAttributes<HTMLSelectElement> {
  /** label rendered above the field, in terminal-prompt style */
  label?: string;
  /** render the label with a `>` prompt prefix */
  prompt?: boolean;
  /** placeholder shown when nothing is selected */
  placeholder?: string;
}

export const TimezoneSelect = forwardRef<HTMLSelectElement, TimezoneSelectProps>(
  ({ label, prompt = true, id, placeholder = "selecciona zona horaria", className = "", ...rest }, ref) => (
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
        {TIMEZONES.map((timezone) => (
          <option key={timezone} value={timezone}>
            {timezone}
          </option>
        ))}
      </select>
    </label>
  ),
);

TimezoneSelect.displayName = "TimezoneSelect";

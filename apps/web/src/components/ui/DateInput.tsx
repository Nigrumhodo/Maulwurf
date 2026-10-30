import { forwardRef } from "react";
import type { InputHTMLAttributes } from "react";

// D1.5 — Input de fecha (clase / vencimiento). Fecha plana (DATE) sin zona; el
// contrato resuelve fechas contra la fecha+zonahoraria de la clase, no "hoy".
export interface DateInputProps
  extends Omit<InputHTMLAttributes<HTMLInputElement>, "type"> {
  /** label rendered above the field, in terminal-prompt style */
  label?: string;
  /** render the label with a `>` prompt prefix */
  prompt?: boolean;
}

export const DateInput = forwardRef<HTMLInputElement, DateInputProps>(
  ({ label, prompt = true, id, className = "", ...rest }, ref) => (
    <label className="lowercase font-pixel flex flex-col gap-1">
      {label != null && (
        <span className="text-mint">
          {prompt ? "> " : ""}
          {label}
        </span>
      )}
      <input
        ref={ref}
        id={id}
        type="date"
        className={`bg-surface text-lavender border-2 border-lavender px-3 py-2 caret-mint placeholder:text-lavender/40 focus:border-mint focus:outline-none ${className}`}
        {...rest}
      />
    </label>
  ),
);

DateInput.displayName = "DateInput";

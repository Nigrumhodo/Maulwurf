import type { HTMLAttributes } from "react";
import { statusTone } from "@/lib/status";
import type { StatusTone } from "@/lib/status";

// D1.5 — Badge de estado. Muestra el valor del contrato TAL CUAL (nunca se
// traduce a texto amigable); el tono solo ajusta el color/borde. El punto es un
// elemento separado, de modo que el texto del badge es exactamente el estado.
export interface StatusBadgeProps extends HTMLAttributes<HTMLSpanElement> {
  /** valor exacto del contrato de estados (p. ej. `transcript_committed_cleanup_pending`) */
  status: string;
}

const TONE_CLASSES: Record<StatusTone, string> = {
  muted: "border-pure-black/40 bg-surface text-lavender/60",
  info: "border-lavender/60 bg-surface text-lavender",
  active: "border-mint bg-surface text-mint",
  success: "border-mint bg-mint text-on-accent",
  warning: "border-mint bg-surface text-mint shadow-block-sm",
  error: "border-mint bg-mint/20 text-mint underline decoration-mint",
};

const DOT_CLASSES: Record<StatusTone, string> = {
  muted: "bg-lavender/40",
  info: "bg-lavender",
  active: "bg-mint animate-pulse",
  success: "bg-mint",
  warning: "bg-mint",
  error: "bg-mint",
};

export function StatusBadge({ status, className = "", ...rest }: StatusBadgeProps) {
  const tone = statusTone(status);
  return (
    <span
      className={`lowercase font-pixel inline-flex items-center gap-2 border-2 px-2 py-0.5 leading-none ${TONE_CLASSES[tone]} ${className}`}
      {...rest}
    >
      <span aria-hidden="true" className={`inline-block h-2 w-2 ${DOT_CLASSES[tone]}`} />
      {status}
    </span>
  );
}
